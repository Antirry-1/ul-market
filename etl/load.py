"""Загрузка окна дат: источник -> raw -> core и карантин."""

from __future__ import annotations

from datetime import date

import psycopg

from etl.audits import AuditFailed, run_audits
from etl.config import Settings
from etl.db import connect, run_sql_file, set_params
from etl.extract import extract_to_temp
from etl.runs import Run, locked_run

WINDOW_FILES = (
    "transform/raw_references.sql",
    "transform/raw_window.sql",
    "transform/dims.sql",
    "transform/clear_window.sql",
    "transform/quarantine.sql",
    "transform/fact.sql",
    "transform/bridges.sql",
)

SOURCE_AUDITS = "transform/source_audits.sql"

# Пока из окна пропало меньше сделок, загрузка не останавливается при любой доле:
# в маленьком окне удаление пары сделок даёт большую долю, но о сбое источника не говорит.
SHRINK_MIN_DEALS = 100

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


def check_source_snapshot(conn: psycopg.Connection) -> dict[str, int]:
    """Сверяет снимок источника с raw до любых изменений.

    Пустой справочник отправил бы в карантин все сделки окна, а пустое окно стёрло бы его из core.
    Каждая строка при этом проходит правила, поэтому остальные аудиты такой снимок пропускают.
    """
    try:
        return run_audits(conn, SOURCE_AUDITS)
    except AuditFailed as error:
        raise AuditFailed(
            f"{error}. Источник отдал неполный снимок, хранилище не изменено. "
            "Если строки удалены намеренно, повторите загрузку с SOURCE_SHRINK_LIMIT=1",
            error.results,
            error.samples,
        ) from error


def load_window(settings: Settings, start: date, end: date) -> None:
    if start >= end:
        raise ValueError(f"Пустое окно: start={start}, end={end}")

    with locked_run(settings.dwh_dsn, "load", start, end) as run:
        # raw, core и карантин меняются в одной транзакции: при сбое остаётся прошлая версия окна.
        with connect(settings.source_dsn) as source, connect(settings.dwh_dsn) as target:
            counts = extract_to_temp(source, target, start, end)
            set_params(target, batch_id=run.run_id, window_start=start, window_end=end,
                       shrink_limit=settings.source_shrink_limit, shrink_min_deals=SHRINK_MIN_DEALS)
            run.details["audits"] = check_source_snapshot(target)
            for path in WINDOW_FILES:
                run_sql_file(target, path)
            run.details["audits"] |= run_audits(target, "transform/audits.sql")
            run.rows_read = counts["prices"]
            run.details["source_rows"] = counts
            _record_stats(target, run)

        with connect(settings.dwh_dsn, autocommit=True) as conn:
            conn.execute(f"analyze {ANALYZE_TABLES}")
