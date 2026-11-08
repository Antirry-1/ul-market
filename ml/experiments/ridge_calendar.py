"""Что даёт календарь в Ridge уровня: медиана прогноза будущих заявок, руб./км, по месяцам.

Варианты обучаются на двух окнах без outlier_mark: train и train + test. Второе окно у модели
будущих заявок в ml.train, первое у модели, по которой считаются метрики test. Параметры и число
итераций берутся из текущей версии, меняется только набор признаков Ridge. Первый вариант
повторяет основную модель: сезон future у него берётся не позже последней даты обучения,
как в ml.score. У вариантов с календарём сезон не подменяется: проверяется, как Ridge
продолжает календарь за пределы обучения.

    docker compose run --rm pipeline python -m ml.experiments.ridge_calendar
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from etl.config import Settings
from etl.db import connect
from ml.baseline import LogRidge
from ml.boosting import fit_boosting, predict_boosting
from ml.experiments.common import main_params
from ml.features import FEATURES, LEVEL_NUMERIC_FEATURES, read_features
from ml.split import SplitBounds
from ml.train import without_outliers

VARIANTS = {
    "без календаря, как основная модель": (),
    "гармоники месяца и дня года": ("month_sin", "month_cos", "day_of_year_sin", "day_of_year_cos"),
    "месяц и день года числом": ("month", "day_of_year"),
}


def main() -> None:
    settings = Settings.from_env()
    bounds = SplitBounds.from_as_of(settings.as_of)
    params = main_params(settings)
    features = list(FEATURES)
    windows = {"train": bounds.test_start, "train + test": bounds.history_end}
    with connect(settings.dwh_dsn) as conn:
        frames = {name: without_outliers(read_features(conn, bounds.train_start, end))
                  for name, end in windows.items()}
        future_raw = read_features(conn, bounds.history_end)
        clipped = {name: read_features(conn, bounds.history_end, season_through=end - timedelta(days=1))
                   for name, end in windows.items()}
    month = pd.to_datetime(future_raw["loading_date"]).dt.to_period("M").to_numpy()
    distance = future_raw["distance"].to_numpy()

    table = {}
    for window, history in frames.items():
        print(f"окно {window}: {len(history)} строк по {history['loading_date'].max()}")
        for name, calendar in VARIANTS.items():
            future = clipped[window] if not calendar else future_raw
            ridge = LogRidge(numeric_features=(*LEVEL_NUMERIC_FEATURES, *calendar)).fit(history)
            level = ridge if params.level_offset else None
            booster = fit_boosting(history, features, params, seed=settings.ml_seed,
                                   threads=settings.ml_threads, level=level)
            boosted = predict_boosting(booster, future, features, params.log_target, level)
            table[(window, name, "Ridge")] = pd.Series(ridge.predict(future) / distance).groupby(month).median()
            table[(window, name, "Ridge + CatBoost")] = pd.Series(boosted / distance).groupby(month).median()

    result = pd.DataFrame(table).round(1)
    result.insert(0, ("заявок", "", ""), pd.Series(month).value_counts().sort_index().to_numpy())
    with pd.option_context("display.width", 200, "display.max_columns", None):
        print(f"Медиана прогноза {len(future_raw)} будущих заявок, руб./км")
        print(result.T.to_string())


if __name__ == "__main__":
    main()
