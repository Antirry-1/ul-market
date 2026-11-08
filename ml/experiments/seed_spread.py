"""Насколько качество зависит от seed: основная модель против CatBoost на ln(цены) без Ridge.

Для каждого seed оба варианта проходят процедуру ml.train: ранняя остановка на хвосте train
(MAPE на val), затем дообучение на всём train с найденным числом итераций (MAPE на test без
outlier_mark, сезон test не позже последней даты train, как в ml.score).

    docker compose run --rm pipeline python -m ml.experiments.seed_spread [число seed, по умолчанию 10]
"""

from __future__ import annotations

import sys
from dataclasses import replace

import numpy as np

from etl.config import Settings
from etl.db import connect
from ml.baseline import LogRidge
from ml.boosting import BoostingParams, fit_boosting, predict_boosting
from ml.experiments.common import main_params
from ml.features import FEATURES, TARGET, read_features
from ml.metrics import regression_metrics
from ml.split import SplitBounds
from ml.train import split_train_validation, without_outliers

DEFAULT_SEEDS = 10


def main() -> None:
    seeds = range(int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SEEDS)
    settings = Settings.from_env()
    bounds = SplitBounds.from_as_of(settings.as_of)
    features = list(FEATURES)
    # Число итераций подбирается заново для каждого seed, поэтому берётся только устройство модели.
    variants = {
        "Ridge + CatBoost": replace(main_params(settings), iterations=BoostingParams.iterations),
        "CatBoost на ln, depth=6": BoostingParams(depth=6, log_target=True),
    }
    with connect(settings.dwh_dsn) as conn:
        train = without_outliers(read_features(conn, bounds.train_start, bounds.test_start))
        test = without_outliers(read_features(conn, bounds.test_start, bounds.history_end,
                                              season_through=bounds.train_through))
    fit_part, validation = split_train_validation(train, bounds)
    levels = {"fit": LogRidge().fit(fit_part), "train": LogRidge().fit(train)}

    results: dict[str, list[tuple[float, float]]] = {name: [] for name in variants}
    for seed in seeds:
        for name, params in variants.items():
            fit_level = levels["fit"] if params.level_offset else None
            train_level = levels["train"] if params.level_offset else None
            probe = fit_boosting(fit_part, features, params, seed=seed, threads=settings.ml_threads,
                                 validation=validation, level=fit_level)
            val_pred = predict_boosting(probe, validation, features, params.log_target, fit_level)
            iterations = int(probe.get_best_iteration()) + 1
            model = fit_boosting(train, features, replace(params, iterations=iterations), seed=seed,
                                 threads=settings.ml_threads, level=train_level)
            test_pred = predict_boosting(model, test, features, params.log_target, train_level)
            val_mape = regression_metrics(validation[TARGET], val_pred)["mape_pct"]
            test_mape = regression_metrics(test[TARGET], test_pred)["mape_pct"]
            results[name].append((val_mape, test_mape))
            print(f"seed {seed}, {name}: итераций {iterations}, MAPE val {val_mape:.2f} %, test {test_mape:.2f} %",
                  flush=True)

    print(f"\nРазброс MAPE по {len(seeds)} seed, п. п.: стандартное отклонение (размах)")
    for name, values in results.items():
        val, test_ = np.array(values).T
        print(f"{name}: val {val.std(ddof=1):.2f} ({np.ptp(val):.2f}), "
              f"test {test_.std(ddof=1):.2f} ({np.ptp(test_):.2f})")


if __name__ == "__main__":
    main()
