"""Витрина: значения, посчитанные вручную, и сверка с pandas."""

import os
from datetime import date
from decimal import Decimal

import pandas as pd
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from etl.marts import build_marts

CARD = "select {} from mart.mart_route_card order by month, body_group"


@pytest.fixture
def build(load, settings):
    def run() -> None:
        load()
        build_marts(settings)

    return run


def test_corridor_on_four_deals(source, build, query):
    for number, price in enumerate((100000.0, 200000.0, 300000.0, 400000.0)):
        source.add_deal(price=price, distance=1000.0, organization_id=100 + number)
    build()
    assert query(CARD.format("p25_trip, median_trip, p75_trip, avg_trip, p25_km, median_km, p75_km, trips_count")) == [(
        Decimal("175000.00"), Decimal("250000.00"), Decimal("325000.00"), Decimal("250000.00"),
        Decimal("175.00"), Decimal("250.00"), Decimal("325.00"), 4,
    )]


def test_metrics_match_pandas(source, build, query):
    prices = [91000.0, 104500.5, 118250.0, 99999.99, 250000.0, 132000.0, 87500.0]
    for number, price in enumerate(prices):
        source.add_deal(price=price, distance=850.0, organization_id=100 + number % 3)
    build()
    series = pd.Series(prices)
    ((p25, median, p75, p75_km, cv),) = query(CARD.format("p25_trip, median_trip, p75_trip, p75_km, cv_rate"))
    assert float(p25) == pytest.approx(series.quantile(0.25), abs=0.01)
    assert float(median) == pytest.approx(series.median(), abs=0.01)
    assert float(p75) == pytest.approx(series.quantile(0.75), abs=0.01)
    assert float(p75_km) == pytest.approx((series / 850.0).quantile(0.75), abs=0.01)
    assert float(cv) == pytest.approx(series.std(ddof=1) / series.mean(), abs=1e-6)


def test_extreme_month_change_fits_mart(source, build, query):
    # Самые крайние медианы, которые пропускает карантин: 1,80 руб. за рейс (0,02 доллара)
    # и 9e8 руб. Изменение около 5e10 % не уместилось бы в прежнюю колонку numeric(12, 2).
    source.add_deal(loading_date=date(2026, 8, 15), currency_code="840", price=0.02, distance=100.0)
    source.add_deal(loading_date=date(2026, 9, 15), price=9e8, distance=100.0)
    build()
    ((mom,),) = query("select mom_pct from mart.mart_route_card where month = '2026-09-01'")
    assert float(mom) == pytest.approx(100 * (9e8 / 1.8 - 1), rel=1e-9)
    ((mom_km,),) = query("""
        select mom_km_pct from mart.mart_market_summary
        where month = '2026-09-01' and route <> 'Все' and body_group = 'Тент'
          and weight_category = 'FTL' and distance_band = 'до 300 км'
    """)
    assert float(mom_km) == pytest.approx(100 * (9e6 / 0.018 - 1), rel=1e-9)


def test_cv_is_null_for_single_trip(source, build, query):
    source.add_deal()
    build()
    assert query(CARD.format("cv_rate")) == [(None,)]


def test_carriers_counted_only_for_carrier_records(source, build, query):
    source.add_deal(price_type=3, organization_id=1)
    source.add_deal(price_type=9, organization_id=2)
    source.add_deal(price_type=9, organization_id=2)
    source.add_deal(price_type=1, organization_id=3)
    build()
    assert query(CARD.format("trips_count, carriers_count")) == [(4, 2)]


@pytest.mark.parametrize(
    "overrides",
    [
        {"price_type": 6},
        {"complex": True},
        {"outlier_mark": True},
        {"loading_date": date(2026, 10, 27)},
    ],
)
def test_excluded_deals_are_in_fact_but_not_in_mart(source, build, query, overrides):
    source.add_deal()
    source.add_deal(price=900000.0, **overrides)
    build()
    assert query("select count(*) from core.fact_price") == [(2,)]
    assert query(CARD.format("trips_count, median_trip")) == [(1, Decimal("100000.00"))]


def test_future_month_of_historic_deal_is_cut_by_row(source, build, query):
    source.add_deal(
        loading_date=date(2026, 9, 15),
        dates=[date(2026, 9, 15), date(2026, 10, 15), date(2026, 11, 15)],
    )
    build()
    assert query(CARD.format("month, trips_count")) == [(date(2026, 9, 1), 1), (date(2026, 10, 1), 1)]


def test_two_dates_in_one_month_count_as_one_trip(source, build, query):
    source.add_deal(dates=[date(2026, 9, 1), date(2026, 9, 30)])
    build()
    assert query(CARD.format("trips_count, last_trip_date")) == [(1, date(2026, 9, 30))]


def test_deal_counts_once_in_group_and_once_per_other_group(source, build, query):
    source.add_deal(body_type=56, links=[56, 57, 15])
    build()
    assert query(CARD.format("body_group, trips_count")) == [("Борт", 1), ("Тент", 1)]


def test_city_duplicates_do_not_multiply_trips(source, build, query):
    source.add_deal(loading_city_gcid=1001, unloading_city_gcid=1003)
    build()
    assert query(CARD.format("loading_city, unloading_city, trips_count")) == [("Москва", "Казань", 1)]


def test_mom_null_when_previous_month_is_missing(source, build, query):
    source.add_deal(loading_date=date(2026, 6, 15), price=100000.0)
    source.add_deal(loading_date=date(2026, 8, 15), price=120000.0)
    build()
    assert query(CARD.format("month, mom_pct")) == [(date(2026, 6, 1), None), (date(2026, 8, 1), None)]


def test_mom_crosses_year_boundary(source, build, query):
    source.add_deal(loading_date=date(2025, 12, 15), price=100000.0)
    source.add_deal(loading_date=date(2026, 1, 15), price=110000.0)
    build()
    assert query(CARD.format("month, mom_pct")) == [
        (date(2025, 12, 1), None),
        (date(2026, 1, 1), Decimal("10.00")),
    ]


def test_yoy_uses_same_month_only(source, build, query):
    source.add_deal(loading_date=date(2025, 9, 15), price=100000.0)
    source.add_deal(loading_date=date(2025, 10, 15), price=500000.0)
    source.add_deal(loading_date=date(2026, 9, 15), price=125000.0)
    build()
    assert query(CARD.format("month, yoy_pct")) == [
        (date(2025, 9, 1), None),
        (date(2025, 10, 1), None),
        (date(2026, 9, 1), Decimal("25.00")),
    ]


def test_dynamics_do_not_cross_cells(source, build, query):
    source.add_deal(loading_date=date(2026, 8, 15), body_type=4)
    source.add_deal(loading_date=date(2026, 9, 15), body_type=56)
    build()
    assert query(CARD.format("mom_pct")) == [(None,), (None,)]


@pytest.mark.parametrize(
    ("trips", "carriers", "last_date", "expected"),
    [
        (30, 5, date(2026, 9, 26), "HIGH"),
        (30, 5, date(2026, 9, 25), "MEDIUM"),
        (30, 4, date(2026, 9, 26), "MEDIUM"),
        (29, 5, date(2026, 9, 26), "MEDIUM"),
        (10, 1, date(2026, 9, 26), "MEDIUM"),
        (9, 5, date(2026, 9, 26), "LOW_DATA"),
    ],
)
def test_reliability_levels(source, build, query, trips, carriers, last_date, expected):
    for number in range(trips):
        source.add_deal(loading_date=last_date, organization_id=number % carriers)
    build()
    assert query(CARD.format("reliability_level, freshness_days")) == [
        (expected, (date(2026, 10, 26) - last_date).days)
    ]


def test_partial_month_flag(source, build, query):
    source.add_deal(loading_date=date(2026, 9, 15))
    source.add_deal(loading_date=date(2026, 10, 15))
    build()
    assert query(CARD.format("month, is_partial_month")) == [
        (date(2026, 9, 1), False),
        (date(2026, 10, 1), True),
    ]


def test_bi_role_reads_mart_only(settings, source, build):
    source.add_deal()
    build()
    params = conninfo_to_dict(settings.dwh_dsn)
    params.update(user="bi_reader", password=os.environ["BI_PASSWORD"])
    with psycopg.connect(make_conninfo(**params), autocommit=True) as conn:
        assert conn.execute("select count(*) from mart.v_route_network").fetchone() == (1,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select count(*) from core.fact_price")


def test_network_view_carries_trust_metrics(source, build, query):
    source.add_deal()
    build()
    columns = {row[0] for row in query(
        """
        select column_name
        from information_schema.columns
        where table_schema = 'mart' and table_name = 'v_route_network'
        """
    )}
    assert {"trips_count", "carriers_count", "cv_rate", "reliability_level", "avg_trip"} <= columns


def test_bid_marked_as_fact_stays_out_of_market(source, build, query):
    source.add_deal(price=100000.0)
    source.add_deal(price=900000.0, price_type=6, is_fact=True)
    build()
    assert query(CARD.format("trips_count, median_trip")) == [(1, Decimal("100000.00"))]


def test_price_rounded_to_zero_stays_out_of_market(source, build, query):
    source.add_deal(price=100000.0)
    source.add_deal(price=0.00001, currency_code="840")
    build()
    assert query(CARD.format("trips_count, median_trip")) == [(1, Decimal("100000.00"))]


def test_largest_allowed_price_fits_mart(source, build, query):
    source.add_deal(price=999_999_999.0, distance=1.0, dates=[date(2026, 8, 15)])
    source.add_deal(price=1001.0, distance=1.0, dates=[date(2026, 9, 15)])
    build()
    assert query("select count(*) from mart.mart_route_card") == [(2,)]
