"""Парный бутстрэп: основная модель (Ridge + CatBoost) против CatBoost на ln(цены), depth=6.

Кандидат проходит ту же процедуру, что основная модель: ранняя остановка на хвосте train,
затем дообучение на всём train с найденным числом итераций. Сравниваются строки test без
outlier_mark; прогноз основной модели берётся из mart.ml_price_predictions.

    docker compose run --rm pipeline python -m ml.experiments.bootstrap_vs_catboost_log
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from etl.config import Settings
from etl.db import connect
from ml import storage
from ml.boosting import BoostingParams, fit_boosting, predict_boosting
from ml.features import FEATURES, TARGET, read_features
from ml.split import SplitBounds
from ml.train import split_train_validation, without_outliers

RESAMPLES = 2000
SEED = 0
CANDIDATE = BoostingParams(depth=6, log_target=True)


def wape(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.abs(actual - predicted).sum() / actual.sum() * 100)


def mae(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.abs(actual - predicted).mean())


def main() -> None:
    settings = Settings.from_env()
    bounds = SplitBounds.from_as_of(settings.as_of)
    features = list(FEATURES)
    with connect(settings.dwh_dsn) as conn:
        train = without_outliers(read_features(conn, bounds.train_start, bounds.test_start))
        test = read_features(conn, bounds.test_start, bounds.history_end, season_through=bounds.train_through)
        main_rows = dict(conn.execute(
            "select price_id, predicted_price_rub::float8 from mart.ml_price_predictions "
            "where split = 'test' and model_name = %s",
            (storage.MAIN_MODEL,),
        ).fetchall())
    test = without_outliers(test)

    fit_part, validation = split_train_validation(train, bounds)
    probe = fit_boosting(fit_part, features, CANDIDATE, seed=settings.ml_seed,
                         threads=settings.ml_threads, validation=validation)
    iterations = int(probe.get_best_iteration()) + 1
    candidate = fit_boosting(train, features, replace(CANDIDATE, iterations=iterations),
                             seed=settings.ml_seed, threads=settings.ml_threads)

    actual = test[TARGET].to_numpy()
    main_pred = test["price_id"].map(main_rows).to_numpy(dtype="float64")
    if np.isnan(main_pred).any():
        raise RuntimeError("Нет прогноза основной модели для части строк test: запустите python -m ml.score")
    cand_pred = predict_boosting(candidate, test, features, CANDIDATE.log_target)

    rng = np.random.default_rng(SEED)
    d_wape, d_mae = [], []
    for _ in range(RESAMPLES):
        rows = rng.integers(0, len(actual), len(actual))
        d_wape.append(wape(actual[rows], cand_pred[rows]) - wape(actual[rows], main_pred[rows]))
        d_mae.append(mae(actual[rows], cand_pred[rows]) - mae(actual[rows], main_pred[rows]))
    lo_w, hi_w = np.percentile(d_wape, [2.5, 97.5])
    lo_m, hi_m = np.percentile(d_mae, [2.5, 97.5])
    print(f"строк test: {len(actual)}, итераций кандидата: {iterations}")
    print(f"WAPE: основная {wape(actual, main_pred):.2f} %, кандидат {wape(actual, cand_pred):.2f} %, "
          f"разница {wape(actual, cand_pred) - wape(actual, main_pred):.2f} п. п. (95 % ДИ {lo_w:.2f}–{hi_w:.2f})")
    print(f"MAE: основная {mae(actual, main_pred):.0f}, кандидат {mae(actual, cand_pred):.0f}, "
          f"разница {mae(actual, cand_pred) - mae(actual, main_pred):.0f} руб. (95 % ДИ {lo_m:.0f}–{hi_m:.0f})")


if __name__ == "__main__":
    main()
