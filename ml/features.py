"""Признаки модели. Всё, что не перечислено здесь явно, в модель не попадает."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import psycopg

TARGET = "price_rub"

NUMERIC_FEATURES = (
    "distance", "total_weight", "total_volume",
    "origin_lat", "origin_lon", "destination_lat", "destination_lon",
    "year", "month", "day_of_week", "day_of_year",
    "month_sin", "month_cos", "day_of_week_sin", "day_of_week_cos",
    "day_of_year_sin", "day_of_year_cos",
)

CATEGORICAL_FEATURES = (
    "origin_region", "destination_region", "origin_city_id", "destination_city_id",
    "primary_body_group", "body_type", "weight_category", "volume_category", "price_type",
)

FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

# Признаки линейной модели уровня цены. Календаря здесь нет: train покрывает июль-октябрь,
# и линейный коэффициент при месяце уводит прогноз на январь на 30 % вниз.
LEVEL_NUMERIC_FEATURES = (
    "distance", "total_weight", "total_volume",
    "origin_lat", "origin_lon", "destination_lat", "destination_lon",
    "ln_distance", "ln_distance_sq", "inverse_distance",
)

# Производные цены, постфактум-агрегаты и поля, известные только после сделки.
FORBIDDEN_FEATURES = frozenset({
    "price", "price_rub", "price_with_vat", "price_km", "price_per_km", "price_per_ton",
    "rate_value", "outlier_mark", "price_group_id", "avg_price", "min_km_price", "max_km_price",
    "outliers_amount", "clear_items_amount", "carrier_cost", "customer_cost", "transportation_cost",
    "profit", "organization_id", "subscriber_id", "created_at", "nearest_rate_date",
})

MISSING_CATEGORY = "missing"


def add_calendar_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Календарные признаки даты погрузки. Гармоники сохраняют близость на стыке периодов."""
    result = frame.copy()
    dates = pd.to_datetime(result["loading_date"])
    result["year"] = dates.dt.year
    result["month"] = dates.dt.month
    result["day_of_week"] = dates.dt.dayofweek
    result["day_of_year"] = dates.dt.dayofyear
    for name, period in (("month", 12.0), ("day_of_week", 7.0), ("day_of_year", 365.25)):
        angle = 2 * np.pi * result[name] / period
        result[f"{name}_sin"] = np.sin(angle)
        result[f"{name}_cos"] = np.cos(angle)
    return result


def prepare_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = add_calendar_features(frame)
    for column in ("price_rub", "distance", "total_weight", "total_volume",
                   "origin_lat", "origin_lon", "destination_lat", "destination_lon"):
        result[column] = pd.to_numeric(result[column], errors="coerce").astype("float64")
    # Цена рейса - ставка на км плюс постоянная часть, в логарифме это нелинейная функция дистанции.
    result["ln_distance"] = np.log(result["distance"])
    result["ln_distance_sq"] = result["ln_distance"] ** 2
    result["inverse_distance"] = 1000.0 / result["distance"]
    # Код города, код кузова и тип записи являются метками, а не величинами.
    result["origin_city_id"] = result["loading_city_gcid"].astype("Int64").astype("string")
    result["destination_city_id"] = result["unloading_city_gcid"].astype("Int64").astype("string")
    result["body_type"] = result["body_type"].astype("Int64").astype("string")
    result["price_type"] = result["price_type"].astype("Int64").astype("string")
    for column in CATEGORICAL_FEATURES:
        result[column] = result[column].astype("object").where(result[column].notna(), MISSING_CATEGORY)
    return result


def read_features(conn: psycopg.Connection, start: date, end: date | None = None) -> pd.DataFrame:
    """Читает полуинтервал [start, end) по дате погрузки; end=None означает без верхней границы."""
    query = "select * from core.v_ml_features where loading_date >= %s"
    params: list[date] = [start]
    if end is not None:
        query += " and loading_date < %s"
        params.append(end)
    with conn.cursor() as cursor:
        cursor.execute(query + " order by loading_date, price_id", params)
        columns = [column.name for column in cursor.description]
        frame = pd.DataFrame(cursor.fetchall(), columns=columns)
    return prepare_features(frame)
