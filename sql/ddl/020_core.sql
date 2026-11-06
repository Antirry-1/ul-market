-- round по numeric: на double вес 2.45 округлился бы до 2.4 и попал в другую категорию.
create or replace function core.weight_category(weight_t double precision)
returns varchar(4)
language sql
immutable
as $$
    select case
        when round(weight_t::numeric, 1) >= 30  then 'G20T'
        when round(weight_t::numeric, 1) >= 10  then 'FTL'
        when round(weight_t::numeric, 1) >= 5   then '10T'
        when round(weight_t::numeric, 1) >= 2.5 then '5T'
        when round(weight_t::numeric, 1) >= 0.5 then 'L5T'
        else 'OTH'
    end
$$;

create or replace function core.volume_category(volume_m3 double precision)
returns varchar(4)
language sql
immutable
as $$
    select case when volume_m3 > 0 and volume_m3 <= 120 then 'FTL' else 'OTH' end
$$;

-- В double precision есть NaN и бесконечности, а сравнения вроде x <= 0 их не отсекают.
create or replace function core.is_finite(value double precision)
returns boolean
language sql
immutable
as $$
    select value not in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
$$;

create table if not exists core.dim_city (
    gc_id      integer primary key,
    source_id  bigint not null,
    city_name  text not null,
    region     text,
    latitude   double precision,
    longitude  double precision
);

create table if not exists core.dim_body_type (
    body_type_id integer primary key,
    name         text not null,
    std_id       integer,
    body_group   text not null check (body_group in ('Тент', 'Рефрижератор', 'Изотерм', 'Борт', 'Прочие'))
);

-- Все сделки, прошедшие DQ, включая ставки торгов, составные маршруты, выбросы и будущие даты.
-- Отбор под рыночную статистику и обучение делают витрина и признаки модели.
create table if not exists core.fact_price (
    price_id            bigint primary key,
    created_at          timestamp(0),
    loading_date        date not null,
    end_loading_date    date,
    loading_city_gcid   integer not null references core.dim_city (gc_id),
    unloading_city_gcid integer not null references core.dim_city (gc_id),
    distance            double precision not null check (distance > 0),
    total_weight        double precision not null check (total_weight >= 0),
    total_volume        double precision not null check (total_volume >= 0),
    weight_category     varchar(4) not null,
    volume_category     varchar(4) not null,
    body_type           integer,
    primary_body_group  text not null,
    price               double precision not null check (price > 0),
    currency_code       varchar(3) not null,
    rate_value          double precision,
    price_rub           numeric(18, 2) not null,
    price_type          smallint not null,
    is_fact             boolean not null,
    complex             boolean not null,
    outlier_mark        boolean not null,
    organization_id     integer,
    _batch_id           bigint not null
);
create index if not exists fact_price_loading_date_idx on core.fact_price (loading_date);

create table if not exists core.bridge_price_body_group (
    price_id   bigint not null references core.fact_price (price_id),
    body_group text not null,
    _batch_id  bigint not null,
    primary key (price_id, body_group)
);

create table if not exists core.fact_price_date (
    price_id   bigint not null references core.fact_price (price_id),
    price_date date not null,
    _batch_id  bigint not null,
    primary key (price_id, price_date)
);
create index if not exists fact_price_date_price_date_idx on core.fact_price_date (price_date);

-- Уникального индекса у курсов нет, поэтому на дату берём одну строку: опубликованную последней.
create or replace view core.v_fx_rate as
select distinct on (num_code, setting_date)
    num_code,
    setting_date,
    value
from raw.exchange_rates
order by num_code, setting_date, id desc;

-- Единственная точка доступа модели к данным. price_rub здесь только как целевая переменная,
-- outlier_mark только для отбора строк: список признаков задан в ml/features.py.
create or replace view core.v_ml_features as
select
    f.price_id,
    f.loading_date,
    f.loading_city_gcid,
    f.unloading_city_gcid,
    f.price_rub,
    f.outlier_mark,
    f.distance,
    f.total_weight,
    f.total_volume,
    o.region    as origin_region,
    d.region    as destination_region,
    o.city_name as origin_city,
    d.city_name as destination_city,
    o.latitude  as origin_lat,
    o.longitude as origin_lon,
    d.latitude  as destination_lat,
    d.longitude as destination_lon,
    f.primary_body_group,
    f.body_type,
    f.weight_category,
    f.volume_category,
    f.price_type
from core.fact_price f
join core.dim_city o on o.gc_id = f.loading_city_gcid
join core.dim_city d on d.gc_id = f.unloading_city_gcid
where f.is_fact
  and f.price_type in (1, 2, 3, 4, 5, 9)
  and not f.complex
  and f.price_rub > 0
  and f.distance > 0;
