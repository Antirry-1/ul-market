"""Полная пересборка витрины с проверкой до публикации.

Инкрементальное обновление здесь не подходит: mom_pct и yoy_pct ссылаются на соседние месяцы,
а одна сделка входит в несколько месяцев, поэтому окно загрузки не равно месяцу витрины.
"""

from __future__ import annotations

import logging

import psycopg

from etl.audits import run_audits
from etl.config import Settings
from etl.db import connect, run_sql_file, set_params
from etl.log import log_event
from etl.runs import locked_run

logger = logging.getLogger(__name__)

MART_TABLES = ("mart.mart_route_card", "mart.mart_market_summary")


def build_marts(settings: Settings) -> None:
    with locked_run(settings.dwh_dsn, "build-marts") as run:
        with connect(settings.dwh_dsn) as conn:
            set_params(conn, as_of=settings.as_of)
            conn.execute("set local work_mem = '256MB'")
            run_sql_file(conn, "mart/route_card.sql")
            run.details["audits"] = run_audits(conn, "mart/audits.sql")
            run_sql_file(conn, "mart/market_summary.sql")
            run.details["audits"] |= run_audits(conn, "mart/market_summary_audits.sql")
            run_sql_file(conn, "mart/publish.sql")
            run.rows_loaded = conn.execute("select count(*) from stg_route_card").fetchone()[0]
            # rows_loaded относится к карточке маршрута, сводка рынка публикуется в той же транзакции.
            run.details["market_summary_rows"] = conn.execute(
                "select count(*) from stg_market_summary"
            ).fetchone()[0]
            run.details["reliability"] = dict(
                conn.execute(
                    "select reliability_level, count(*) from stg_route_card group by 1 order by 1"
                ).fetchall()
            )

    vacuum_marts(settings)


def vacuum_marts(settings: Settings) -> None:
    """Убирает прошлую версию витрины, оставленную DELETE + INSERT, и обновляет статистику.

    Идёт после шага: витрина к этому моменту опубликована, и сбой уборки не делает сборку
    неудачной. Он только попадает в лог, а мёртвые строки позже уберёт autovacuum.
    VACUUM не работает внутри транзакции, отсюда autocommit.
    """
    try:
        with connect(settings.dwh_dsn, autocommit=True) as conn:
            for table in MART_TABLES:
                conn.execute(f"vacuum (analyze) {table}")
    except psycopg.Error as error:
        log_event(logger, "mart_vacuum_failed", level=logging.WARNING, error=str(error))
