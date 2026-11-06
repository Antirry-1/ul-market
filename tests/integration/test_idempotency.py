"""Повторный запуск и изменения в источнике."""

from datetime import date

import pytest

from etl.audits import AuditFailed
from etl.load import load_window
from etl.marts import build_marts

HASH_QUERIES = {
    "raw.prices": "select count(*), md5(string_agg(id || '|' || price || '|' || loading_date, ',' order by id)) from raw.prices",
    "fact": """
        select count(*), sum(price_rub),
               md5(string_agg(price_id || '|' || price_rub || '|' || primary_body_group || '|' || weight_category,
                              ',' order by price_id))
        from core.fact_price
    """,
    "bridge": "select count(*), md5(string_agg(price_id || body_group, ',' order by price_id, body_group)) from core.bridge_price_body_group",
    "dates": "select count(*), md5(string_agg(price_id || '|' || price_date, ',' order by price_id, price_date)) from core.fact_price_date",
    "quarantine": "select count(*), md5(string_agg(price_id || reason_code, ',' order by price_id, reason_code)) from dq.dq_quarantine_prices",
    "mart": """
        select count(*), sum(trips_count), sum(median_trip), sum(p75_km),
               md5(string_agg(m::text, ',' order by month, loading_city_gcid, unloading_city_gcid, body_group, weight_category))
        from mart.mart_route_card m
    """,
}


def snapshot(query) -> dict:
    return {name: query(statement) for name, statement in HASH_QUERIES.items()}


@pytest.fixture
def populated(source):
    source.add_deal(loading_date=date(2026, 9, 15), links=[56, 15])
    source.add_deal(loading_date=date(2026, 9, 20), price=200000.0, body_type=None, links=[])
    source.add_deal(loading_date=date(2026, 10, 5), dates=[date(2026, 10, 5), date(2026, 11, 5)])
    source.add_deal(loading_date=date(2026, 10, 6), price=0.0)
    source.add_deal(loading_date=date(2026, 10, 7), currency_code="840", price=1000.0)
    source.add_deal(loading_date=date(2026, 10, 8), currency_code="840", nearest_rate_date=None)
    return source


def test_repeat_of_full_and_partial_window_changes_nothing(populated, load, query, settings):
    load()
    build_marts(settings)
    before = snapshot(query)
    assert before["fact"][0][0] == 4

    load()
    build_marts(settings)
    assert snapshot(query) == before

    load(date(2026, 10, 1), date(2026, 11, 1))
    build_marts(settings)
    assert snapshot(query) == before


def test_window_is_half_open(source, load, query):
    source.add_deal(loading_date=date(2026, 9, 30))
    inside = source.add_deal(loading_date=date(2026, 10, 1))
    source.add_deal(loading_date=date(2026, 11, 1))
    load(date(2026, 10, 1), date(2026, 11, 1))
    assert query("select price_id from core.fact_price") == [(inside,)]


def test_deal_moved_to_other_window_has_one_version(source, load, query):
    deal_id = source.add_deal(loading_date=date(2026, 9, 15))
    load()
    source.execute("update prices set loading_date = '2026-10-15' where id = %s", (deal_id,))
    source.execute("update price_dates set price_date = '2026-10-15' where price_id = %s", (deal_id,))

    load(date(2026, 10, 1), date(2026, 11, 1))

    assert query("select loading_date from core.fact_price") == [(date(2026, 10, 15),)]
    assert query("select price_date from core.fact_price_date") == [(date(2026, 10, 15),)]
    assert query("select count(*) from raw.prices") == [(1,)]


def test_deal_removed_from_source_leaves_window(source, load, query):
    keep = source.add_deal()
    gone = source.add_deal()
    load()
    source.execute("delete from prices where id = %s", (gone,))
    load()
    assert query("select price_id from core.fact_price") == [(keep,)]
    assert query("select count(*) from core.bridge_price_body_group") == [(1,)]


def test_deal_moved_out_of_reloaded_window_is_reported(source, query, settings):
    deal_id = source.add_deal(loading_date=date(2026, 9, 15), dates=[date(2026, 9, 15)])
    load_window(settings, date(2026, 9, 1), date(2026, 10, 1))
    source.execute("update prices set loading_date = '2026-08-15' where id = %s", (deal_id,))
    load_window(settings, date(2026, 9, 1), date(2026, 10, 1))
    audits = query("select details -> 'audits' from ops.pipeline_runs order by run_id desc limit 1")[0][0]
    assert audits["deal_left_window"] == 1
    assert query("select count(*) from core.fact_price") == [(0,)]

    load_window(settings, date(2026, 8, 1), date(2026, 9, 1))
    assert query("select price_id from core.fact_price") == [(deal_id,)]


def test_fixed_deal_leaves_quarantine(source, load, query):
    deal_id = source.add_deal(price=0.0)
    load()
    assert query("select count(*) from dq.dq_quarantine_prices") == [(1,)]

    source.execute("update prices set price = 50000 where id = %s", (deal_id,))
    load()
    assert query("select count(*) from dq.dq_quarantine_prices") == [(0,)]
    assert query("select price_rub from core.fact_price") == [(50000,)]


def test_broken_deal_leaves_fact_and_links(source, load, query):
    deal_id = source.add_deal()
    load()
    source.execute("update prices set price = 0 where id = %s", (deal_id,))
    load()
    assert query("select count(*) from core.fact_price") == [(0,)]
    assert query("select count(*) from core.bridge_price_body_group") == [(0,)]
    assert query("select reason_code from dq.dq_quarantine_prices") == [("PRICE_NON_POSITIVE",)]


def test_failed_audit_rolls_back_window(source, load, query, monkeypatch):
    source.add_deal()
    load()
    source.execute("update prices set price = 77777")

    def fail(*_args):
        raise AuditFailed("сбой")

    monkeypatch.setattr("etl.load.run_audits", fail)
    with pytest.raises(AuditFailed):
        load()

    assert query("select price from raw.prices") == [(100000.0,)]
    assert query("select price_rub from core.fact_price") == [(100000,)]
    assert query("select status from ops.pipeline_runs order by run_id") == [("success",), ("failed",)]


def test_failed_audit_keeps_published_mart(source, load, query, settings, monkeypatch):
    source.add_deal()
    load()
    build_marts(settings)
    before = query("select median_trip, trips_count from mart.mart_route_card")
    source.add_deal(price=900000.0)
    load()

    def fail(*_args):
        raise AuditFailed("сбой")

    monkeypatch.setattr("etl.marts.run_audits", fail)
    with pytest.raises(AuditFailed):
        build_marts(settings)
    assert query("select median_trip, trips_count from mart.mart_route_card") == before
