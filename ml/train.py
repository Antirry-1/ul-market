"""Обучение моделей.

Параметры выбираются на хвосте train, затем модель дообучается на всём train. Эта модель
тестовый период не видит, и метрики test считаются по ней.

Прогноз будущих заявок строит та же модель с теми же параметрами, обученная на всей истории
до as_of. К дате снимка цены test уже известны, и без них прогноз отставал бы от рынка
на три последние недели. Строки test читаются отдельным запросом уже после того, как
модель для метрик обучена, и в неё не попадают.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import joblib
import pandas as pd
from catboost import CatBoostRegressor

from etl.config import Settings
from etl.db import connect
from etl.log import log_event, setup_logging
from etl.runs import locked_run
from ml import storage
from ml.baseline import LogRidge, RatePerKmBaseline
from ml.boosting import BoostingParams, fit_boosting, predict_boosting
from ml.features import FEATURES, TARGET, add_calendar_features, read_features
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
    """Хвост train для выбора параметров и ранней остановки.

    Сезон строк хвоста берётся не позже последней даты, на которой учатся кандидаты, как у test
    в ml/score.py. Иначе параметры подбирались бы на прогнозе с сезоном за пределами обучения,
    а test проверял бы прогноз без него.
    """
    dates = pd.to_datetime(frame["loading_date"])
    is_validation = dates >= pd.Timestamp(bounds.val_start)
    validation = add_calendar_features(frame[is_validation], bounds.fit_through)
    return frame[~is_validation], validation


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


def without_outliers(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[~frame["outlier_mark"]].reset_index(drop=True)


def fit_main_model(
    frame: pd.DataFrame,
    params: BoostingParams,
    settings: Settings,
) -> tuple[LogRidge, CatBoostRegressor]:
    """Ridge уровня и CatBoost с готовыми параметрами. Ridge нужен и сам по себе, как бейзлайн."""
    ridge = LogRidge().fit(frame)
    booster = fit_boosting(frame, list(FEATURES), params, seed=settings.ml_seed,
                           threads=settings.ml_threads, level=ridge if params.level_offset else None)
    return ridge, booster


def train(settings: Settings) -> None:
    bounds = SplitBounds.from_as_of(settings.as_of)

    with locked_run(settings.dwh_dsn, "ml-train") as run:
        with connect(settings.dwh_dsn) as conn:
            frame = read_features(conn, bounds.train_start, bounds.test_start)
        train_frame = without_outliers(frame)
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

        final_params = replace(params, iterations=iterations)
        ridge, booster = fit_main_model(train_frame, final_params, settings)

        with connect(settings.dwh_dsn) as conn:
            recent = read_features(conn, bounds.test_start, bounds.history_end)
        history = pd.concat([train_frame, without_outliers(recent)], ignore_index=True)
        forecast_ridge, forecast_booster = fit_main_model(history, final_params, settings)
        log_event(logger, "forecast_model_trained", history_rows=len(history),
                  history_start=bounds.train_start, history_through=bounds.as_of)

        with storage.new_release(settings.model_dir) as release:
            booster.save_model(str(release / storage.CATBOOST_FILE))
            joblib.dump(ridge, release / storage.RIDGE_FILE)
            joblib.dump(RatePerKmBaseline().fit(train_frame), release / storage.BASELINE_FILE)
            forecast_booster.save_model(str(release / storage.FORECAST_CATBOOST_FILE))
            joblib.dump(forecast_ridge, release / storage.FORECAST_RIDGE_FILE)
            # Версия и запуск обучения: по ним прогноз и метрика находят свои файлы и запись журнала.
            # Границы выборок сверяют скоринг и абляция, см. storage.check_split.
            (release / storage.META_FILE).write_text(
                json.dumps({"release": release.name, "train_run_id": run.run_id,
                            "split": bounds.as_dict(),
                            "forecast_rows": len(history),
                            "params": final_params.as_dict(), "features": list(FEATURES)},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # Ссылка current переключается при выходе из блока, то есть после коммита метрик.
            # Если запись метрик упадёт, останутся прежние модель и метрики, а не новая модель со старыми.
            with connect(settings.dwh_dsn) as conn:
                for name, metrics in ((storage.MAIN_MODEL, boosting_val),
                                      (storage.RIDGE_MODEL, ridge_val),
                                      (storage.BASELINE_MODEL, baseline_val)):
                    storage.write_metrics(conn, name, "val", {"no_outliers": metrics},
                                          model_release=release.name)
                    log_event(logger, "validation_metrics", model=name, **metrics)

        run.rows_read = len(frame) + len(recent)
        run.rows_loaded = len(train_frame)
        run.details.update(params=final_params.as_dict(), selection_metric=SELECTION_METRIC,
                           model_release=release.name, forecast_rows=len(history))


def main() -> None:
    setup_logging()
    train(Settings.from_env())


if __name__ == "__main__":
    main()
