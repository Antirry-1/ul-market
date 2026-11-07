"""Сводка рынка: срезы «Все», границы плеч, сравнение с прошлыми месяцами."""

from datetime import date
from decimal import Decimal

import pytest

from etl.marts import build_marts

ALL = "route = 'Все' and body_group = 'Все' and weight_category = 'Все' and distance_band = 'Все'"


@pytest.fixture
def build(load, settings):
    def run() -> None:
        load()
        build_marts(settings)

    return run


def test_corridor_in_rubles_per_km(source, build, query):
    for price in (100000.0, 200000.0, 300000.0, 400000.0):
        source.add_deal(price=price, distance=1000.0)
    build()
    assert query(f"select p25_km, median_km, p75_km, median_trip, median_distance, trips_count "
                 f"from mart.mart_market_summary where {ALL}") == [(
        Decimal("175.00"), Decimal("250.00"), Decimal("325.00"), Decimal("250000.00"), Decimal("1000.0"), 4,
    )]


def test_deal_with_two_body_groups_counts_once_in_all(source, build, query):
    source.add_deal(body_type=56, links=[56, 15])
    build()
    bodies = query("select body_group, trips_count from mart.mart_market_summary "
                   "where route = 'Все' and weight_category = 'Все' and distance_band = 'Все' order by 1")
    assert len(bodies) == 3
    assert dict(bodies)["Все"] == 1
    assert sum(count for body, count in bodies if body != "Все") == 2


def test_every_filter_combination_has_one_row(source, build, query):
    source.add_deal()
    build()
    assert query("select count(*), count(distinct (route, body_group, weight_category, distance_band)) "
                 "from mart.mart_market_summary") == [(16, 16)]


@pytest.mark.parametrize(("distance", "band"), [
    (299.9, "до 300 км"),
    (300.0, "300–1000 км"),
    (999.9, "300–1000 км"),
    (1000.0, "1000+ км"),
])
def test_distance_band_boundaries(source, build, query, distance, band):
    source.add_deal(distance=distance)
    build()
    assert query("select distinct distance_band from mart.mart_market_summary "
                 "where distance_band <> 'Все'") == [(band,)]


def test_change_to_previous_month_and_january(source, build, query):
    source.add_deal(price=100000.0, dates=[date(2026, 1, 15)], loading_date=date(2026, 1, 15))
    source.add_deal(price=110000.0, dates=[date(2026, 8, 15)], loading_date=date(2026, 8, 15))
    source.add_deal(price=121000.0, dates=[date(2026, 9, 15)], loading_date=date(2026, 9, 15))
    build()
    rows = query(f"select month, mom_km_pct, ytd_km_pct from mart.mart_market_summary where {ALL} order by month")
    assert rows == [
        (date(2026, 1, 1), None, Decimal("0.00")),
        (date(2026, 8, 1), None, Decimal("10.00")),
        (date(2026, 9, 1), Decimal("10.00"), Decimal("21.00")),
    ]


def test_trend_rises_with_prices(source, build, query):
    for month, price in ((7, 100000.0), (8, 150000.0), (9, 200000.0)):
        day = date(2026, month, 15)
        source.add_deal(price=price, dates=[day], loading_date=day)
    build()
    assert query(f"select trend_km from mart.mart_market_summary where {ALL} and month = date '2026-09-01'") == [
        ("▁▄█",),
    ]


def test_constant_series_has_flat_trend(query):
    assert query("select mart.sparkline(array[5, 5, 5]::numeric[])") == [("▄▄▄",)]
