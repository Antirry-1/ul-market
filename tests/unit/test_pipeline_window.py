import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dags"))

from pipeline_window import load_arguments, retrain_requested  # noqa: E402

START = datetime(2026, 10, 25, tzinfo=timezone.utc)
END = datetime(2026, 10, 26, tzinfo=timezone.utc)


def test_scheduled_run_uses_data_interval():
    assert load_arguments({}, START, END) == "--start-date 2026-10-25 --end-date 2026-10-26"


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
