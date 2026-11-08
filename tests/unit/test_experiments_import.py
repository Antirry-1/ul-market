import importlib

import pytest

MODULES = ("bootstrap_vs_catboost_log", "full_history_trend", "ridge_calendar", "seed_spread")


@pytest.mark.parametrize("name", MODULES)
def test_experiment_imports(name):
    module = importlib.import_module(f"ml.experiments.{name}")
    assert callable(module.main)
