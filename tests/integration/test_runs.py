"""Журнал запусков: записи оборванных процессов и итог упавшего шага."""

import logging

import psycopg
import pytest

from etl.audits import AuditFailed
from etl.runs import ABANDONED_ERROR, RUN_LOCK_CLASS

RUNNING_ROW = "insert into ops.pipeline_runs (step, status) values ('load', 'running') returning run_id"


def run_row(query, run_id: int) -> tuple:
    return query("select status, details, finished_at is not null from ops.pipeline_runs where run_id = %s",
                 (run_id,))[0]


def test_abandoned_run_is_closed_by_next_step(load, query):
    ((abandoned_id,),) = query(RUNNING_ROW)
    load()
    status, details, finished = run_row(query, abandoned_id)
    ((next_id,),) = query("select max(run_id) from ops.pipeline_runs")
    assert status == "failed"
    assert finished
    assert details == {"error": ABANDONED_ERROR, "closed_by_run": next_id}
    assert run_row(query, next_id)[0] == "success"


def test_live_run_is_not_closed(load, query, settings):
    # Соединение держит блокировку записи, как это делает tracked_run работающего шага.
    with psycopg.connect(settings.dwh_dsn, autocommit=True) as live:
        live_id = live.execute(RUNNING_ROW).fetchone()[0]
        live.execute("select pg_advisory_lock(%s::int, %s::int)", (RUN_LOCK_CLASS, live_id))
        load()
        assert run_row(query, live_id) == ("running", {}, False)

    load()
    assert run_row(query, live_id)[0] == "failed"


AUDITS = """
-- @audit fact_is_empty error
select price_id from core.fact_price;

-- @audit nothing_wrong warn
select 1 where false;
"""


def test_failed_audit_keeps_violation_counts(source, load, query, monkeypatch, caplog):
    source.add_deal()
    source.add_deal()
    monkeypatch.setattr("etl.audits.read_sql", lambda _path: AUDITS)
    with caplog.at_level(logging.INFO, logger="etl.runs"), pytest.raises(AuditFailed):
        load()

    ((status, details),) = query("select status, details from ops.pipeline_runs")
    assert status == "failed"
    assert details["audits"] == {"fact_is_empty": 2, "nothing_wrong": 0}
    # По образцу сразу видно, какие сделки нарушили проверку.
    assert sorted(details["audit_samples"]["fact_is_empty"]) == [1, 2]
    assert set(details["audit_samples"]) == {"fact_is_empty"}
    assert "fact_is_empty" in details["error"]
    finished = [record for record in caplog.records if record.getMessage() == "step_finished"]
    assert [record.levelname for record in finished] == ["ERROR"]
    assert query("select count(*) from core.fact_price") == [(0,)]
