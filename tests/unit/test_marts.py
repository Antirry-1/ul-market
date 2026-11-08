import logging
from argparse import Namespace

import psycopg
import pytest

from etl import marts


class Connection:
    def __init__(self, executed: list[str], fail_on: str | None = None) -> None:
        self.executed = executed
        self.fail_on = fail_on

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query: str) -> None:
        if self.fail_on and self.fail_on in query:
            raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
        self.executed.append(query)


def test_vacuum_covers_both_marts(monkeypatch):
    executed = []
    monkeypatch.setattr(marts, "connect", lambda dsn, autocommit: Connection(executed))
    marts.vacuum_marts(Namespace(dwh_dsn="dwh"))
    assert executed == [f"vacuum (analyze) {table}" for table in marts.MART_TABLES]


def test_vacuum_failure_is_logged_not_raised(monkeypatch, caplog):
    # Витрина уже опубликована: сбой уборки не должен пометить сборку как неудачную.
    monkeypatch.setattr(marts, "connect", lambda dsn, autocommit: Connection([], fail_on="vacuum"))
    with caplog.at_level(logging.WARNING, logger=marts.logger.name):
        marts.vacuum_marts(Namespace(dwh_dsn="dwh"))
    assert [record.getMessage() for record in caplog.records] == ["mart_vacuum_failed"]


def test_vacuum_does_not_hide_programming_errors(monkeypatch):
    def broken_connect(dsn, autocommit):
        raise TypeError("bad argument")

    monkeypatch.setattr(marts, "connect", broken_connect)
    with pytest.raises(TypeError):
        marts.vacuum_marts(Namespace(dwh_dsn="dwh"))
