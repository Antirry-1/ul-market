import numpy as np
import pandas as pd
import pytest

from ml.metrics import EmptySample, regression_metrics, sliced_metrics


def test_values_on_known_errors():
    metrics = regression_metrics(np.array([100.0, 200.0]), np.array([110.0, 180.0]))
    assert metrics["n_rows"] == 2
    assert metrics["mae_rub"] == pytest.approx(15)
    assert metrics["rmse_rub"] == pytest.approx(np.sqrt(250))
    assert metrics["mape_pct"] == pytest.approx(10)
    assert metrics["mdape_pct"] == pytest.approx(10)
    assert metrics["wape_pct"] == pytest.approx(10)
    assert metrics["r2"] == pytest.approx(1 - 500 / 5000)


def test_r2_undefined_for_constant_target():
    assert regression_metrics(np.array([5.0, 5.0]), np.array([4.0, 6.0]))["r2"] is None


def test_empty_sample_is_an_error_not_nan():
    with pytest.raises(EmptySample):
        regression_metrics(np.array([]), np.array([]))


def test_outliers_counted_only_in_all_rows():
    frame = pd.DataFrame({
        "price_rub": [100.0, 200.0, 900.0],
        "outlier_mark": [False, False, True],
        "distance": [100.0, 700.0, 100.0],
        "primary_body_group": ["Тент", "Борт", "Тент"],
        "weight_category": ["FTL", "FTL", "FTL"],
    })
    result = sliced_metrics(frame, np.array([100.0, 200.0, 100.0]))
    assert result["no_outliers"]["n_rows"] == 2
    assert result["no_outliers"]["mae_rub"] == 0
    assert result["all_rows"]["n_rows"] == 3
    assert result["body_group=Тент"]["n_rows"] == 1
    assert result["distance=500-1000"]["n_rows"] == 1
