"""Простые модели для сравнения с основной."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ml.features import CATEGORICAL_FEATURES, LEVEL_NUMERIC_FEATURES, TARGET

GROUP_KEYS = ["origin_region", "destination_region", "primary_body_group"]
MIN_GROUP_ROWS = 5
RIDGE_ALPHA = 10.0


class RatePerKmBaseline:
    """Медиана руб./км train по паре регионов и кузову, умноженная на дистанцию."""

    def fit(self, frame: pd.DataFrame) -> "RatePerKmBaseline":
        rate = frame[TARGET] / frame["distance"]
        self.global_rate_ = float(rate.median())
        grouped = rate.groupby([frame[key] for key in GROUP_KEYS])
        medians = grouped.median()
        self.group_rates_ = medians[grouped.size() >= MIN_GROUP_ROWS].rename("rate").reset_index()
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        rates = frame[GROUP_KEYS].merge(self.group_rates_, on=GROUP_KEYS, how="left")["rate"]
        return rates.fillna(self.global_rate_).to_numpy() * frame["distance"].to_numpy()


class LogRidge:
    """Ridge на ln(цены): факторы цены перемножаются, поэтому в логарифме связь почти линейна.

    Импутация, масштаб и кодирование обучаются внутри fit, то есть только на train.
    Модель сравнивается с основной и задаёт для неё уровень цены.
    """

    def __init__(
        self,
        categorical: tuple[str, ...] = CATEGORICAL_FEATURES,
        numeric_features: tuple[str, ...] = LEVEL_NUMERIC_FEATURES,
    ) -> None:
        numeric = Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ])
        columns = ColumnTransformer([
            ("numeric", numeric, list(numeric_features)),
            ("categorical", OneHotEncoder(handle_unknown="ignore"), list(categorical)),
        ])
        self.model_ = Pipeline([("columns", columns), ("model", Ridge(alpha=RIDGE_ALPHA))])

    def fit(self, frame: pd.DataFrame) -> "LogRidge":
        self.model_.fit(frame, np.log(frame[TARGET]))
        return self

    def predict_log(self, frame: pd.DataFrame) -> np.ndarray:
        return self.model_.predict(frame)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.exp(self.predict_log(frame))
