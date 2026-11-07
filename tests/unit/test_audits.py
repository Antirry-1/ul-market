import json
import logging
from datetime import date
from decimal import Decimal

import pytest

from etl.audits import SAMPLE_ROWS, AuditFailed, parse_audits, run_audits, sample_rows
from etl.log import JsonFormatter, log_event

TEXT = """
-- Общий комментарий файла.

-- @audit first error
select 1
from a;

-- Комментарий ко второй проверке.
-- @audit second warn
select 2;
"""


def test_audits_are_split_by_header():
    audits = parse_audits(TEXT)
    assert [(audit.name, audit.severity) for audit in audits] == [("first", "error"), ("second", "warn")]
    assert audits[0].query == "select 1\nfrom a"
    assert audits[1].query == "select 2"


class FakeConnection:
    """Отвечает на запрос аудита заданным числом нарушений: строки с общим числом и номером строки."""

    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts

    def execute(self, query: str):
        count = next(value for marker, value in self.counts.items() if marker in query)
        return FakeCursor([(count, number) for number in range(1, min(count, SAMPLE_ROWS) + 1)])


class FakeCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple]:
        return self.rows


AUDITS = """
-- @audit broken error
select 'broken';

-- @audit noisy warn
select 'noisy';

-- @audit clean error
select 'clean';
"""


@pytest.fixture
def audit_file(monkeypatch):
    monkeypatch.setattr("etl.audits.read_sql", lambda _path: AUDITS)


def test_failed_audit_keeps_counts_of_all_audits(audit_file):
    conn = FakeConnection({"'broken'": 3, "'noisy'": 2, "'clean'": 0})
    with pytest.raises(AuditFailed) as failure:
        run_audits(conn, "any.sql")
    assert failure.value.results == {"broken": 3, "noisy": 2, "clean": 0}
    assert failure.value.samples == {"broken": [1, 2, 3], "noisy": [1, 2]}
    assert "broken" in str(failure.value)
    assert "noisy" not in str(failure.value)


def test_violations_are_logged_by_severity(audit_file, caplog):
    conn = FakeConnection({"'broken'": 0, "'noisy'": 2, "'clean'": 0})
    with caplog.at_level(logging.INFO, logger="etl.audits"):
        assert run_audits(conn, "any.sql") == {"broken": 0, "noisy": 2, "clean": 0}
    assert [(record.levelname, record.fields["audit"]) for record in caplog.records] == [("WARNING", "noisy")]

    conn = FakeConnection({"'broken'": 1, "'noisy'": 0, "'clean'": 0})
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="etl.audits"), pytest.raises(AuditFailed):
        run_audits(conn, "any.sql")
    assert [(record.levelname, record.fields["audit"]) for record in caplog.records] == [("ERROR", "broken")]


def test_json_log_carries_level(caplog):
    with caplog.at_level(logging.INFO, logger="etl.audits"):
        log_event(logging.getLogger("etl.audits"), "audit_violations", level=logging.WARNING,
                  audit="noisy", rows=2)
    payload = json.loads(JsonFormatter().format(caplog.records[0]))
    assert payload["level"] == "WARNING"
    assert payload["event"] == "audit_violations"
    assert payload["rows"] == 2


def test_sample_is_limited_and_logged(audit_file, caplog):
    conn = FakeConnection({"'broken'": 0, "'noisy'": 40, "'clean'": 0})
    with caplog.at_level(logging.INFO, logger="etl.audits"):
        assert run_audits(conn, "any.sql")["noisy"] == 40
    (record,) = caplog.records
    assert record.fields["rows"] == 40
    assert record.fields["sample"] == list(range(1, SAMPLE_ROWS + 1))


def test_sample_rows_drop_total_and_keep_keys_json_ready():
    assert sample_rows([(2, 7), (2, 8)]) == [7, 8]
    assert sample_rows([(1, date(2026, 9, 1), 1001, Decimal("1.50"), None)]) == [
        ["2026-09-01", 1001, "1.50", None],
    ]
