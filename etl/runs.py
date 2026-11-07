"""Журнал запусков. Пишет отдельным соединением, поэтому переживает откат шага."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date

from psycopg.types.json import Jsonb

from etl.audits import AuditFailed
from etl.db import connect, pipeline_lock
from etl.log import log_event

logger = logging.getLogger(__name__)

# Первый ключ advisory-блокировки записи журнала, второй ключ равен run_id.
# Блокировку держит соединение журнала до конца шага. Если процесс убит, она снимается
# вместе с соединением, и по ней видно, что запись running уже никто не ведёт.
RUN_LOCK_CLASS = 7_260_102

ABANDONED_ERROR = "процесс прервался до завершения шага"

# Не через pipeline_lock: зеркало ClickHouse пишет в журнал без неё и может идти параллельно.
CLOSE_ABANDONED_RUNS = """
update ops.pipeline_runs r
set status = 'failed',
    finished_at = now(),
    details = r.details || jsonb_build_object('error', %(error)s::text, 'closed_by_run', %(run_id)s::bigint)
where r.status = 'running'
  and r.run_id <> %(run_id)s
  and not exists (
      select 1
      from pg_locks l
      where l.locktype = 'advisory'
        and l.database = (select oid from pg_database where datname = current_database())
        and l.classid::bigint = %(lock_class)s
        and l.objid::bigint = r.run_id
        and l.objsubid = 2
  )
returning r.run_id, r.step
"""


@dataclass
class Run:
    run_id: int
    rows_read: int | None = None
    rows_loaded: int | None = None
    rows_quarantined: int | None = None
    details: dict = field(default_factory=dict)


@contextmanager
def tracked_run(
    dsn: str,
    step: str,
    window_start: date | None = None,
    window_end: date | None = None,
) -> Iterator[Run]:
    with connect(dsn, autocommit=True) as conn:
        # Запись становится видна другим уже вместе с блокировкой, иначе её закрыл бы соседний шаг.
        with conn.transaction():
            run_id = conn.execute(
                """
                insert into ops.pipeline_runs (step, window_start, window_end, status)
                values (%s, %s, %s, 'running')
                returning run_id
                """,
                (step, window_start, window_end),
            ).fetchone()[0]
            conn.execute("select pg_advisory_lock(%s::int, %s::int)", (RUN_LOCK_CLASS, run_id))
        _close_abandoned_runs(conn, run_id)

        run = Run(run_id)
        log_event(logger, "step_started", step=step, run_id=run_id,
                  window_start=window_start, window_end=window_end)
        status = "failed"
        try:
            yield run
            status = "success"
        except Exception as error:
            run.details["error"] = str(error)
            if isinstance(error, AuditFailed):
                run.details["audits"] = {**run.details.get("audits", {}), **error.results}
                run.details["audit_samples"] = error.samples
            raise
        finally:
            conn.execute(
                """
                update ops.pipeline_runs
                set status = %s, rows_read = %s, rows_loaded = %s, rows_quarantined = %s,
                    details = %s, finished_at = now()
                where run_id = %s
                """,
                (status, run.rows_read, run.rows_loaded, run.rows_quarantined,
                 Jsonb(run.details), run_id),
            )
            log_event(logger, "step_finished",
                      level=logging.INFO if status == "success" else logging.ERROR,
                      step=step, run_id=run_id, status=status,
                      rows_read=run.rows_read, rows_loaded=run.rows_loaded,
                      rows_quarantined=run.rows_quarantined, **run.details)


@contextmanager
def locked_run(
    dsn: str,
    step: str,
    window_start: date | None = None,
    window_end: date | None = None,
) -> Iterator[Run]:
    """Шаг, который меняет хранилище: сначала запись в журнале, потом общая блокировка.

    Запись появляется раньше блокировки, поэтому в журнале видно и ожидание занятого хранилища,
    и отказ PipelineBusy: started_at показывает, когда шаг начал ждать.
    """
    with tracked_run(dsn, step, window_start, window_end) as run, pipeline_lock(dsn):
        yield run


def _close_abandoned_runs(conn, run_id: int) -> None:
    closed = conn.execute(
        CLOSE_ABANDONED_RUNS,
        {"error": ABANDONED_ERROR, "run_id": run_id, "lock_class": RUN_LOCK_CLASS},
    ).fetchall()
    for abandoned_id, abandoned_step in closed:
        log_event(logger, "abandoned_run_closed", level=logging.WARNING,
                  run_id=abandoned_id, step=abandoned_step, closed_by_run=run_id)
