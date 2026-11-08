"""Обучение и предсказание CatBoost."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool

from ml.baseline import LogRidge
from ml.features import CATEGORICAL_FEATURES, TARGET


@dataclass(frozen=True)
class BoostingParams:
    depth: int
    log_target: bool
    learning_rate: float = 0.05
    iterations: int = 3000
    # Деревья учатся на остатках линейной модели: уровень цены им выучивать не нужно.
    level_offset: bool = False

    def __post_init__(self) -> None:
        # Уровень задан в логарифме цены. С меткой в рублях CatBoost прибавил бы логарифм к рублям
        # и обучился бы без ошибки, но на бессмысленной сумме.
        if self.level_offset and not self.log_target:
            raise ValueError("level_offset работает только вместе с log_target")

    def as_dict(self) -> dict:
        return asdict(self)


def _pool(
    frame: pd.DataFrame,
    features: list[str],
    log_target: bool,
    level: LogRidge | None = None,
    with_label: bool = True,
) -> Pool:
    label = None
    if with_label:
        label = np.log(frame[TARGET]) if log_target else frame[TARGET]
    categorical = [name for name in features if name in CATEGORICAL_FEATURES]
    baseline = level.predict_log(frame) if level is not None else None
    return Pool(frame[features], label=label, cat_features=categorical, baseline=baseline)


def fit_boosting(
    train: pd.DataFrame,
    features: list[str],
    params: BoostingParams,
    *,
    seed: int,
    threads: int,
    validation: pd.DataFrame | None = None,
    level: LogRidge | None = None,
) -> CatBoostRegressor:
    model = CatBoostRegressor(
        iterations=params.iterations,
        depth=params.depth,
        learning_rate=params.learning_rate,
        loss_function="RMSE",
        random_seed=seed,
        thread_count=threads,
        allow_writing_files=False,
        verbose=False,
    )
    if validation is None:
        model.fit(_pool(train, features, params.log_target, level))
    else:
        model.fit(
            _pool(train, features, params.log_target, level),
            eval_set=_pool(validation, features, params.log_target, level),
            early_stopping_rounds=100,
            use_best_model=True,
        )
    return model


def predict_boosting(
    model: CatBoostRegressor,
    frame: pd.DataFrame,
    features: list[str],
    log_target: bool,
    level: LogRidge | None = None,
) -> np.ndarray:
    # CatBoost сам прибавляет baseline из Pool к предсказанию.
    predicted = model.predict(_pool(frame, features, log_target, level, with_label=False))
    return np.exp(predicted) if log_target else np.maximum(predicted, 1.0)
