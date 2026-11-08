import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dags"))

from pipeline_window import (  # noqa: E402
    TASK_TIMEOUT,
    clickhouse_available,
    load_arguments,
    retrain_requested,
)

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


def test_mirror_skipped_without_clickhouse_settings():
    assert clickhouse_available({}, ping=lambda url: True) is False


def test_mirror_runs_only_when_clickhouse_answers():
    env = {"CLICKHOUSE_HOST": "clickhouse", "CLICKHOUSE_PORT": "8123"}
    urls = []
    assert clickhouse_available(env, ping=lambda url: urls.append(url) or True) is True
    assert urls == ["http://clickhouse:8123/ping"]
    assert clickhouse_available(env, ping=lambda url: False) is False


def test_unresolvable_clickhouse_host_means_skip():
    env = {"CLICKHOUSE_HOST": "clickhouse.invalid", "CLICKHOUSE_PORT": "8123"}
    assert clickhouse_available(env) is False


def test_task_timeout_leaves_room_for_waiting_on_busy_storage():
    # Задача сначала может ждать занятое хранилище, и только потом работает сама.
    from etl.db import DEFAULT_PIPELINE_LOCK_TIMEOUT

    assert DEFAULT_PIPELINE_LOCK_TIMEOUT == "15min"
    assert TASK_TIMEOUT >= timedelta(minutes=15) * 3
