"""Обучение на всей истории до test вместо трёх месяцев, с линейным трендом в Ridge и без него.

Процедура та же, что в ml.train, с устройством и learning rate основной модели: ранняя
остановка на строках с val_start, затем дообучение на всей истории с найденным числом
итераций. Сезон не подменяется: в истории есть прошлые октябри, и дата test не выходит за
пределы обучения. Метрики на test без outlier_mark; для сравнения печатается основная модель
из текущей версии, со своей подстановкой сезона, как в ml.score.

    docker compose run --rm pipeline python -m ml.experiments.full_history_trend
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from etl.config import Settings
from etl.db import connect
from ml import storage
from ml.baseline import LogRidge
from ml.boosting import BoostingParams, fit_boosting, predict_boosting
from ml.experiments.common import describe, main_params
from ml.features import FEATURES, LEVEL_NUMERIC_FEATURES, TARGET, read_features
from ml.score import MainModel
from ml.split import SplitBounds
from ml.train import without_outliers

HISTORY_START = pd.Timestamp("2000-01-01").date()
TREND_ORIGIN = pd.Timestamp("2026-01-01")


def with_trend(frame: pd.DataFrame) -> pd.DataFrame:
    days = (pd.to_datetime(frame["loading_date"]) - TREND_ORIGIN).dt.days
    return frame.assign(trend_years=days / 365.25)


def main() -> None:
    settings = Settings.from_env()
    bounds = SplitBounds.from_as_of(settings.as_of)
    features = list(FEATURES)
    params = replace(main_params(settings), iterations=BoostingParams.iterations)
    with connect(settings.dwh_dsn) as conn:
        history = with_trend(without_outliers(read_features(conn, HISTORY_START, bounds.test_start)))
        test = with_trend(without_outliers(read_features(conn, bounds.test_start, bounds.history_end)))
        test_clipped = without_outliers(read_features(conn, bounds.test_start, bounds.history_end,
                                                      season_through=bounds.train_through))
    print(f"история: {len(history)} строк с {history['loading_date'].min()}, test: {len(test)} строк")

    dates = pd.to_datetime(history["loading_date"])
    fit_part = history[dates < pd.Timestamp(bounds.val_start)]
    validation = history[dates >= pd.Timestamp(bounds.val_start)]
    actual = test[TARGET].to_numpy()

    for name, numeric in (("с трендом", (*LEVEL_NUMERIC_FEATURES, "trend_years")),
                          ("без тренда", LEVEL_NUMERIC_FEATURES)):
        probe_level = LogRidge(numeric_features=numeric).fit(fit_part)
        probe = fit_boosting(fit_part, features, params, seed=settings.ml_seed, threads=settings.ml_threads,
                             validation=validation, level=probe_level)
        iterations = int(probe.get_best_iteration()) + 1
        ridge = LogRidge(numeric_features=numeric).fit(history)
        booster = fit_boosting(history, features, replace(params, iterations=iterations),
                               seed=settings.ml_seed, threads=settings.ml_threads, level=ridge)
        print(f"\nвся история {name}, итераций {iterations}")
        print(f"  Ridge + CatBoost: {describe(actual, predict_boosting(booster, test, features, params.log_target, ridge))}")
        print(f"  только Ridge:     {describe(actual, ridge.predict(test))}")

    release = storage.current_release(settings.model_dir)
    meta = storage.read_meta(release)
    main = MainModel.load(release, storage.CATBOOST_FILE, storage.RIDGE_FILE)
    print(f"\nосновная модель:    {describe(test_clipped[TARGET].to_numpy(), main.predict(test_clipped, meta))}")


if __name__ == "__main__":
    main()
