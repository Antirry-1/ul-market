from argparse import Namespace
from datetime import date

import pytest

from etl import cli
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


@pytest.mark.parametrize("args", [
    Namespace(command="load", start_date=None, end_date=None, full=True),
    Namespace(command="run-all", start_date=None, end_date=None, full=False),
])
def test_full_window_covers_source_and_loaded_raw(monkeypatch, args):
    # Окно полной загрузки берётся из источника вместе с raw, а не из одного источника.
    settings = Namespace(source_dsn="source", dwh_dsn="dwh")
    calls = []
    monkeypatch.setattr(cli, "full_window", lambda *dsns: calls.append(dsns) or (date(2026, 1, 1), date(2027, 1, 1)))
    assert resolve_window(args, settings) == (date(2026, 1, 1), date(2027, 1, 1))
    assert calls == [("source", "dwh")]
