-- Зеркало mart.mart_route_card. Перцентили считает PostgreSQL: они не складываются
-- из частей, поэтому ClickHouse хранит готовые значения и отвечает на чтение.
-- Первые колонки ORDER BY совпадают с типичным фильтром карточки маршрута.
create table {table}
(
    month               Date,
    loading_city_gcid   Int32,
    unloading_city_gcid Int32,
    body_group          LowCardinality(String),
    weight_category     LowCardinality(String),
    loading_city        LowCardinality(String),
    unloading_city      LowCardinality(String),
    p25_trip            Decimal(14, 2),
    median_trip         Decimal(14, 2),
    p75_trip            Decimal(14, 2),
    avg_trip            Decimal(14, 2),
    p25_km              Decimal(12, 2),
    median_km           Decimal(12, 2),
    p75_km              Decimal(12, 2),
    trips_count         Int32,
    carriers_count      Int32,
    cv_rate             Nullable(Decimal(12, 6)),
    mom_pct             Nullable(Decimal(12, 2)),
    yoy_pct             Nullable(Decimal(12, 2)),
    reliability_level   LowCardinality(String),
    last_trip_date      Date,
    freshness_days      Int32,
    is_partial_month    Bool,
    as_of               Date
)
engine = ReplacingMergeTree
partition by toYYYYMM(month)
order by (loading_city_gcid, unloading_city_gcid, body_group, weight_category, month)
