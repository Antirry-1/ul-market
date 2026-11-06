"""Правила качества данных, в том числе те, что на сгенерированном источнике не срабатывают."""

from datetime import date, datetime

import pytest


def reasons(query, deal_id: int) -> set[str]:
    return {row[0] for row in query(
        "select reason_code from dq.dq_quarantine_prices where price_id = %s", (deal_id,)
    )}


def in_fact(query, deal_id: int) -> bool:
    return bool(query("select 1 from core.fact_price where price_id = %s", (deal_id,)))


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"price": 0.0}, {"PRICE_NON_POSITIVE"}),
        ({"price": -5.0}, {"PRICE_NON_POSITIVE"}),
        ({"price": 1000.0}, {"RUB_PRICE_TOO_LOW"}),
        ({"price": 0.01}, {"RUB_PRICE_TOO_LOW"}),
        ({"distance": 0.0}, {"DISTANCE_NON_POSITIVE"}),
        ({"distance": -1.0}, {"DISTANCE_NON_POSITIVE"}),
        ({"total_weight": -0.1}, {"WEIGHT_NEGATIVE"}),
        ({"total_volume": -0.1}, {"VOLUME_NEGATIVE"}),
        ({"loading_city_gcid": 9999}, {"LOADING_CITY_NOT_FOUND"}),
        ({"unloading_city_gcid": 9999}, {"UNLOADING_CITY_NOT_FOUND"}),
        ({"currency_code": "840", "nearest_rate_date": None}, {"FX_RATE_MISSING"}),
        ({"currency_code": "978", "nearest_rate_date": date(2026, 9, 11)}, {"FX_RATE_MISSING"}),
        ({"price": 0.0, "distance": 0.0}, {"PRICE_NON_POSITIVE", "DISTANCE_NON_POSITIVE"}),
        ({"price": float("nan")}, {"VALUE_NOT_FINITE"}),
        ({"price": float("inf")}, {"VALUE_NOT_FINITE"}),
        ({"price": float("-inf")}, {"VALUE_NOT_FINITE", "PRICE_NON_POSITIVE"}),
        ({"distance": float("nan")}, {"VALUE_NOT_FINITE"}),
        ({"distance": float("inf")}, {"VALUE_NOT_FINITE"}),
        ({"total_weight": float("nan")}, {"VALUE_NOT_FINITE"}),
        ({"total_volume": float("inf")}, {"VALUE_NOT_FINITE"}),
        ({"price": 1e9}, {"PRICE_RUB_OUT_OF_RANGE"}),
        ({"price": 1e17}, {"PRICE_RUB_OUT_OF_RANGE"}),
        ({"price": 5000.0, "distance": 1e-6}, {"PRICE_RUB_OUT_OF_RANGE"}),
        ({"price": 0.001, "currency_code": "840"}, {"PRICE_RUB_OUT_OF_RANGE"}),
    ],
)
def test_invalid_deal_goes_to_quarantine_with_reason(source, load, query, overrides, expected):
    deal_id = source.add_deal(**overrides)
    load()
    assert reasons(query, deal_id) == expected
    assert not in_fact(query, deal_id)


@pytest.mark.parametrize(
    "overrides",
    [
        {"price": 1000.01},
        {"total_weight": 0.0},
        {"total_volume": 0.0},
        {"unloading_city_gcid": 1001},
        {"nearest_rate_date": None},
        {"currency_code": "840", "price": 500.0},
    ],
)
def test_valid_deal_stays_in_core(source, load, query, overrides):
    deal_id = source.add_deal(**overrides)
    load()
    assert reasons(query, deal_id) == set()
    assert in_fact(query, deal_id)


def test_quarantine_keeps_source_row(source, load, query):
    deal_id = source.add_deal(price=0.0, loading_date=date(2026, 9, 20))
    load()
    ((raw_row, loading_date),) = query(
        "select raw_row, loading_date from dq.dq_quarantine_prices where price_id = %s", (deal_id,)
    )
    assert raw_row["id"] == deal_id
    assert loading_date == date(2026, 9, 20)


def test_load_journal_counts_rows(source, load, query):
    source.add_deal()
    source.add_deal(price=0.0, distance=0.0)
    load()
    ((status, read, loaded, quarantined, details),) = query(
        "select status, rows_read, rows_loaded, rows_quarantined, details from ops.pipeline_runs"
    )
    assert (status, read, loaded, quarantined) == ("success", 2, 1, 1)
    assert details["quarantine_reasons"] == {"DISTANCE_NON_POSITIVE": 1, "PRICE_NON_POSITIVE": 1}


def test_currency_conversion_uses_stored_rate_date(source, load, query):
    deal_id = source.add_deal(currency_code="840", price=1000.0)
    load()
    assert query("select rate_value, price_rub from core.fact_price where price_id = %s", (deal_id,)) == [
        (90.0, 90000)
    ]


def test_duplicate_rate_does_not_multiply_deal(source, load, query):
    source.add_rate("840", 91.0, date(2026, 9, 10))
    deal_id = source.add_deal(currency_code="840", price=1000.0)
    load()
    assert query("select rate_value from core.fact_price where price_id = %s", (deal_id,)) == [(91.0,)]


def test_rate_date_audit_reports_but_keeps_stored_date(source, load, query):
    source.add_rate("840", 95.0, date(2026, 9, 12))
    expected_next_rate = source.add_deal(
        currency_code="840", created_at=datetime(2026, 9, 1, 8, 0), nearest_rate_date=date(2026, 9, 10)
    )
    wrong_date = source.add_deal(
        currency_code="840", created_at=datetime(2026, 9, 20, 8, 0), nearest_rate_date=date(2026, 9, 10)
    )
    load()
    ((audits,),) = query("select details -> 'audits' from ops.pipeline_runs")
    assert audits["nearest_rate_date_differs_from_rule"] == 1
    assert query(
        "select price_id, rate_value from core.fact_price order by price_id"
    ) == [(expected_next_rate, 90.0), (wrong_date, 90.0)]


@pytest.mark.parametrize("rate", [0.0, -5.0, float("nan"), float("inf")])
def test_unusable_rate_goes_to_quarantine(source, load, query, rate):
    source.add_rate("840", rate, date(2026, 9, 11))
    deal_id = source.add_deal(currency_code="840", nearest_rate_date=date(2026, 9, 11))
    load()
    assert reasons(query, deal_id) == {"FX_RATE_INVALID"}
    assert not in_fact(query, deal_id)


@pytest.mark.parametrize(
    ("weight", "category"),
    [(0.0, "OTH"), (0.44, "OTH"), (0.45, "L5T"), (2.44, "L5T"), (2.45, "5T"), (4.96, "10T"),
     (9.94, "10T"), (9.96, "FTL"), (10.0, "FTL"), (29.94, "FTL"), (29.96, "G20T"), (30.0, "G20T")],
)
def test_weight_category_bounds(query, weight, category):
    assert query("select core.weight_category(%s)", (weight,)) == [(category,)]


@pytest.mark.parametrize(
    ("volume", "category"),
    [(0.0, "OTH"), (0.1, "FTL"), (120.0, "FTL"), (120.1, "OTH")],
)
def test_volume_category_bounds(query, volume, category):
    assert query("select core.volume_category(%s)", (volume,)) == [(category,)]


@pytest.mark.parametrize("empty", [None, "", " "])
def test_missing_categories_are_filled(source, load, query, empty):
    deal_id = source.add_deal(total_weight=9.96, total_volume=0.0, weight_category=empty, volume_category=empty)
    load()
    assert query(
        "select weight_category, volume_category from core.fact_price where price_id = %s", (deal_id,)
    ) == [("FTL", "OTH")]


def test_source_category_is_kept(source, load, query):
    deal_id = source.add_deal(total_weight=9.96, weight_category="10T")
    load()
    assert query("select weight_category from core.fact_price where price_id = %s", (deal_id,)) == [("10T",)]
    ((audits,),) = query("select details -> 'audits' from ops.pipeline_runs")
    assert audits["source_category_differs_from_rule"] == 1
