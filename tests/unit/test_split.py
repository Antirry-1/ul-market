from datetime import date

import pandas as pd

from ml.split import SplitBounds, assign_split

BOUNDS = SplitBounds.from_as_of(date(2026, 10, 26))


def label(value: str):
    return assign_split(pd.Series([value]), BOUNDS).iloc[0]


def test_bounds_follow_from_as_of():
    assert BOUNDS.train_start == date(2026, 7, 26)
    assert BOUNDS.val_start == date(2026, 9, 21)
    assert BOUNDS.test_start == date(2026, 10, 5)


def test_edges_of_each_split():
    assert pd.isna(label("2026-07-25"))
    assert label("2026-07-26") == "train"
    assert label("2026-10-04") == "train"
    assert label("2026-10-05") == "test"
    assert label("2026-10-26") == "test"
    assert label("2026-10-27") == "future"


def test_splits_are_chronological():
    dates = pd.Series(pd.date_range("2026-07-26", "2026-11-30"))
    split = assign_split(dates, BOUNDS)
    assert dates[split == "train"].max() < dates[split == "test"].min()
    assert dates[split == "test"].max() < dates[split == "future"].min()
