-- Разрядность процентов изменения следует из границ карантина (transform/quarantine.sql).
-- Медиана за рейс лежит в [1, 1e9), поэтому mom_pct и yoy_pct меньше 1e11 и входят в numeric(14, 2).
-- Медиана за км лежит в [0.01, 1e9), поэтому mom_km_pct и ytd_km_pct меньше 1e13 и входят в numeric(16, 2).

-- Первичный ключ начинается с маршрута: карточка читает историю одной ячейки по месяцам.
create table if not exists mart.mart_route_card (
    month               date not null,
    loading_city_gcid   integer not null,
    unloading_city_gcid integer not null,
    body_group          text not null,
    weight_category     varchar(4) not null,
    loading_city        text not null,
    unloading_city      text not null,
    p25_trip            numeric(14, 2) not null,
    median_trip         numeric(14, 2) not null,
    p75_trip            numeric(14, 2) not null,
    avg_trip            numeric(14, 2) not null,
    p25_km              numeric(12, 2) not null,
    median_km           numeric(12, 2) not null,
    p75_km              numeric(12, 2) not null,
    trips_count         integer not null,
    carriers_count      integer not null,
    cv_rate             numeric(12, 6),
    mom_pct             numeric(14, 2),
    yoy_pct             numeric(14, 2),
    reliability_level   text not null check (reliability_level in ('HIGH', 'MEDIUM', 'LOW_DATA')),
    last_trip_date      date not null,
    freshness_days      integer not null,
    is_partial_month    boolean not null,
    as_of               date not null,
    primary key (loading_city_gcid, unloading_city_gcid, body_group, weight_category, month)
);
create index if not exists mart_route_card_month_reliability_idx
    on mart.mart_route_card (month, reliability_level);

-- Сводка рынка: готовый срез на каждое сочетание фильтров, значение «Все» означает срез без этого фильтра.
-- Перцентили не складываются, поэтому собрать их из ячеек карточки маршрута нельзя.
create table if not exists mart.mart_market_summary (
    month            date not null,
    route            text not null,
    body_group       text not null,
    weight_category  text not null,
    distance_band    text not null,
    p25_km           numeric(12, 2) not null,
    median_km        numeric(12, 2) not null,
    p75_km           numeric(12, 2) not null,
    median_trip      numeric(14, 2) not null,
    median_distance  numeric(10, 1) not null,
    trips_count      integer not null,
    mom_km_pct       numeric(16, 2),
    ytd_km_pct       numeric(16, 2),
    trend_km         text not null,
    is_partial_month boolean not null,
    as_of            date not null,
    primary key (route, body_group, weight_category, distance_band, month)
);
create index if not exists mart_market_summary_month_idx
    on mart.mart_market_summary (month, body_group, weight_category, distance_band);

-- Мини-график строкой: значения приводятся к восьми уровням от минимума до максимума ряда.
create or replace function mart.sparkline(val numeric[]) returns text
language sql immutable as $$
    select string_agg(
        case
            when bounds.high = bounds.low then '▄'
            else substr('▁▂▃▄▅▆▇█', 1 + floor(7 * (item.value - bounds.low) / (bounds.high - bounds.low))::int, 1)
        end,
        '' order by item.position
    )
    from unnest(val) with ordinality as item(value, position)
    cross join (select min(v) as low, max(v) as high from unnest(val) as v) bounds
$$;

create table if not exists mart.ml_price_predictions (
    price_id            bigint not null,
    loading_date        date not null,
    loading_city_gcid   integer not null,
    unloading_city_gcid integer not null,
    body_group          text not null,
    actual_price_rub    numeric(18, 2),
    predicted_price_rub numeric(18, 2) not null,
    abs_error_rub       numeric(18, 2),
    ape_pct             numeric(12, 4),
    model_name          text not null,
    split               text not null check (split in ('test', 'future')),
    outlier_mark        boolean not null,
    scored_at           timestamptz not null default now(),
    -- Версия файлов модели (каталог release-* в MODEL_DIR), которая дала прогноз.
    model_release       text not null,
    -- Последняя дата погрузки в обучающих данных модели. У test это день перед test_start:
    -- модель для метрик тестовый период не видела. У future это as_of: будущие заявки
    -- оценивает модель, обученная на всей истории до даты снимка.
    trained_through     date not null,
    primary key (price_id, model_name),
    check (split <> 'future'
           or (actual_price_rub is null and abs_error_rub is null and ape_pct is null)),
    -- Прогноз дала модель, которая эту сделку не видела.
    check (trained_through < loading_date)
);
create index if not exists ml_price_predictions_loading_date_idx
    on mart.ml_price_predictions (loading_date);

create table if not exists mart.ml_model_metrics (
    model_name text not null,
    split      text not null,
    slice      text not null,
    n_rows     integer not null,
    r2         double precision,
    mape_pct   double precision,
    rmse_rub   double precision,
    mae_rub    double precision,
    mdape_pct  double precision,
    wape_pct   double precision,
    -- Ключи не включают версию: в таблицах лежит результат последней версии, а не история.
    model_release text not null,
    primary key (model_name, split, slice)
);
