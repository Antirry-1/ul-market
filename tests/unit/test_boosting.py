import numpy as np
import pytest

from ml.baseline import LogRidge
from ml.boosting import BoostingParams, fit_boosting, predict_boosting
from tests.unit.frames import deals


def test_level_offset_is_applied_once():
    frame = deals()
    level = LogRidge().fit(frame)
    params = BoostingParams(depth=3, log_target=True, iterations=30, level_offset=True)
    model = fit_boosting(frame, ["distance"], params, seed=42, threads=1, level=level)
    predicted = predict_boosting(model, frame, ["distance"], params.log_target, level)
    assert np.abs(predicted / frame["price_rub"] - 1).mean() < 0.05


def test_level_offset_requires_log_target():
    with pytest.raises(ValueError):
        BoostingParams(depth=3, log_target=False, level_offset=True)
