from argparse import Namespace
from datetime import date

import pytest

from etl.cli import resolve_window


def window(start: date, end: date) -> Namespace:
    return Namespace(command="load", start_date=start, end_date=end, full=False)


def test_valid_window_is_returned():
    assert resolve_window(window(date(2026, 10, 1), date(2026, 11, 1)), None) == (
        date(2026, 10, 1), date(2026, 11, 1),
    )


@pytest.mark.parametrize("end", [date(2026, 10, 1), date(2026, 9, 30)])
def test_empty_window_stops_with_message(end):
    with pytest.raises(SystemExit, match="Пустое окно"):
        resolve_window(window(date(2026, 10, 1), end), None)
