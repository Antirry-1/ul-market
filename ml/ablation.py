"""Абляция признаков на тех же данных и с теми же параметрами, что у основной модели.

Результаты только сравниваются и в выбор модели не возвращаются.
Прогнозы этих вариантов в mart.ml_price_predictions не пишутся.
"""

from __future__ import annotations

import logging

import pandas as pd

from etl.config import Settings
from etl.db import connect
from etl.log import log_event, setup_logging
from etl.runs import locked_run
from ml import storage
from ml.baseline import LogRidge
from ml.boosting import BoostingParams, fit_boosting, predict_boosting
from ml.features import CATEGORICAL_FEATURES, FEATURES, TARGET, read_features
from ml.metrics import sliced_metrics
from ml.split import SplitBounds, assign_split

logger = logging.getLogger(__name__)

NO_PRICE_TYPE_MODEL = "ablation_no_price_type"
LEAK_DEMO_MODEL = "leak_demo_price_per_km"
LEAK_COLUMN = "leaked_price_per_km"


def add_leak(frame: pd.DataFrame) -> pd.DataFrame:
    """Намеренная утечка: удельная ставка текущего рейса содержит сам таргет."""
    return frame.assign(**{LEAK_COLUMN: frame[TARGET] / frame["distance"]})


def run_ablation(settings: Settings) -> None:
    bounds = SplitBounds.from_as_of(settings.as_of)

    with locked_run(settings.dwh_dsn, "ml-ablation") as run:
        release = storage.current_release(settings.model_dir)
        meta = storage.read_meta(release)
        storage.check_split(meta, bounds)
        storage.check_features(meta, FEATURES)
        params = BoostingParams(**meta["params"])
        features = meta["features"]

        # Модель уровня обучается заново: иначе price_type вернулся бы в вариант через неё.
        without_price_type = tuple(name for name in CATEGORICAL_FEATURES if name != "price_type")
        variants = {
            NO_PRICE_TYPE_MODEL: (
                [name for name in features if name != "price_type"], lambda frame: frame, without_price_type,
            ),
            LEAK_DEMO_MODEL: ([*features, LEAK_COLUMN], add_leak, CATEGORICAL_FEATURES),
        }

        with connect(settings.dwh_dsn) as conn:
            frame = read_features(conn, bounds.train_start, bounds.history_end,
                                  season_through=bounds.train_through)
        frame["split"] = assign_split(frame["loading_date"], bounds)
        train = frame[(frame["split"] == "train") & ~frame["outlier_mark"]]
        test = frame[frame["split"] == "test"]

        results = {}
        for name, (variant_features, prepare, categorical) in variants.items():
            level = LogRidge(categorical).fit(train) if params.level_offset else None
            model = fit_boosting(prepare(train), variant_features, params,
                                 seed=settings.ml_seed, threads=settings.ml_threads, level=level)
            predicted = predict_boosting(model, prepare(test), variant_features, params.log_target, level)
            results[name] = sliced_metrics(test, predicted)
            log_event(logger, "ablation_metrics", model=name, **results[name]["no_outliers"])

        with connect(settings.dwh_dsn) as conn:
            for name, metrics in results.items():
                storage.write_metrics(conn, name, "test", metrics, model_release=meta["release"])
        run.rows_read = len(frame)
        run.details["model_release"] = meta["release"]


def main() -> None:
    setup_logging()
    run_ablation(Settings.from_env())


if __name__ == "__main__":
    main()
