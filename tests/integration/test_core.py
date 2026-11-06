"""Измерения и связи: join не должен размножать сделки."""

from datetime import date

import pytest


def test_city_duplicates_collapse_to_minimal_id(source, load, query):
    source.add_deal()
    load()
    assert query("select gc_id, source_id, city_name, region from core.dim_city order by gc_id") == [
        (1001, 1, "Москва", "г. Москва"),
        (1003, 3, "Казань", "Республика Татарстан"),
        (1004, 5, "Екатеринбург", "Свердловская обл."),
    ]


def test_joins_keep_one_row_per_deal(source, load, query):
    for _ in range(5):
        source.add_deal(links=[56, 15], dates=[date(2026, 9, 15), date(2026, 10, 15)])
    load()
    assert query("select count(*) from core.fact_price") == [(5,)]
    assert query("select count(*) from core.v_ml_features") == [(5,)]
    assert query("select count(*) from core.bridge_price_body_group") == [(10,)]
    assert query("select count(*) from core.fact_price_date") == [(10,)]


def test_body_group_mapping(source, load, query):
    cases = {
        source.add_deal(body_type=56, links=[56, 57, 61]): (["Тент"], "Тент"),
        source.add_deal(body_type=56, links=[56, 15]): (["Борт", "Тент"], "Тент"),
        source.add_deal(body_type=4, links=[46]): (["Рефрижератор"], "Рефрижератор"),
        source.add_deal(body_type=2, links=[]): (["Изотерм"], "Изотерм"),
        source.add_deal(body_type=1, links=[]): (["Борт"], "Борт"),
        source.add_deal(body_type=50): (["Прочие"], "Прочие"),
        source.add_deal(body_type=777, links=[]): (["Прочие"], "Прочие"),
        source.add_deal(body_type=None, links=[]): (["Не указан"], "Не указан"),
        source.add_deal(body_type=None, links=[4]): (["Рефрижератор"], "Рефрижератор"),
    }
    load()
    bridge = query(
        """
        select f.price_id, f.primary_body_group, array_agg(b.body_group order by b.body_group)
        from core.fact_price f
        join core.bridge_price_body_group b using (price_id)
        group by 1, 2
        """
    )
    assert {deal: (groups, primary) for deal, primary, groups in bridge} == cases


def test_forbidden_source_fields_are_not_in_dwh(load, query):
    load()
    assert query(
        """
        select table_schema, table_name, column_name
        from information_schema.columns
        where column_name = 'price_group_id' or table_name = 'price_group_stats'
        """
    ) == []


@pytest.mark.parametrize(
    ("body_type", "links", "expected"),
    [
        (56, [56], "Тент"),
        (56, [15, 56], "Тент"),
        (56, [4], "Рефрижератор"),
        (56, [], "Тент"),
        (None, [4], "Рефрижератор"),
        (None, [], "Не указан"),
    ],
)
def test_primary_body_group_is_one_of_deal_groups(source, load, query, body_type, links, expected):
    source.add_deal(body_type=body_type, links=links)
    load()
    assert query("select primary_body_group from core.fact_price") == [(expected,)]
    assert query(
        """
        select count(*)
        from core.fact_price f
        join core.bridge_price_body_group b
          on b.price_id = f.price_id and b.body_group = f.primary_body_group
        """
    ) == [(1,)]


def test_model_ignores_bid_marked_as_fact(source, load, query):
    source.add_deal(price_type=6, is_fact=True)
    load()
    assert query("select count(*) from core.v_ml_features") == [(0,)]
