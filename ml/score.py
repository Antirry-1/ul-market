"""Пакетный скоринг отложенного периода и будущих заявок, оценка на test."""

from __future__ import annotations

import io
import json
import logging

import joblib
import numpy as np
import pandas as pd
import psycopg
from catboost import CatBoostRegressor

from etl.config import Settings
from etl.db import connect, pipeline_lock
from etl.log import log_event, setup_logging
from etl.runs import tracked_run
from ml import storage
from ml.boosting import predict_boosting
from ml.features import TARGET, read_features
from ml.metrics import sliced_metrics
from ml.split import SplitBounds, assign_split

logger = logging.getLogger(__name__)

PREDICTION_COLUMNS = (
    "price_id", "loading_date", "loading_city_gcid", "unloading_city_gcid", "body_group",
    "actual_price_rub", "predicted_price_rub", "abs_error_rub", "ape_pct",
    "model_name", "split", "outlier_mark",
)


def build_predictions(frame: pd.DataFrame, predicted: np.ndarray, model_name: str) -> pd.DataFrame:
    is_test = frame["split"] == "test"
    actual = frame[TARGET].where(is_test)
    abs_error = (actual - predicted).abs()
    return pd.DataFrame({
        "price_id": frame["price_id"],
        "loading_date": frame["loading_date"],
        "loading_city_gcid": frame["loading_city_gcid"],
        "unloading_city_gcid": frame["unloading_city_gcid"],
        "body_group": frame["primary_body_group"],
        # Для будущих заявок цена в источнике есть, но фактом она не считается.
        "actual_price_rub": actual.round(2),
        "predicted_price_rub": np.round(predicted, 2),
        "abs_error_rub": abs_error.round(2),
        "ape_pct": (abs_error / actual * 100).round(4),
        "model_name": model_name,
        "split": frame["split"],
        "outlier_mark": frame["outlier_mark"],
    })


def write_predictions(conn: psycopg.Connection, predictions: pd.DataFrame, model_name: str) -> None:
    buffer = io.StringIO()
    predictions[list(PREDICTION_COLUMNS)].to_csv(buffer, index=False, header=False)
    conn.execute("delete from mart.ml_price_predictions where model_name = %s", (model_name,))
    columns = ", ".join(PREDICTION_COLUMNS)
    with conn.cursor().copy(
        f"copy mart.ml_price_predictions ({columns}) from stdin with (format csv)"
    ) as copy:
        copy.write(buffer.getvalue())


def score(settings: Settings) -> None:
    bounds = SplitBounds.from_as_of(settings.as_of)

    with pipeline_lock(settings.dwh_dsn), tracked_run(settings.dwh_dsn, "ml-score") as run:
        # Под блокировкой: обучение не подменит файлы, пока они читаются.
        release = storage.current_release(settings.model_dir)
        meta = json.loads((release / storage.META_FILE).read_text(encoding="utf-8"))
        features = meta["features"]
        log_target = meta["params"]["log_target"]
        booster = CatBoostRegressor()
        booster.load_model(str(release / storage.CATBOOST_FILE))
        ridge = joblib.load(release / storage.RIDGE_FILE)
        baseline = joblib.load(release / storage.BASELINE_FILE)

        with connect(settings.dwh_dsn) as conn:
            frame = read_features(conn, bounds.test_start)
        frame["split"] = assign_split(frame["loading_date"], bounds)
        frame = frame.reset_index(drop=True)

        level = ridge if meta["params"]["level_offset"] else None
        predicted = predict_boosting(booster, frame, features, log_target, level)
        predictions = build_predictions(frame, predicted, storage.MAIN_MODEL)

        test_mask = (frame["split"] == "test").to_numpy()
        test = frame[test_mask]
        metrics = {
            storage.MAIN_MODEL: sliced_metrics(test, predicted[test_mask]),
            storage.RIDGE_MODEL: sliced_metrics(test, ridge.predict(test)),
            storage.BASELINE_MODEL: sliced_metrics(test, baseline.predict(test)),
        }

        with connect(settings.dwh_dsn) as conn:
            write_predictions(conn, predictions, storage.MAIN_MODEL)
            for name, values in metrics.items():
                storage.write_metrics(conn, name, "test", values)

        for name, values in metrics.items():
            log_event(logger, "test_metrics", model=name, slice="no_outliers", **values["no_outliers"])
            log_event(logger, "test_metrics", model=name, slice="all_rows", **values["all_rows"])

        counts = predictions["split"].value_counts().to_dict()
        run.rows_read = len(frame)
        run.rows_loaded = len(predictions)
        run.details["predictions"] = counts


def main() -> None:
    setup_logging()
    score(Settings.from_env())


if __name__ == "__main__":
    main()
