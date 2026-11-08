"""Хронологическое разбиение по дате погрузки. Границы считаются только от as_of."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

import pandas as pd

TRAIN_MONTHS = 3
TEST_WEEKS = 3
VAL_DAYS = 14


@dataclass(frozen=True)
class SplitBounds:
    train_start: date
    val_start: date
    test_start: date
    as_of: date

    @classmethod
    def from_as_of(cls, as_of: date) -> "SplitBounds":
        test_start = as_of - timedelta(weeks=TEST_WEEKS)
        return cls(
            train_start=(pd.Timestamp(as_of) - pd.DateOffset(months=TRAIN_MONTHS)).date(),
            val_start=test_start - timedelta(days=VAL_DAYS),
            test_start=test_start,
            as_of=as_of,
        )

    @property
    def fit_through(self) -> date:
        """Последняя дата погрузки, на которой учатся кандидаты при выборе параметров."""
        return self.val_start - timedelta(days=1)

    @property
    def train_through(self) -> date:
        """Последняя дата погрузки в обучении модели, по которой считаются метрики test."""
        return self.test_start - timedelta(days=1)

    @property
    def history_end(self) -> date:
        """Конец истории для модели будущих заявок, не включительно: в неё входит и as_of."""
        return self.as_of + timedelta(days=1)

    def as_dict(self) -> dict[str, str]:
        return {name: value.isoformat() for name, value in asdict(self).items()}


def assign_split(loading_date: pd.Series, bounds: SplitBounds) -> pd.Series:
    """Метка выборки на строку: train, test, future; строки до train_start получают NA."""
    dates = pd.to_datetime(loading_date)
    train_start = pd.Timestamp(bounds.train_start)
    test_start = pd.Timestamp(bounds.test_start)
    as_of = pd.Timestamp(bounds.as_of)

    split = pd.Series(pd.NA, index=loading_date.index, dtype="object")
    split[(dates >= train_start) & (dates < test_start)] = "train"
    split[(dates >= test_start) & (dates <= as_of)] = "test"
    split[dates > as_of] = "future"
    return split
