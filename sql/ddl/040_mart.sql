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
    mom_pct             numeric(12, 2),
    yoy_pct             numeric(12, 2),
    reliability_level   text not null check (reliability_level in ('HIGH', 'MEDIUM', 'LOW_DATA')),
    last_trip_date      date not null,
    freshness_days      integer not null,
    is_partial_month    boolean not null,
    as_of               date not null,
    primary key (loading_city_gcid, unloading_city_gcid, body_group, weight_category, month)
);
create index if not exists mart_route_card_month_reliability_idx
    on mart.mart_route_card (month, reliability_level);

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
    primary key (price_id, model_name),
    check (split <> 'future'
           or (actual_price_rub is null and abs_error_rub is null and ape_pct is null))
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
    primary key (model_name, split, slice)
);
