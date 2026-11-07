"""Интеграционные тесты: настоящий код на отдельных базах и маленьких наборах сделок."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import date, datetime

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from etl.config import Settings
from etl.db import SQL_DIR
from etl.init_dwh import init_dwh
from etl.load import load_window

TEST_SOURCE_DB = "ul_test_source"
TEST_DWH_DB = "ul_test_dwh"

WHOLE_RANGE = (date(2020, 1, 1), date(2030, 1, 1))

# Без базы интеграционные тесты пропускаются, и pytest остаётся зелёным на одних unit-тестах.
# REQUIRE_DB=1 превращает пропуск в ошибку: так прогон в контейнере или CI не обманет.
REQUIRE_DB_ENV = "REQUIRE_DB"

BODY_TYPES = (
    (1, "бортовая", 15), (2, "изотермический", 2), (3, "контейнер", 3), (4, "рефрижератор", 4),
    (15, "борт", 15), (46, "рефрижератор мультирежимный", 4), (50, "самосвал", 50),
    (56, "тентованный", 56), (57, "тент бок", 56), (61, "тент полная растентовка", 56),
)

CITIES = (
    (1, 1001, "Москва, г. Москва", 55.75, 37.61),
    (2, 1001, "Москва, г. Москва", 55.75, 37.61),
    (3, 1003, "Казань, Республика Татарстан", 55.79, 49.10),
    (4, 1003, "Казань г, Татарстан", 55.79, 49.10),
    (5, 1004, "Екатеринбург, Свердловская обл.", 56.83, 60.60),
)

SOURCE_TABLES = "price_dates, price_body_types, prices, exchange_rates, cities, body_types"
DWH_TABLES = (
    "raw.prices, raw.price_dates, raw.price_body_types, raw.cities, raw.body_types, raw.exchange_rates, "
    "core.fact_price_date, core.bridge_price_body_group, core.fact_price, core.dim_city, core.dim_body_type, "
    "dq.dq_quarantine_prices, mart.mart_route_card, mart.ml_price_predictions, mart.ml_model_metrics, "
    "ops.pipeline_runs"
)


class SourceData:
    """Заполняет тестовый источник. Значения по умолчанию дают валидную сделку."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.next_id = 1
        self.next_rate_id = 1

    def seed_references(self) -> None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cursor:
            cursor.executemany(
                """
                insert into cities (id, full_name_local, full_name_gc, gc_id, name, latitude, longitude)
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                [(i, name, name, gc, name.split(",")[0], lat, lon) for i, gc, name, lat, lon in CITIES],
            )
            cursor.executemany(
                'insert into body_types (id, name, "id_from_1C", std_id) values (%s, %s, %s, %s)',
                [(i, name, i, std) for i, name, std in BODY_TYPES],
            )
        self.add_rate("840", 90.0, date(2026, 9, 10))
        self.add_rate("978", 100.0, date(2026, 9, 10))

    def add_rate(self, code: str, value: float, setting_date: date) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                "insert into exchange_rates (id, num_code, value, setting_date) values (%s, %s, %s, %s)",
                (self.next_rate_id, code, value, setting_date),
            )
        self.next_rate_id += 1

    def add_deal(self, *, dates: list[date] | None = None, links: list[int] | None = None, **overrides) -> int:
        deal_id = overrides.pop("id", self.next_id)
        self.next_id = max(self.next_id, deal_id) + 1
        price_type = overrides.get("price_type", 3)
        row = {
            "id": deal_id,
            "created_at": datetime(2026, 9, 10, 12, 0),
            "organization_id": 100,
            "loading_date": date(2026, 9, 15),
            "loading_city_gcid": 1001,
            "unloading_city_gcid": 1003,
            "distance": 1000.0,
            "total_weight": 20.0,
            "total_volume": 80.0,
            "body_type": 56,
            "price": 100000.0,
            "currency_code": "643",
            "complex": False,
            "price_type": price_type,
            "is_fact": price_type in (1, 2, 3, 4, 5, 9),
            "nearest_rate_date": date(2026, 9, 10),
            "outlier_mark": False,
            "weight_category": "FTL",
            "volume_category": "FTL",
        }
        row.update(overrides)
        names = ", ".join(row)
        values = ", ".join(f"%({name})s" for name in row)
        deal_dates = dates if dates is not None else [row["loading_date"]]
        deal_links = links if links is not None else ([row["body_type"]] if row["body_type"] is not None else [])
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cursor:
            cursor.execute(f"insert into prices ({names}) values ({values})", row)
            cursor.executemany(
                "insert into price_dates (price_id, price_date) values (%s, %s)",
                [(deal_id, value) for value in deal_dates],
            )
            cursor.executemany(
                "insert into price_body_types (price_id, body_type_id) values (%s, %s)",
                [(deal_id, value) for value in deal_links],
            )
        return deal_id

    def execute(self, query: str, params: tuple = ()) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute(query, params)


@pytest.fixture(scope="session")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    try:
        base = Settings.from_env()
        conninfo = conninfo_to_dict(base.dwh_dsn)
        with psycopg.connect(base.dwh_dsn, autocommit=True, connect_timeout=3) as admin:
            for name in (TEST_SOURCE_DB, TEST_DWH_DB):
                admin.execute(f"drop database if exists {name} with (force)")
                admin.execute(f"create database {name}")
    except (RuntimeError, psycopg.OperationalError) as error:
        if os.environ.get(REQUIRE_DB_ENV) == "1":
            pytest.fail(f"PostgreSQL недоступен, а {REQUIRE_DB_ENV}=1: {error}", pytrace=False)
        pytest.skip(f"PostgreSQL недоступен: {error}")

    source_dsn = make_conninfo(**{**conninfo, "dbname": TEST_SOURCE_DB})
    dwh_dsn = make_conninfo(**{**conninfo, "dbname": TEST_DWH_DB})
    with psycopg.connect(source_dsn) as conn:
        conn.execute((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    init_dwh(dwh_dsn)
    return replace(
        base,
        source_dsn=source_dsn,
        dwh_dsn=dwh_dsn,
        as_of=date(2026, 10, 26),
        model_dir=tmp_path_factory.mktemp("models"),
    )


@pytest.fixture(autouse=True)
def source(settings: Settings) -> SourceData:
    with psycopg.connect(settings.source_dsn) as conn:
        conn.execute(f"truncate {SOURCE_TABLES} restart identity cascade")
    with psycopg.connect(settings.dwh_dsn) as conn:
        conn.execute(f"truncate {DWH_TABLES} restart identity cascade")
    data = SourceData(settings.source_dsn)
    data.seed_references()
    return data


@pytest.fixture
def load(settings: Settings):
    def run(start: date = WHOLE_RANGE[0], end: date = WHOLE_RANGE[1]) -> None:
        load_window(settings, start, end)

    return run


@pytest.fixture
def query(settings: Settings):
    def run(statement: str, params: tuple = ()) -> list[tuple]:
        with psycopg.connect(settings.dwh_dsn) as conn:
            return conn.execute(statement, params).fetchall()

    return run
