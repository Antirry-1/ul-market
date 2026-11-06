"""Предел качества прогноза на загруженном стенде.

Случайные числа генератор хранит во временной таблице, поэтому без множителей шума и аномалий
он выдаёт для тех же сделок точную цену. Сравнение с фактом показывает ошибку, ниже которой
честная модель опуститься не может.

Запуск: docker compose run --rm pipeline pytest -m stand -s
"""

from __future__ import annotations

import os
import re

import numpy as np
import pandas as pd
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from etl.config import Settings
from etl.db import SQL_DIR
from ml.storage import MAIN_MODEL

pytestmark = pytest.mark.stand

NOISELESS_DB = "ul_test_noiseless"
N_PRICES = int(os.environ.get("N_PRICES", "300000"))

# Ожидаемые значения посчитаны на этом объёме. На малой выборке модель может случайно обойти предел.
REFERENCE_PRICES = 300000
reference_size_only = pytest.mark.skipif(
    N_PRICES != REFERENCE_PRICES, reason=f"значения зафиксированы для N_PRICES={REFERENCE_PRICES}"
)

NOISE_FACTOR = "* exp(0.16 * sqrt(-2 * ln(greatest(g3.r_noise1, 1e-9))) * cos(2 * pi() * g3.r_noise2))"
ANOMALY_FACTOR = "* CASE WHEN g3.r_out < 0.012 THEN 0.25 WHEN g3.r_out > 0.988 THEN 4 ELSE 1 END"

# Генератор делит цену в валюте на эти числа, затем сделка пересчитывается по курсу на дату.
CURRENCY_DIVIDER = {"643": 1.0, "840": 90.0, "978": 100.0}

ACTUAL_QUERY = """
select f.price_id, f.price_rub::float8 as actual, f.rate_value, f.currency_code,
       f.loading_date, f.distance, f.total_weight, f.price_type
from mart.ml_price_predictions p
join core.fact_price f using (price_id)
where p.model_name = %s and p.split = 'test' and not p.outlier_mark
order by f.price_id
"""

NOISELESS_QUERY = """
select id as price_id, price as noiseless_price, loading_date, distance, total_weight, price_type
from prices
"""


def noiseless_generator() -> str:
    text = (SQL_DIR / "synthetic.sql").read_text(encoding="utf-8")
    for factor in (NOISE_FACTOR, ANOMALY_FACTOR):
        assert text.count(factor) == 1, f"В генераторе не найден множитель: {factor}"
        text = text.replace(factor, "")
    # Команды psql сервер не выполняет.
    text = re.sub(r"^\\.*$", "", text, flags=re.MULTILINE)
    return text.replace(":n_prices", str(N_PRICES))


def read_frame(conn: psycopg.Connection, query: str, params: tuple = ()) -> pd.DataFrame:
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        columns = [column.name for column in cursor.description]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


def metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    error = actual - predicted
    return {
        "mape": float(np.mean(np.abs(error) / actual) * 100),
        "wape": float(np.abs(error).sum() / actual.sum() * 100),
        "rmse": float(np.sqrt(np.mean(error**2))),
        "mae": float(np.abs(error).mean()),
        "r2": float(1 - (error**2).sum() / ((actual - actual.mean()) ** 2).sum()),
    }


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings.from_env()


@pytest.fixture(scope="module")
def actual(settings: Settings) -> pd.DataFrame:
    with psycopg.connect(settings.dwh_dsn) as conn:
        frame = read_frame(conn, ACTUAL_QUERY, (MAIN_MODEL,))
    assert len(frame), "Стенд пуст: сначала выполните docker compose up"
    return frame


@pytest.fixture(scope="module")
def noiseless(settings: Settings) -> pd.DataFrame:
    conninfo = conninfo_to_dict(settings.dwh_dsn)
    dsn = make_conninfo(**{**conninfo, "dbname": NOISELESS_DB})
    with psycopg.connect(settings.dwh_dsn, autocommit=True) as admin:
        admin.execute(f"drop database if exists {NOISELESS_DB} with (force)")
        admin.execute(f"create database {NOISELESS_DB}")
    try:
        with psycopg.connect(dsn) as conn:
            conn.execute((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
            conn.execute(noiseless_generator())
            yield read_frame(conn, NOISELESS_QUERY)
    finally:
        with psycopg.connect(settings.dwh_dsn, autocommit=True) as admin:
            admin.execute(f"drop database if exists {NOISELESS_DB} with (force)")


@pytest.fixture(scope="module")
def deals(actual: pd.DataFrame, noiseless: pd.DataFrame) -> pd.DataFrame:
    return actual.merge(noiseless, on="price_id", how="left", suffixes=("", "_noiseless"))


def floor_with_rate(deals: pd.DataFrame) -> np.ndarray:
    rate = deals["rate_value"].astype("float64").where(deals["currency_code"] != "643", 1.0)
    return (deals["noiseless_price"] * rate).to_numpy()


def test_generator_reproduces_the_same_deals(deals):
    assert deals["noiseless_price"].notna().all()
    for column in ("loading_date", "distance", "total_weight", "price_type"):
        assert (deals[column] == deals[f"{column}_noiseless"]).all(), column


@reference_size_only
def test_floor_on_test_rows(deals):
    result = metrics(deals["actual"].to_numpy(), floor_with_rate(deals))
    print(
        f"\nПредел на {len(deals)} строках test: MAPE {result['mape']:.3f} %, WAPE {result['wape']:.3f} %, "
        f"RMSE {result['rmse']:.0f}, MAE {result['mae']:.0f}, R2 {result['r2']:.4f}"
    )
    assert len(deals) == 12808
    assert result["mape"] == pytest.approx(13.594, abs=0.001)
    assert result["wape"] == pytest.approx(13.078, abs=0.001)
    assert result["rmse"] == pytest.approx(50112, abs=1)
    assert result["mae"] == pytest.approx(25508, abs=1)
    assert result["r2"] == pytest.approx(0.9314, abs=0.0001)


@reference_size_only
def test_ignoring_exchange_rate_overstates_the_floor(deals):
    divider = deals["currency_code"].map(CURRENCY_DIVIDER).to_numpy()
    without_rate = deals["noiseless_price"].to_numpy() * divider
    result = metrics(deals["actual"].to_numpy(), without_rate)
    print(f"\nБез пересчёта по курсу: MAPE {result['mape']:.3f} %")
    assert result["mape"] == pytest.approx(13.78, abs=0.005)


@reference_size_only
def test_model_does_not_beat_the_floor(deals, settings):
    floor = metrics(deals["actual"].to_numpy(), floor_with_rate(deals))
    with psycopg.connect(settings.dwh_dsn) as conn:
        rmse, mae = conn.execute(
            """
            select rmse_rub, mae_rub
            from mart.ml_model_metrics
            where model_name = %s and split = 'test' and slice = 'no_outliers'
            """,
            (MAIN_MODEL,),
        ).fetchone()
    print(f"\nМодель: RMSE {rmse:.0f}, MAE {mae:.0f}; предел: RMSE {floor['rmse']:.0f}, MAE {floor['mae']:.0f}")
    assert rmse >= floor["rmse"]
    assert mae >= floor["mae"]
