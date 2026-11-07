"""Общая блокировка хранилища: занятое хранилище даёт понятную ошибку, а не бесконечное ожидание."""

import time

import psycopg
import pytest

from etl.db import PIPELINE_LOCK_KEY, PIPELINE_LOCK_TIMEOUT_ENV, PipelineBusy


@pytest.fixture
def busy_dwh(settings):
    # Второе соединение ведёт себя как чужой прогон, который держит хранилище.
    with psycopg.connect(settings.dwh_dsn, autocommit=True) as other:
        other.execute("select pg_advisory_lock(%s)", (PIPELINE_LOCK_KEY,))
        yield
        other.execute("select pg_advisory_unlock(%s)", (PIPELINE_LOCK_KEY,))


def test_step_fails_fast_when_dwh_is_busy(source, load, query, busy_dwh, monkeypatch):
    source.add_deal()
    monkeypatch.setenv(PIPELINE_LOCK_TIMEOUT_ENV, "300ms")

    started = time.monotonic()
    with pytest.raises(PipelineBusy, match="занято другим прогоном.*300ms"):
        load()

    assert time.monotonic() - started < 10
    assert query("select count(*) from core.fact_price") == [(0,)]
    # Отказ виден в журнале, а не только в логе процесса.
    ((step, status, error),) = query("select step, status, details->>'error' from ops.pipeline_runs")
    assert (step, status) == ("load", "failed")
    assert "занято другим прогоном" in error


def test_step_runs_once_dwh_is_free(source, load, query, monkeypatch):
    source.add_deal()
    monkeypatch.setenv(PIPELINE_LOCK_TIMEOUT_ENV, "300ms")
    load()
    assert query("select count(*) from core.fact_price") == [(1,)]
