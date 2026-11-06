"""Загрузка окна дат: источник -> raw -> core и карантин."""

from __future__ import annotations

from datetime import date

import psycopg

from etl.audits import run_audits
from etl.config import Settings
from etl.db import connect, pipeline_lock, run_sql_file, set_params
from etl.extract import extract_to_temp
from etl.runs import Run, tracked_run

WINDOW_FILES = (
    "transform/raw_references.sql",
    "transform/raw_window.sql",
    "transform/dims.sql",
    "transform/clear_window.sql",
    "transform/quarantine.sql",
    "transform/fact.sql",
    "transform/bridges.sql",
)

ANALYZE_TABLES = (
    "raw.prices, raw.price_dates, raw.price_body_types, "
    "core.fact_price, core.fact_price_date, core.bridge_price_body_group, dq.dq_quarantine_prices"
)


def _record_stats(conn: psycopg.Connection, run: Run) -> None:
    run.rows_loaded = conn.execute(
        "select count(*) from core.fact_price f join tmp_prices p on p.id = f.price_id"
    ).fetchone()[0]
    run.rows_quarantined = conn.execute(
        "select count(distinct price_id) from dq.dq_quarantine_prices where _batch_id = %s",
        (run.run_id,),
    ).fetchone()[0]
    run.details["quarantine_reasons"] = dict(
        conn.execute(
            """
            select reason_code, count(*)
            from dq.dq_quarantine_prices
            where _batch_id = %s
            group by reason_code
            order by reason_code
            """,
            (run.run_id,),
        ).fetchall()
    )


def load_window(settings: Settings, start: date, end: date) -> None:
    if start >= end:
        raise ValueError(f"Пустое окно: start={start}, end={end}")

    with pipeline_lock(settings.dwh_dsn), tracked_run(settings.dwh_dsn, "load", start, end) as run:
        # raw, core и карантин меняются в одной транзакции: при сбое остаётся прошлая версия окна.
        with connect(settings.source_dsn) as source, connect(settings.dwh_dsn) as target:
            counts = extract_to_temp(source, target, start, end)
            set_params(target, batch_id=run.run_id, window_start=start, window_end=end)
            for path in WINDOW_FILES:
                run_sql_file(target, path)
            run.details["audits"] = run_audits(target, "transform/audits.sql")
            run.rows_read = counts["prices"]
            run.details["source_rows"] = counts
            _record_stats(target, run)

        with connect(settings.dwh_dsn, autocommit=True) as conn:
            conn.execute(f"analyze {ANALYZE_TABLES}")
