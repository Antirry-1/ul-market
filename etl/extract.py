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


REFERENCE_TABLES = (
    SourceTable(
        "cities",
        ("id", "full_name_local", "full_name_gc", "gc_id", "name", "longitude", "latitude",
         "created_at", "updated_at"),
        windowed=False,
    ),
    SourceTable("body_types", ("id", "name", "id_from_1C", "std_id"), windowed=False),
    SourceTable("exchange_rates", ("id", "num_code", "value", "setting_date"), windowed=False),
)
PRICES = SourceTable("prices", PRICE_COLUMNS, windowed=True)
# Связи сделок отбираются по сделке: по её дате погрузки или по её id.
LINK_TABLES = (
    SourceTable("price_dates", ("id", "price_id", "price_date"), windowed=True, join_prices=True),
    SourceTable("price_body_types", ("id", "price_id", "body_type_id"), windowed=True, join_prices=True),
)


def _select_statement(table: SourceTable, *, only_ids: bool = False) -> sql.Composable:
    """Строки таблицы из окна и строки сделок из списка ids.

    only_ids оставляет только список: так дочитываются сделки, которые ушли из окна.
    """
    columns = sql.SQL(", ").join(sql.Identifier("t", column) for column in table.columns)
    statement = sql.SQL("select {columns} from {table} t").format(
        columns=columns, table=sql.Identifier(table.name)
    )
    if table.join_prices:
        statement += sql.SQL(" join prices p on p.id = t.price_id")
    if table.windowed:
        alias = "p" if table.join_prices else "t"
        deal_id = sql.Identifier(alias, "id")
        if only_ids:
            statement += sql.SQL(" where {id} = any(%(ids)s::bigint[])").format(id=deal_id)
        else:
            statement += sql.SQL(
                " where ({col} >= %(start)s and {col} < %(end)s) or {id} = any(%(ids)s::bigint[])"
            ).format(col=sql.Identifier(alias, "loading_date"), id=deal_id)
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


def _copy_rows(
    source: psycopg.Connection,
    target: psycopg.Connection,
    table: SourceTable,
    temp_name: sql.Identifier,
    statement: sql.Composable,
    params: dict | None,
) -> None:
    copy_out = sql.SQL("copy ({select}) to stdout").format(select=statement)
    copy_in = sql.SQL("copy {tmp} ({columns}) from stdin").format(
        tmp=temp_name,
        columns=sql.SQL(", ").join(sql.Identifier(column) for column in table.columns),
    )
    with source.cursor().copy(copy_out, params) as reader, target.cursor().copy(copy_in) as writer:
        for block in reader:
            writer.write(block)


def _finish_temp_table(target: psycopg.Connection, temp_name: sql.Identifier) -> int:
    target.execute(sql.SQL("analyze {tmp}").format(tmp=temp_name))
    return target.execute(sql.SQL("select count(*) from {tmp}").format(tmp=temp_name)).fetchone()[0]


def _deals_left_window(target: psycopg.Connection, start: date, end: date) -> list[int]:
    """Сделки окна в raw, которых нет в снимке окна: их дата погрузки сменилась или их удалили."""
    rows = target.execute(
        """
        select r.id
        from raw.prices r
        left join tmp_prices p on p.id = r.id
        where r.loading_date >= %s and r.loading_date < %s
          and p.id is null
        order by r.id
        """,
        (start, end),
    ).fetchall()
    return [deal_id for (deal_id,) in rows]


def _extract(
    source: psycopg.Connection,
    target: psycopg.Connection,
    table: SourceTable,
    params: dict | None,
) -> int:
    temp_name = _create_temp_table(target, table)
    _copy_rows(source, target, table, temp_name, _select_statement(table), params)
    return _finish_temp_table(target, temp_name)


def extract_to_temp(
    source: psycopg.Connection,
    target: psycopg.Connection,
    start: date,
    end: date,
) -> dict[str, int]:
    """Читает все таблицы одним снимком: сделки и связанные строки согласованы между собой.

    Сделка, у которой дата погрузки ушла за пределы окна, дочитывается по id в том же снимке.
    Иначе перезагрузка окна удалила бы её из raw и core, и она вернулась бы только с загрузкой
    своего нового окна. По id не находятся только сделки, удалённые в источнике.
    """
    source.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    source.read_only = True
    counts = {table.name: _extract(source, target, table, None) for table in REFERENCE_TABLES}

    in_window = _extract(source, target, PRICES, {"start": start, "end": end, "ids": []})
    left = _deals_left_window(target, start, end)
    counts["prices"] = in_window
    if left:
        _copy_rows(source, target, PRICES, sql.Identifier("tmp_prices"),
                   _select_statement(PRICES, only_ids=True), {"ids": left})
        counts["prices"] = _finish_temp_table(target, sql.Identifier("tmp_prices"))
    counts["prices_moved_out_of_window"] = counts["prices"] - in_window

    for table in LINK_TABLES:
        counts[table.name] = _extract(source, target, table, {"start": start, "end": end, "ids": left})
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


def full_window(source_dsn: str, dwh_dsn: str) -> tuple[date, date]:
    """Окно полной загрузки: даты источника вместе с датами, уже лежащими в raw.

    Только по источнику окно сужается, когда там удаляют сделку с крайней датой погрузки.
    Такая сделка оказалась бы вне окна и навсегда осталась бы в raw, core и прогнозах.
    """
    low, high = source_date_range(source_dsn)
    with psycopg.connect(dwh_dsn) as conn:
        # До первого init-dwh таблицы ещё нет: тогда окно задаёт один источник.
        if conn.execute("select to_regclass('raw.prices') is not null").fetchone()[0]:
            raw_low, raw_high = conn.execute(
                "select min(loading_date), max(loading_date) + 1 from raw.prices"
            ).fetchone()
            if raw_low is not None:
                low, high = min(low, raw_low), max(high, raw_high)
    return low, high
