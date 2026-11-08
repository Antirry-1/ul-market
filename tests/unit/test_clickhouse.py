from datetime import date
from decimal import Decimal

from etl import clickhouse as clickhouse_module
from etl.clickhouse import (
    COLUMNS,
    REQUEST_TIMEOUT,
    ClickHouse,
    NULLABLE_COLUMNS,
    RELIABILITY_LEVELS,
    SUMMED_COLUMNS,
    TEXT_COLUMNS,
    export_query,
    find_differences,
    month_totals_query,
    parse_tsv,
)
from etl.db import read_sql


def ddl_columns() -> list[str]:
    body = read_sql("clickhouse/mart_route_card.sql").split("(", 1)[1].split("\n)\nengine")[0]
    return [line.split()[0] for line in body.strip().splitlines()]


def mart_columns() -> list[str]:
    ddl = read_sql("ddl/040_mart.sql")
    body = ddl.split("create table if not exists mart.mart_route_card (", 1)[1].split("primary key", 1)[0]
    return [line.split()[0] for line in body.strip().splitlines()]


def test_mirror_columns_match_clickhouse_table():
    assert ddl_columns() == list(COLUMNS)


def test_mirror_covers_every_mart_column():
    assert mart_columns() == list(COLUMNS)


def test_summed_columns_exist_in_mart():
    assert set(SUMMED_COLUMNS) | set(NULLABLE_COLUMNS) | set(TEXT_COLUMNS) <= set(COLUMNS)


def test_reliability_levels_match_mart_constraint():
    ddl = read_sql("ddl/040_mart.sql")
    constraint = ddl.split("reliability_level in (", 1)[1].split(")", 1)[0]
    assert tuple(level.strip().strip("'") for level in constraint.split(",")) == RELIABILITY_LEVELS


def test_month_totals_check_keys_and_text():
    # Без них зеркало с испорченными подписями или ключами ячеек прошло бы сверку.
    query = month_totals_query("t", "month")
    assert "sum(loading_city_gcid)" in query
    assert "sum(char_length(loading_city))" in query
    assert "sum(case when reliability_level = 'LOW_DATA' then 1 else 0 end)" in query
    assert "sum(case when is_partial_month then 1 else 0 end)" in query
    # Число строк месяца идёт сразу за месяцем: из него журнал берёт rows_read.
    assert query.startswith("select month as month_key, count(*),")


def test_export_writes_null_and_bool_in_clickhouse_format():
    query = export_query()
    assert "null '\\N'" in query
    assert "is_partial_month::int" in query


def test_month_totals_count_nulls_separately():
    query = month_totals_query("t", "month")
    assert "count(mom_pct)" in query
    assert "sum(mom_pct)" in query


def row(month: object, total: object) -> tuple:
    return (month, 2, total, None)


def test_same_totals_in_both_systems_have_no_differences():
    postgres = [row(date(2026, 9, 1), Decimal("10.50"))]
    clickhouse = [row("2026-09-01", "10.5")]
    assert find_differences(postgres, clickhouse) == []


def test_changed_total_is_reported():
    postgres = [row(date(2026, 9, 1), Decimal("10.50"))]
    clickhouse = [row("2026-09-01", "10.51")]
    assert find_differences(postgres, clickhouse) == ["2026-09-01"]


def test_missing_month_is_reported():
    postgres = [row(date(2026, 8, 1), 1), row(date(2026, 9, 1), 1)]
    clickhouse = [row("2026-09-01", 1)]
    assert find_differences(postgres, clickhouse) == ["2026-08-01"]


def test_month_without_any_value_matches_empty_postgres_sum():
    postgres = [(date(2023, 10, 1), 2, Decimal("10.50"), None)]
    clickhouse = parse_tsv("2023-10-01\t2\t10.50\t\\N\n")
    assert clickhouse == [("2023-10-01", "2", "10.50", None)]
    assert find_differences(postgres, clickhouse) == []


def test_request_waits_for_clickhouse_no_longer_than_timeout(monkeypatch):
    # Без таймаута зависший ClickHouse держал бы зеркало и задачу DAG бесконечно.
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"1\n"

    def fake_urlopen(request, timeout=None):
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(clickhouse_module.urllib.request, "urlopen", fake_urlopen)
    ClickHouse("http://clickhouse:8123/", "market", "user", "secret").execute("select 1")
    assert seen["timeout"] == REQUEST_TIMEOUT
    assert 0 < REQUEST_TIMEOUT <= 600
