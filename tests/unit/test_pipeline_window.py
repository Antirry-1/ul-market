import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dags"))

from pipeline_window import load_arguments, retrain_requested  # noqa: E402

START = datetime(2026, 10, 25, tzinfo=timezone.utc)
END = datetime(2026, 10, 26, tzinfo=timezone.utc)


def test_scheduled_run_extends_data_interval():
    assert load_arguments({}, START, END) == "--start-date 2026-09-20 --end-date 2027-02-23"


def scheduled_window(start: datetime, end: datetime) -> tuple[date, date]:
    match = re.fullmatch(r"--start-date (\d{4}-\d{2}-\d{2}) --end-date (\d{4}-\d{2}-\d{2})",
                         load_arguments({}, start, end))
    assert match, "в bash уходят только даты"
    return date.fromisoformat(match[1]), date.fromisoformat(match[2])


@pytest.mark.parametrize("loading_date", [
    date(2027, 2, 13),                         # самая дальняя заявка в источнике: as_of + 110 дней
    date(2026, 10, 25),                        # погрузка в день интервала
    date(2026, 10, 25) - timedelta(days=30),   # сделка, ещё влияющая на свежесть витрины
])
def test_scheduled_window_covers_requests_created_in_interval(loading_date):
    start, end = scheduled_window(START, END)
    assert start <= loading_date < end


def test_scheduled_window_follows_interval_not_as_of():
    start = datetime(2026, 11, 7, tzinfo=timezone.utc)
    assert scheduled_window(start, start + timedelta(days=1)) == (date(2026, 10, 3), date(2027, 3, 8))


def test_conf_window_overrides_data_interval():
    conf = {"start_date": "2026-10-01", "end_date": "2026-11-01"}
    assert load_arguments(conf, START, END) == "--start-date 2026-10-01 --end-date 2026-11-01"


def test_full_load():
    assert load_arguments({"full": True}, START, END) == "--full"


@pytest.mark.parametrize("conf", [
    {"start_date": "2026-10-01"},
    {"end_date": "2026-11-01"},
    {"full": True, "start_date": "2026-10-01", "end_date": "2026-11-01"},
])
def test_incomplete_or_conflicting_window_is_rejected(conf):
    with pytest.raises(ValueError):
        load_arguments(conf, START, END)


def test_value_reaching_bash_must_be_a_date():
    conf = {"start_date": "2026-10-01; rm -rf /", "end_date": "2026-11-01"}
    with pytest.raises(ValueError):
        load_arguments(conf, START, END)


@pytest.mark.parametrize(("conf", "expected"), [({}, False), ({"retrain": True}, True)])
def test_retrain_only_on_request(conf, expected):
    assert retrain_requested(conf) is expected
