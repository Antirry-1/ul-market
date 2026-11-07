"""Неполный снимок источника останавливает загрузку, а не стирает хранилище."""

from dataclasses import replace
from datetime import date

import pytest

from etl.audits import AuditFailed
from etl.load import load_window


def load_allowing_shrink(settings) -> None:
    """Загрузка с SOURCE_SHRINK_LIMIT=1: так оператор подтверждает намеренное удаление."""
    load_window(replace(settings, source_shrink_limit=1.0), date(2020, 1, 1), date(2030, 1, 1))


def last_run(query) -> tuple:
    return query("select status, details -> 'audits' from ops.pipeline_runs order by run_id desc limit 1")[0]


def test_empty_reference_stops_load(source, load, query):
    deal_id = source.add_deal()
    load()
    source.execute("delete from cities")

    with pytest.raises(AuditFailed, match="SOURCE_SHRINK_LIMIT"):
        load()

    assert query("select price_id from core.fact_price") == [(deal_id,)]
    assert query("select count(*) from raw.cities") == [(5,)]
    assert query("select count(*) from dq.dq_quarantine_prices") == [(0,)]
    status, audits = last_run(query)
    assert status == "failed"
    assert audits["source_reference_empty"] == 1
    assert audits["source_reference_shrank"] == 5


def test_empty_reference_stops_first_load(source, load, query):
    source.add_deal()
    source.execute("delete from exchange_rates")
    with pytest.raises(AuditFailed):
        load()
    assert query("select count(*) from raw.prices") == [(0,)]


def test_reference_losing_many_rows_stops_load(source, load, query, settings):
    source.add_deal()
    load()
    # Екатеринбург в сделках не участвует, но это 1 строка из 5 справочника.
    source.execute("delete from cities where id = 5")

    with pytest.raises(AuditFailed):
        load()
    assert query("select count(*) from raw.cities") == [(5,)]

    load_allowing_shrink(settings)
    assert query("select count(*) from raw.cities") == [(4,)]


def test_window_losing_many_deals_stops_load(source, load, query, settings, monkeypatch):
    monkeypatch.setattr("etl.load.SHRINK_MIN_DEALS", 2)
    deals = [source.add_deal() for _ in range(3)]
    load()
    source.execute("delete from prices where id <> %s", (deals[0],))

    with pytest.raises(AuditFailed):
        load()
    assert query("select count(*) from core.fact_price") == [(3,)]
    assert last_run(query)[1]["source_window_shrank"] == 2

    load_allowing_shrink(settings)
    assert query("select price_id from core.fact_price") == [(deals[0],)]


def test_few_lost_deals_do_not_stop_load(source, load, query):
    deals = [source.add_deal() for _ in range(3)]
    load()
    source.execute("delete from prices where id <> %s", (deals[0],))

    load()

    assert query("select price_id from core.fact_price") == [(deals[0],)]
    status, audits = last_run(query)
    assert status == "success"
    assert audits["source_window_shrank"] == 0
    assert audits["deal_left_window"] == 2
