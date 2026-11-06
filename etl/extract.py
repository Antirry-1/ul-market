"""Перенос данных из источника во временные таблицы хранилища через COPY."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg import sql

from etl.log import log_event

logger = logging.getLogger(__name__)

PRICE_COLUMNS = (
    "id", "created_at", "updated_at", "subscriber_id", "organization_id", "loading_date",
    "loading_city_gcid", "unloading_city_gcid", "distance", "total_weight", "total_volume",
    "body_type", "price", "currency_code", "complex", "price_type", "item_id", "is_fact",
    "nearest_rate_date", "meta_item_id", "end_loading_date", "source", "external_item_uuid",
    "outlier_mark", "weight_category", "volume_category",
)


@dataclass(frozen=True)
class SourceTable:
    name: str
    columns: tuple[str, ...]
    windowed: bool
    join_prices: bool = False


SOURCE_TABLES = (
    SourceTable(
        "cities",
        ("id", "full_name_local", "full_name_gc", "gc_id", "name", "longitude", "latitude",
         "created_at", "updated_at"),
        windowed=False,
    ),
    SourceTable("body_types", ("id", "name", "id_from_1C", "std_id"), windowed=False),
    SourceTable("exchange_rates", ("id", "num_code", "value", "setting_date"), windowed=False),
    SourceTable("prices", PRICE_COLUMNS, windowed=True),
    SourceTable("price_dates", ("id", "price_id", "price_date"), windowed=True, join_prices=True),
    SourceTable("price_body_types", ("id", "price_id", "body_type_id"), windowed=True, join_prices=True),
)


def _select_statement(table: SourceTable) -> sql.Composable:
    columns = sql.SQL(", ").join(sql.Identifier("t", column) for column in table.columns)
    statement = sql.SQL("select {columns} from {table} t").format(
        columns=columns, table=sql.Identifier(table.name)
    )
    if table.join_prices:
        statement += sql.SQL(" join prices p on p.id = t.price_id")
    if table.windowed:
        alias = "p" if table.join_prices else "t"
        statement += sql.SQL(" where {col} >= %(start)s and {col} < %(end)s").format(
            col=sql.Identifier(alias, "loading_date")
        )
    return statement


def _create_temp_table(target: psycopg.Connection, table: SourceTable) -> sql.Identifier:
    temp_name = sql.Identifier(f"tmp_{table.name}")
    target.execute(
        sql.SQL("create temp table {tmp} (like {raw}) on commit drop").format(
            tmp=temp_name, raw=sql.Identifier("raw", table.name)
        )
    )
    target.execute(
        sql.SQL("alter table {tmp} drop column _batch_id, drop column _loaded_at").format(tmp=temp_name)
    )
    return temp_name


def _copy_table(
    source: psycopg.Connection,
    target: psycopg.Connection,
    table: SourceTable,
    start: date,
    end: date,
) -> int:
    temp_name = _create_temp_table(target, table)
    copy_out = sql.SQL("copy ({select}) to stdout").format(select=_select_statement(table))
    copy_in = sql.SQL("copy {tmp} ({columns}) from stdin").format(
        tmp=temp_name,
        columns=sql.SQL(", ").join(sql.Identifier(column) for column in table.columns),
    )
    params = {"start": start, "end": end} if table.windowed else None
    with source.cursor().copy(copy_out, params) as reader, target.cursor().copy(copy_in) as writer:
        for block in reader:
            writer.write(block)
    target.execute(sql.SQL("analyze {tmp}").format(tmp=temp_name))
    return target.execute(sql.SQL("select count(*) from {tmp}").format(tmp=temp_name)).fetchone()[0]


def extract_to_temp(
    source: psycopg.Connection,
    target: psycopg.Connection,
    start: date,
    end: date,
) -> dict[str, int]:
    """Читает все таблицы одним снимком: сделки и связанные строки согласованы между собой."""
    source.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    source.read_only = True
    counts = {table.name: _copy_table(source, target, table, start, end) for table in SOURCE_TABLES}
    source.rollback()
    log_event(logger, "source_extracted", window_start=start, window_end=end, **counts)
    return counts


def source_date_range(source_dsn: str) -> tuple[date, date]:
    """Возвращает окно [min, max + 1 день) по loading_date."""
    with psycopg.connect(source_dsn) as conn:
        low, high = conn.execute(
            "select min(loading_date), max(loading_date) + 1 from prices"
        ).fetchone()
    if low is None:
        raise RuntimeError("В источнике нет сделок")
    return low, high
