"""Полная пересборка витрины с проверкой до публикации.

Инкрементальное обновление здесь не подходит: mom_pct и yoy_pct ссылаются на соседние месяцы,
а одна сделка входит в несколько месяцев, поэтому окно загрузки не равно месяцу витрины.
"""

from __future__ import annotations

from etl.audits import run_audits
from etl.config import Settings
from etl.db import connect, pipeline_lock, run_sql_file, set_params
from etl.runs import tracked_run


def build_marts(settings: Settings) -> None:
    with pipeline_lock(settings.dwh_dsn), tracked_run(settings.dwh_dsn, "build-marts") as run:
        with connect(settings.dwh_dsn) as conn:
            set_params(conn, as_of=settings.as_of)
            conn.execute("set local work_mem = '256MB'")
            run_sql_file(conn, "mart/route_card.sql")
            run.details["audits"] = run_audits(conn, "mart/audits.sql")
            run_sql_file(conn, "mart/market_summary.sql")
            run.details["audits"] |= run_audits(conn, "mart/market_summary_audits.sql")
            run_sql_file(conn, "mart/publish.sql")
            run.rows_loaded = conn.execute("select count(*) from stg_route_card").fetchone()[0]
            run.details["reliability"] = dict(
                conn.execute(
                    "select reliability_level, count(*) from stg_route_card group by 1 order by 1"
                ).fetchall()
            )

        # VACUUM не работает внутри транзакции.
        with connect(settings.dwh_dsn, autocommit=True) as conn:
            conn.execute("vacuum (analyze) mart.mart_route_card")
            conn.execute("vacuum (analyze) mart.mart_market_summary")
