"""Набор сделок для проверки моделей: цена равна ставке на км плюс постоянная часть."""

import numpy as np
import pandas as pd

from ml.features import CATEGORICAL_FEATURES, prepare_features


def deals(count: int = 300, loading_date: str = "2026-08-15") -> pd.DataFrame:
    distance = np.linspace(100.0, 3000.0, count)
    frame = pd.DataFrame({name: ["x"] * count for name in CATEGORICAL_FEATURES})
    frame = frame.assign(
        loading_date=loading_date, distance=distance, price_rub=80.0 * distance + 6000.0,
        body_type=56, price_type=1, total_weight=20.0, total_volume=80.0,
        loading_city_gcid=1001, unloading_city_gcid=1003,
        origin_lat=55.0, origin_lon=37.0, destination_lat=56.0, destination_lon=49.0,
    )
    return prepare_features(frame)
