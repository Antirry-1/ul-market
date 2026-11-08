from datetime import date

import numpy as np
import pandas as pd
import pytest

from ml.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    FORBIDDEN_FEATURES,
    LEVEL_NUMERIC_FEATURES,
    MISSING_CATEGORY,
    NUMERIC_FEATURES,
    TARGET,
    add_calendar_features,
    prepare_features,
)


def calendar(value: str) -> pd.Series:
    return add_calendar_features(pd.DataFrame({"loading_date": [value]})).iloc[0]


def test_no_forbidden_feature_in_model():
    assert not set(FEATURES) & FORBIDDEN_FEATURES
    assert TARGET not in FEATURES


def test_level_model_has_no_forbidden_features():
    assert not set(LEVEL_NUMERIC_FEATURES) & FORBIDDEN_FEATURES
    assert TARGET not in LEVEL_NUMERIC_FEATURES


def test_level_model_has_no_calendar():
    calendar_columns = set(add_calendar_features(pd.DataFrame({"loading_date": ["2026-10-05"]})).columns)
    assert not set(LEVEL_NUMERIC_FEATURES) & (calendar_columns - {"loading_date"})


def test_identifiers_are_not_features():
    assert not {"price_id", "loading_date", "loading_city_gcid", "unloading_city_gcid"} & set(FEATURES)


def test_feature_groups_do_not_overlap():
    assert not set(NUMERIC_FEATURES) & set(CATEGORICAL_FEATURES)
    assert len(FEATURES) == len(set(FEATURES))


def test_price_type_is_a_feature():
    assert "price_type" in CATEGORICAL_FEATURES


def test_calendar_parts():
    row = calendar("2026-10-05")
    assert (row["year"], row["month"], row["day_of_week"], row["day_of_year"]) == (2026, 10, 0, 278)


def test_harmonics_lie_on_unit_circle():
    frame = add_calendar_features(pd.DataFrame({"loading_date": pd.date_range("2026-01-01", periods=400)}))
    for name in ("month", "day_of_week", "day_of_year"):
        assert np.allclose(frame[f"{name}_sin"] ** 2 + frame[f"{name}_cos"] ** 2, 1)


def test_new_year_days_stay_close():
    december, january = calendar("2026-12-31"), calendar("2027-01-01")
    distance = np.hypot(
        december["day_of_year_sin"] - january["day_of_year_sin"],
        december["day_of_year_cos"] - january["day_of_year_cos"],
    )
    # В невисокосном году номер дня идёт 365 -> 1, на круге 365.25 это шаг в 1.25 дня.
    assert distance == pytest.approx(2 * np.sin(np.pi * 1.25 / 365.25))


def test_week_wraps_from_sunday_to_monday():
    sunday, monday = calendar("2026-10-25"), calendar("2026-10-26")
    distance = np.hypot(
        sunday["day_of_week_sin"] - monday["day_of_week_sin"],
        sunday["day_of_week_cos"] - monday["day_of_week_cos"],
    )
    assert distance == pytest.approx(2 * np.sin(np.pi / 7))


def test_missing_categories_get_marker():
    source = {name: [None] for name in CATEGORICAL_FEATURES}
    source.update({name: [1.0] for name in ("price_rub", "distance", "total_weight", "total_volume",
                                            "origin_lat", "origin_lon", "destination_lat", "destination_lon")})
    source.update(loading_date=["2026-10-05"], loading_city_gcid=[None], unloading_city_gcid=[None])
    frame = prepare_features(pd.DataFrame(source))
    assert (frame.loc[0, list(CATEGORICAL_FEATURES)] == MISSING_CATEGORY).all()


def test_city_features_are_gc_id_labels():
    source = {name: ["x"] for name in CATEGORICAL_FEATURES}
    source.update({name: [1.0] for name in ("price_rub", "distance", "total_weight", "total_volume",
                                            "origin_lat", "origin_lon", "destination_lat", "destination_lon")})
    source.update(loading_date=["2026-10-05"], loading_city_gcid=[1001], unloading_city_gcid=[2001],
                  body_type=[56], price_type=[3])
    row = prepare_features(pd.DataFrame(source)).iloc[0]
    assert (row["origin_city_id"], row["destination_city_id"]) == ("1001", "2001")


SEASON_COLUMNS = ["month", "day_of_year", "month_sin", "month_cos", "day_of_year_sin", "day_of_year_cos"]


def test_season_after_training_comes_from_last_training_date():
    frame = add_calendar_features(
        pd.DataFrame({"loading_date": ["2026-10-26", "2026-12-05", "2027-01-23"]}),
        season_through=date(2026, 10, 26),
    )
    assert (frame[SEASON_COLUMNS].nunique() == 1).all()
    assert frame.loc[0, "month"] == 10 and frame.loc[0, "day_of_year"] == 299


def test_season_cut_keeps_year_and_weekday():
    row = add_calendar_features(pd.DataFrame({"loading_date": ["2027-01-23"]}),
                                season_through=date(2026, 10, 26)).iloc[0]
    assert (row["year"], row["day_of_week"]) == (2027, 5)


def test_season_cut_does_not_touch_earlier_dates():
    dates = pd.DataFrame({"loading_date": pd.date_range("2026-07-26", "2026-10-26")})
    pd.testing.assert_frame_equal(add_calendar_features(dates, season_through=date(2026, 10, 26)),
                                  add_calendar_features(dates))
