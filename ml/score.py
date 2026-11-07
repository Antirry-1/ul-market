"""Пакетный скоринг отложенного периода и будущих заявок, оценка на test.

Строки test оценивает модель, обученная только на train: по ним считаются метрики.
Будущие заявки оценивает модель, обученная на всей истории до as_of, см. ml/train.py.
Какая модель дала прогноз, видно по trained_through, последней дате её обучающих данных.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psycopg
from catboost import CatBoostRegressor

from etl.config import Settings
from etl.db import connect
from etl.log import log_event, setup_logging
from etl.runs import locked_run
from ml import storage
from ml.baseline import LogRidge
from ml.boosting import predict_boosting
from ml.features import TARGET, read_features
from ml.metrics import sliced_metrics
from ml.split import SplitBounds, assign_split

logger = logging.getLogger(__name__)

PREDICTION_COLUMNS = (
    "price_id", "loading_date", "loading_city_gcid", "unloading_city_gcid", "body_group",
    "actual_price_rub", "predicted_price_rub", "abs_error_rub", "ape_pct",
    "model_name", "split", "outlier_mark", "model_release", "trained_through",
)


def build_predictions(frame: pd.DataFrame, predicted: np.ndarray, model_name: str,
                      model_release: str, bounds: SplitBounds) -> pd.DataFrame:
    is_test = frame["split"] == "test"
    trained_through = frame["split"].map({"test": bounds.train_through, "future": bounds.as_of})
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
        "model_release": model_release,
        "trained_through": trained_through,
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


@dataclass(frozen=True)
class MainModel:
    booster: CatBoostRegressor
    ridge: LogRidge

    @classmethod
    def load(cls, release: Path, booster_file: str, ridge_file: str) -> "MainModel":
        booster = CatBoostRegressor()
        booster.load_model(str(release / booster_file))
        return cls(booster, joblib.load(release / ridge_file))

    def predict(self, frame: pd.DataFrame, meta: dict) -> np.ndarray:
        if frame.empty:
            return np.empty(0)
        level = self.ridge if meta["params"]["level_offset"] else None
        return predict_boosting(self.booster, frame, meta["features"], meta["params"]["log_target"], level)


def score(settings: Settings) -> None:
    bounds = SplitBounds.from_as_of(settings.as_of)

    with locked_run(settings.dwh_dsn, "ml-score") as run:
        # Под блокировкой: обучение не подменит файлы, пока они читаются.
        release = storage.current_release(settings.model_dir)
        meta = storage.read_meta(release)
        storage.check_split(meta, bounds)
        storage.check_files(release)
        evaluated = MainModel.load(release, storage.CATBOOST_FILE, storage.RIDGE_FILE)
        forecast = MainModel.load(release, storage.FORECAST_CATBOOST_FILE, storage.FORECAST_RIDGE_FILE)
        baseline = joblib.load(release / storage.BASELINE_FILE)

        with connect(settings.dwh_dsn) as conn:
            frame = read_features(conn, bounds.test_start)
        frame["split"] = assign_split(frame["loading_date"], bounds)
        frame = frame.reset_index(drop=True)

        test_mask = (frame["split"] == "test").to_numpy()
        test = frame[test_mask]
        predicted = np.empty(len(frame))
        predicted[test_mask] = evaluated.predict(test, meta)
        predicted[~test_mask] = forecast.predict(frame[~test_mask], meta)
        predictions = build_predictions(frame, predicted, storage.MAIN_MODEL, meta["release"], bounds)

        metrics = {
            storage.MAIN_MODEL: sliced_metrics(test, predicted[test_mask]),
            storage.RIDGE_MODEL: sliced_metrics(test, evaluated.ridge.predict(test)),
            storage.BASELINE_MODEL: sliced_metrics(test, baseline.predict(test)),
        }

        with connect(settings.dwh_dsn) as conn:
            write_predictions(conn, predictions, storage.MAIN_MODEL)
            for name, values in metrics.items():
                storage.write_metrics(conn, name, "test", values, model_release=meta["release"])

        for name, values in metrics.items():
            log_event(logger, "test_metrics", model=name, slice="no_outliers", **values["no_outliers"])
            log_event(logger, "test_metrics", model=name, slice="all_rows", **values["all_rows"])

        counts = predictions["split"].value_counts().to_dict()
        run.rows_read = len(frame)
        run.rows_loaded = len(predictions)
        run.details.update(predictions=counts, model_release=meta["release"],
                           train_run_id=meta.get("train_run_id"))


def main() -> None:
    setup_logging()
    score(Settings.from_env())


if __name__ == "__main__":
    main()
