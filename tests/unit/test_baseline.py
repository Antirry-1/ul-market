import numpy as np
import pandas as pd

from ml.baseline import MIN_GROUP_ROWS, LogRidge, RatePerKmBaseline
from tests.unit.frames import deals


def rows(count: int, rate: float, region: str) -> pd.DataFrame:
    return pd.DataFrame({
        "origin_region": [region] * count,
        "destination_region": ["B"] * count,
        "primary_body_group": ["Тент"] * count,
        "distance": [100.0] * count,
        "price_rub": [100.0 * rate] * count,
    })


def test_group_median_times_distance():
    model = RatePerKmBaseline().fit(rows(MIN_GROUP_ROWS, 50.0, "A"))
    assert model.predict(rows(1, 0.0, "A").assign(distance=10.0)) == np.array([500.0])


def test_rare_or_unknown_group_falls_back_to_global_median():
    frame = pd.concat([rows(MIN_GROUP_ROWS * 2, 50.0, "A"), rows(1, 900.0, "RARE")])
    model = RatePerKmBaseline().fit(frame)
    assert model.predict(rows(1, 0.0, "RARE").assign(distance=1.0)) == np.array([50.0])
    assert model.predict(rows(1, 0.0, "NEW").assign(distance=1.0)) == np.array([50.0])


def test_log_ridge_follows_rate_plus_fixed_part():
    frame = deals()
    relative_error = np.abs(LogRidge().fit(frame).predict(frame) / frame["price_rub"] - 1)
    assert relative_error.mean() < 0.05


def test_log_ridge_level_does_not_depend_on_loading_month():
    model = LogRidge().fit(deals())
    assert np.array_equal(model.predict(deals(loading_date="2026-08-15")), model.predict(deals(loading_date="2027-01-15")))
