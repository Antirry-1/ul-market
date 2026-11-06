"""Журнал запусков. Пишет отдельным соединением, поэтому переживает откат шага."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date

from psycopg.types.json import Jsonb

from etl.db import connect
from etl.log import log_event

logger = logging.getLogger(__name__)


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
        run_id = conn.execute(
            """
            insert into ops.pipeline_runs (step, window_start, window_end, status)
            values (%s, %s, %s, 'running')
            returning run_id
            """,
            (step, window_start, window_end),
        ).fetchone()[0]
        run = Run(run_id)
        log_event(logger, "step_started", step=step, run_id=run_id,
                  window_start=window_start, window_end=window_end)
        status = "failed"
        try:
            yield run
            status = "success"
        except Exception as error:
            run.details["error"] = str(error)
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
            log_event(logger, "step_finished", step=step, run_id=run_id, status=status,
                      rows_read=run.rows_read, rows_loaded=run.rows_loaded,
                      rows_quarantined=run.rows_quarantined, **run.details)
