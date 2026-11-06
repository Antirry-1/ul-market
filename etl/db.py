"""Подключения и выполнение SQL-файлов."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Один ключ на весь пайплайн: два прогона не должны менять хранилище одновременно.
PIPELINE_LOCK_KEY = 7_260_101


def connect(dsn: str, *, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=autocommit)


def read_sql(relative_path: str) -> str:
    return (SQL_DIR / relative_path).read_text(encoding="utf-8")


def run_sql_file(conn: psycopg.Connection, relative_path: str) -> None:
    # Без параметров psycopg отправляет текст простым протоколом, поэтому
    # в файле может быть несколько команд. Значения передаются через set_params.
    conn.execute(read_sql(relative_path))


def set_params(conn: psycopg.Connection, **params: object) -> None:
    """Задаёт app.* на текущую транзакцию, SQL читает их через current_setting."""
    for name, value in params.items():
        conn.execute("select set_config(%s, %s, true)", (f"app.{name}", str(value)))


@contextmanager
def pipeline_lock(dsn: str) -> Iterator[None]:
    with connect(dsn, autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(%s)", (PIPELINE_LOCK_KEY,))
        try:
            yield
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (PIPELINE_LOCK_KEY,))
