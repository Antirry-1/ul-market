"""Обучение моделей.

Этот модуль читает только строки раньше test_start: тестовый период сюда не попадает.
Параметры выбираются на хвосте train, затем модель дообучается на всём train.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import joblib
import pandas as pd

from etl.config import Settings
from etl.db import connect, pipeline_lock
from etl.log import log_event, setup_logging
from etl.runs import tracked_run
from ml import storage
from ml.baseline import LogRidge, RatePerKmBaseline
from ml.boosting import BoostingParams, fit_boosting, predict_boosting
from ml.features import FEATURES, TARGET, read_features
from ml.metrics import regression_metrics
from ml.split import SplitBounds

logger = logging.getLogger(__name__)

CANDIDATES = (
    BoostingParams(depth=6, log_target=False),
    BoostingParams(depth=8, log_target=False),
    BoostingParams(depth=6, log_target=True),
    BoostingParams(depth=8, log_target=True),
    BoostingParams(depth=4, log_target=True, learning_rate=0.03, level_offset=True),
)

# WAPE, а не MAPE: MAPE при выборе поощряет заниженный прогноз на дешёвых рейсах.
SELECTION_METRIC = "wape_pct"


def split_train_validation(frame: pd.DataFrame, bounds: SplitBounds) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.to_datetime(frame["loading_date"])
    is_validation = dates >= pd.Timestamp(bounds.val_start)
    return frame[~is_validation], frame[is_validation]


def select_boosting(
    fit_part: pd.DataFrame,
    validation: pd.DataFrame,
    level: LogRidge,
    settings: Settings,
) -> tuple[BoostingParams, int, dict]:
    features = list(FEATURES)
    results = []
    for params in CANDIDATES:
        candidate_level = level if params.level_offset else None
        model = fit_boosting(fit_part, features, params, seed=settings.ml_seed,
                             threads=settings.ml_threads, validation=validation, level=candidate_level)
        predicted = predict_boosting(model, validation, features, params.log_target, candidate_level)
        metrics = regression_metrics(validation[TARGET], predicted)
        iterations = int(model.get_best_iteration()) + 1
        log_event(logger, "candidate_evaluated", best_iterations=iterations, **params.as_dict(), **metrics)
        results.append((metrics[SELECTION_METRIC], params, iterations, metrics))
    _, best, iterations, metrics = min(results, key=lambda item: item[0])
    return best, iterations, metrics


def train(settings: Settings) -> None:
    bounds = SplitBounds.from_as_of(settings.as_of)

    with pipeline_lock(settings.dwh_dsn), tracked_run(settings.dwh_dsn, "ml-train") as run:
        with connect(settings.dwh_dsn) as conn:
            frame = read_features(conn, bounds.train_start, bounds.test_start)
        train_frame = frame[~frame["outlier_mark"]].reset_index(drop=True)
        fit_part, validation = split_train_validation(train_frame, bounds)
        log_event(logger, "train_data_read", rows_read=len(frame), train_rows=len(train_frame),
                  fit_rows=len(fit_part), validation_rows=len(validation))

        ridge_fit = LogRidge().fit(fit_part)
        params, iterations, boosting_val = select_boosting(fit_part, validation, ridge_fit, settings)
        log_event(logger, "boosting_selected", best_iterations=iterations, **params.as_dict())

        baseline_val = regression_metrics(
            validation[TARGET], RatePerKmBaseline().fit(fit_part).predict(validation)
        )
        ridge_val = regression_metrics(validation[TARGET], ridge_fit.predict(validation))

        features = list(FEATURES)
        final_params = replace(params, iterations=iterations)
        ridge = LogRidge().fit(train_frame)
        booster = fit_boosting(train_frame, features, final_params, seed=settings.ml_seed,
                               threads=settings.ml_threads,
                               level=ridge if final_params.level_offset else None)
        with storage.new_release(settings.model_dir) as release:
            booster.save_model(str(release / storage.CATBOOST_FILE))
            joblib.dump(ridge, release / storage.RIDGE_FILE)
            joblib.dump(RatePerKmBaseline().fit(train_frame), release / storage.BASELINE_FILE)
            (release / storage.META_FILE).write_text(
                json.dumps({"params": final_params.as_dict(), "features": features},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        with connect(settings.dwh_dsn) as conn:
            for name, metrics in ((storage.MAIN_MODEL, boosting_val),
                                  (storage.RIDGE_MODEL, ridge_val),
                                  (storage.BASELINE_MODEL, baseline_val)):
                storage.write_metrics(conn, name, "val", {"no_outliers": metrics})
                log_event(logger, "validation_metrics", model=name, **metrics)

        run.rows_read = len(frame)
        run.rows_loaded = len(train_frame)
        run.details.update(params=final_params.as_dict(), selection_metric=SELECTION_METRIC)


def main() -> None:
    setup_logging()
    train(Settings.from_env())


if __name__ == "__main__":
    main()
