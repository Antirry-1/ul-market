"""Метрики качества, все ошибки в рублях."""

from __future__ import annotations

import numpy as np
import pandas as pd

DISTANCE_BUCKETS = (0.0, 500.0, 1000.0, 2000.0, np.inf)
DISTANCE_LABELS = ("0-500", "500-1000", "1000-2000", "2000+")


def regression_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float | int | None]:
    actual = np.asarray(actual, dtype="float64")
    predicted = np.asarray(predicted, dtype="float64")
    error = actual - predicted
    abs_error = np.abs(error)
    ape = abs_error / actual * 100
    total_variance = float(np.sum((actual - actual.mean()) ** 2))
    return {
        "n_rows": int(actual.size),
        # На одной строке или постоянном факте R2 не определён.
        "r2": 1 - float(np.sum(error**2)) / total_variance if total_variance > 0 else None,
        "mape_pct": float(ape.mean()),
        "rmse_rub": float(np.sqrt(np.mean(error**2))),
        "mae_rub": float(abs_error.mean()),
        "mdape_pct": float(np.median(ape)),
        "wape_pct": float(abs_error.sum() / actual.sum() * 100),
    }


def sliced_metrics(frame: pd.DataFrame, predicted: np.ndarray) -> dict[str, dict]:
    """Метрики на всех строках, без размеченных выбросов и по срезам строк без выбросов."""
    data = frame.assign(_predicted=np.asarray(predicted, dtype="float64"))
    clean = data[~data["outlier_mark"]]
    result = {
        "no_outliers": regression_metrics(clean["price_rub"], clean["_predicted"]),
        "all_rows": regression_metrics(data["price_rub"], data["_predicted"]),
    }
    distance = pd.cut(clean["distance"], DISTANCE_BUCKETS, labels=DISTANCE_LABELS, right=False)
    groups = (
        ("body_group", clean["primary_body_group"]),
        ("distance", distance),
        ("weight_category", clean["weight_category"]),
    )
    for name, keys in groups:
        for key, part in clean.groupby(keys, observed=True):
            result[f"{name}={key}"] = regression_metrics(part["price_rub"], part["_predicted"])
    return result
