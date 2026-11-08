"""Зеркало витрины в ClickHouse.

Таблица заменяется целиком: новая версия собирается рядом, сверяется с PostgreSQL
и только потом подменяет рабочую через EXCHANGE TABLES. Читатели видят либо старую
версию, либо новую, а версия с расхождением не публикуется.
"""

from __future__ import annotations

import base64
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal

from etl.config import Settings
from etl.db import connect, mirror_lock, read_sql
from etl.log import log_event, setup_logging
from etl.runs import tracked_run

logger = logging.getLogger(__name__)

TABLE = "mart_route_card"
STAGING_TABLE = f"{TABLE}_staging"

COLUMNS = (
    "month", "loading_city_gcid", "unloading_city_gcid", "body_group", "weight_category",
    "loading_city", "unloading_city", "p25_trip", "median_trip", "p75_trip", "avg_trip",
    "p25_km", "median_km", "p75_km", "trips_count", "carriers_count", "cv_rate",
    "mom_pct", "yoy_pct", "reliability_level", "last_trip_date", "freshness_days",
    "is_partial_month", "as_of",
)

# Суммы по месяцу: расхождение в любой ячейке меняет сумму своего месяца.
# NULL в Postgres и ClickHouse суммы пропускают одинаково, число пропусков сверяется отдельно.
SUMMED_COLUMNS = (
    "loading_city_gcid", "unloading_city_gcid",
    "p25_trip", "median_trip", "p75_trip", "avg_trip", "p25_km", "median_km", "p75_km",
    "trips_count", "carriers_count", "cv_rate", "mom_pct", "yoy_pct", "freshness_days",
)
NULLABLE_COLUMNS = ("cv_rate", "mom_pct", "yoy_pct")
# Текст сверяется суммарной длиной в символах: обрезка или сбой кодировки её меняют.
# char_length в ClickHouse считает символы UTF-8, как и в PostgreSQL, а не байты.
TEXT_COLUMNS = ("body_group", "weight_category", "loading_city", "unloading_city")
RELIABILITY_LEVELS = ("HIGH", "MEDIUM", "LOW_DATA")


def month_checks() -> tuple[str, ...]:
    """Выражения сверки по месяцу. Каждое одинаково считается в PostgreSQL и в ClickHouse."""
    return (
        "count(*)",
        *(f"sum({name})" for name in SUMMED_COLUMNS),
        *(f"count({name})" for name in NULLABLE_COLUMNS),
        *(f"sum(char_length({name}))" for name in TEXT_COLUMNS),
        *(f"sum(case when reliability_level = '{level}' then 1 else 0 end)" for level in RELIABILITY_LEVELS),
        "sum(case when is_partial_month then 1 else 0 end)",
    )

# Сколько секунд запрос ждёт ответа ClickHouse. Без ограничения зависший сервер держал бы
# зеркало, и задачу DAG, бесконечно. Самый долгий запрос, вставка всей витрины, идёт секунды.
REQUEST_TIMEOUT = 300


@dataclass(frozen=True)
class ClickHouse:
    url: str
    database: str
    user: str
    password: str
    timeout: float = REQUEST_TIMEOUT

    @classmethod
    def from_env(cls) -> "ClickHouse":
        return cls(
            url=f"http://{os.environ['CLICKHOUSE_HOST']}:{os.environ['CLICKHOUSE_PORT']}/",
            database=os.environ["CLICKHOUSE_DB"],
            user=os.environ["CLICKHOUSE_USER"],
            password=os.environ["CLICKHOUSE_PASSWORD"],
        )

    def execute(self, query: str, body: bytes = b"") -> str:
        """Запрос идёт в параметре, а тело остаётся для данных INSERT."""
        params = urllib.parse.urlencode({"database": self.database, "query": query})
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        request = urllib.request.Request(
            f"{self.url}?{params}",
            data=body,
            headers={"Authorization": f"Basic {token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise RuntimeError(error.read().decode("utf-8", "replace")) from error


def export_query() -> str:
    # Bool читается из 1 и 0, а не из t и f. NULL в CSV у ClickHouse записывается как \N.
    columns = ", ".join(
        f"{name}::int" if name == "is_partial_month" else name for name in COLUMNS
    )
    return (
        f"copy (select {columns} from mart.{TABLE} order by month) "
        "to stdout with (format csv, null '\\N')"
    )


def month_totals_query(table: str, month_expression: str) -> str:
    """Месяц и итоги сверки по нему; число строк месяца идёт вторым столбцом."""
    checks = ", ".join(month_checks())
    return (
        f"select {month_expression} as month_key, {checks} "
        f"from {table} group by month_key order by month_key"
    )


def normalize(rows: list[tuple]) -> list[tuple]:
    """Приводит значения обеих систем к одному виду: дата строкой, числа через Decimal."""
    return [
        (str(month), *(Decimal(str(value)) if value is not None else Decimal(0) for value in values))
        for month, *values in rows
    ]


def find_differences(postgres: list[tuple], clickhouse: list[tuple]) -> list[str]:
    source = dict((row[0], row[1:]) for row in normalize(postgres))
    mirror = dict((row[0], row[1:]) for row in normalize(clickhouse))
    return sorted(month for month in source.keys() | mirror.keys() if source.get(month) != mirror.get(month))


def parse_tsv(text: str) -> list[tuple]:
    """Сумма Nullable-колонки пуста, если в месяце нет ни одного значения: TSV пишет её как \\N."""
    return [
        tuple(None if value == "\\N" else value for value in line.split("\t"))
        for line in text.splitlines()
        if line
    ]


def mirror_route_card(settings: Settings, clickhouse: ClickHouse) -> None:
    with tracked_run(settings.dwh_dsn, "clickhouse-mirror") as run, mirror_lock(settings.dwh_dsn):
        ddl = read_sql("clickhouse/mart_route_card.sql")
        clickhouse.execute(f"drop table if exists {STAGING_TABLE}")
        clickhouse.execute(ddl.format(table=STAGING_TABLE))
        clickhouse.execute(ddl.format(table=f"if not exists {TABLE}"))

        with connect(settings.dwh_dsn) as conn:
            # Выгрузка и сверка видят один снимок витрины, даже если её пересоберут в этот момент.
            conn.execute("set transaction isolation level repeatable read read only")
            with conn.cursor().copy(export_query()) as copy:
                payload = b"".join(bytes(chunk) for chunk in copy)
            postgres_totals = conn.execute(
                month_totals_query(f"mart.{TABLE}", "month")
            ).fetchall()

        columns = ", ".join(COLUMNS)
        clickhouse.execute(f"insert into {STAGING_TABLE} ({columns}) format CSV", payload)

        mirror_totals = parse_tsv(clickhouse.execute(
            month_totals_query(f"{STAGING_TABLE} final", "toString(month)") + " format TSV"
        ))
        differences = find_differences(postgres_totals, mirror_totals)
        if differences:
            raise RuntimeError(f"Зеркало расходится с PostgreSQL в месяцах: {differences[:12]}")

        clickhouse.execute(f"exchange tables {STAGING_TABLE} and {TABLE}")
        clickhouse.execute(f"drop table {STAGING_TABLE}")

        run.rows_read = sum(row[1] for row in postgres_totals)
        run.rows_loaded = run.rows_read
        run.details["months_compared"] = len(postgres_totals)


def main() -> None:
    setup_logging()
    mirror_route_card(Settings.from_env(), ClickHouse.from_env())
    log_event(logger, "clickhouse_mirror_finished", table=TABLE)


if __name__ == "__main__":
    main()
