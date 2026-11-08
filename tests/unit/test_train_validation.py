from datetime import date

import pandas as pd

from ml.split import SplitBounds
from ml.train import split_train_validation
from tests.unit.frames import deals

BOUNDS = SplitBounds.from_as_of(date(2026, 10, 26))
CALENDAR = ["month", "day_of_year", "month_sin", "month_cos", "day_of_year_sin", "day_of_year_cos"]


def frame_with_dates(*days: str) -> pd.DataFrame:
    return pd.concat([deals(count=1, loading_date=day) for day in days], ignore_index=True)


def test_fit_part_ends_the_day_before_validation():
    assert BOUNDS.fit_through == date(2026, 9, 20)


def test_validation_gets_season_of_last_fit_date():
    # Октябрьская строка хвоста не должна получить сезон, которого кандидаты не видели.
    fit_part, validation = split_train_validation(frame_with_dates("2026-09-20", "2026-10-03"), BOUNDS)
    assert list(fit_part["loading_date"]) == ["2026-09-20"]
    assert list(validation["loading_date"]) == ["2026-10-03"]
    assert validation[CALENDAR].iloc[0].tolist() == fit_part[CALENDAR].iloc[0].tolist()


def test_validation_keeps_real_year_and_weekday():
    _, validation = split_train_validation(frame_with_dates("2026-10-03"), BOUNDS)
    assert validation["year"].iloc[0] == 2026
    assert validation["day_of_week"].iloc[0] == pd.Timestamp("2026-10-03").dayofweek


def test_split_leaves_input_frame_untouched():
    frame = frame_with_dates("2026-09-20", "2026-10-03")
    before = frame.copy()
    split_train_validation(frame, BOUNDS)
    pd.testing.assert_frame_equal(frame, before)
