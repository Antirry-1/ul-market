"""Создание схем и объектов хранилища. Все файлы идемпотентны."""

from __future__ import annotations

import logging

from etl.db import SQL_DIR, connect, pipeline_lock, run_sql_file
from etl.log import log_event

logger = logging.getLogger(__name__)


def ddl_files() -> list[str]:
    files = sorted(path.relative_to(SQL_DIR).as_posix() for path in (SQL_DIR / "ddl").glob("*.sql"))
    views = SQL_DIR / "mart" / "views.sql"
    if views.exists():
        files.append("mart/views.sql")
    return files


def init_dwh(dsn: str) -> None:
    files = ddl_files()
    with pipeline_lock(dsn), connect(dsn) as conn:
        for path in files:
            run_sql_file(conn, path)
    log_event(logger, "dwh_initialized", files=len(files))
