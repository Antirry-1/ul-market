"""Общие части экспериментов: параметры текущей версии модели и форматирование отчёта.

Эксперименты ничего не пишут в хранилище и в каталог моделей, только печатают результат.
Им нужна обученная версия модели (python -m ml.train), а bootstrap_vs_catboost_log ещё и прогнозы
test в mart.ml_price_predictions (python -m ml.score). Запускать их одновременно с ml.train нельзя:
новая версия удаляет прошлый каталог, и эксперимент, который ещё читает из него файлы, упадёт.
"""

from __future__ import annotations

import numpy as np

from etl.config import Settings
from ml import storage
from ml.boosting import BoostingParams
from ml.features import FEATURES
from ml.metrics import regression_metrics
from ml.split import SplitBounds


def main_params(settings: Settings) -> BoostingParams:
    """Параметры основной модели из текущей версии, с подобранным числом итераций."""
    meta = storage.read_meta(storage.current_release(settings.model_dir))
    storage.check_split(meta, SplitBounds.from_as_of(settings.as_of))
    storage.check_features(meta, FEATURES)
    return BoostingParams(**meta["params"])


def median_bias_pct(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Медианное смещение прогноза: минус значит, что модель занижает цену."""
    return float(np.median(np.asarray(predicted) / np.asarray(actual) - 1) * 100)


def describe(actual: np.ndarray, predicted: np.ndarray) -> str:
    metrics = regression_metrics(actual, predicted)
    return (f"R² {metrics['r2']:.4f}, MAPE {metrics['mape_pct']:.2f} %, WAPE {metrics['wape_pct']:.2f} %, "
            f"RMSE {metrics['rmse_rub']:.0f}, MAE {metrics['mae_rub']:.0f}, "
            f"смещение {median_bias_pct(actual, predicted):+.1f} %")
