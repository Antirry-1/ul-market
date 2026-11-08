"""Подключения и выполнение SQL-файлов."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

import psycopg

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Один ключ на весь пайплайн: два прогона не должны менять хранилище одновременно.
PIPELINE_LOCK_KEY = 7_260_101
# Зеркало ClickHouse только читает хранилище и общую блокировку не берёт, но два зеркала
# сразу пишут в одну таблицу staging. По очереди они публикуют версии в порядке своих снимков.
MIRROR_LOCK_KEY = 7_260_103

# Сколько шаг ждёт, пока другой прогон отпустит хранилище. Обычный полный прогон укладывается
# в несколько минут, так что дольше ждать бессмысленно: задача планировщика должна упасть
# с понятной причиной, а не висеть. Формат значения как у lock_timeout в PostgreSQL.
PIPELINE_LOCK_TIMEOUT_ENV = "PIPELINE_LOCK_TIMEOUT"
DEFAULT_PIPELINE_LOCK_TIMEOUT = "15min"


class PipelineBusy(RuntimeError):
    """Хранилище занято другим прогоном дольше, чем разрешено ждать."""


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
def advisory_lock(dsn: str, key: int, holder: str) -> Iterator[None]:
    timeout = os.environ.get(PIPELINE_LOCK_TIMEOUT_ENV) or DEFAULT_PIPELINE_LOCK_TIMEOUT
    with connect(dsn, autocommit=True) as conn:
        # lock_timeout ограничивает и ожидание advisory-блокировки. Соединение служит только
        # для блокировки, поэтому настройка на сессию ничего другого не задевает.
        conn.execute("select set_config('lock_timeout', %s, false)", (timeout,))
        try:
            conn.execute("select pg_advisory_lock(%s)", (key,))
        except psycopg.errors.LockNotAvailable as error:
            raise PipelineBusy(
                f"{holder} занято другим прогоном: блокировка не освободилась за {timeout} "
                f"({PIPELINE_LOCK_TIMEOUT_ENV})"
            ) from error
        try:
            yield
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (key,))


def pipeline_lock(dsn: str) -> AbstractContextManager[None]:
    return advisory_lock(dsn, PIPELINE_LOCK_KEY, "Хранилище")


def mirror_lock(dsn: str) -> AbstractContextManager[None]:
    return advisory_lock(dsn, MIRROR_LOCK_KEY, "Зеркало ClickHouse")
