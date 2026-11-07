-- Карточка маршрута: месяц x город отправления x город назначения x группа кузова x весовая категория.
-- Месяц берётся из price_dates, поэтому многомесячная сделка входит в каждый свой месяц.
-- Результат пишется во временную таблицу, публикация только после аудитов.

create temp table stg_route_card (like mart.mart_route_card) on commit drop;

insert into stg_route_card (
    month, loading_city_gcid, unloading_city_gcid, body_group, weight_category,
    loading_city, unloading_city,
    p25_trip, median_trip, p75_trip, avg_trip, p25_km, median_km, p75_km,
    trips_count, carriers_count, cv_rate, mom_pct, yoy_pct,
    reliability_level, last_trip_date, freshness_days, is_partial_month, as_of
)
with params as (
    select current_setting('app.as_of')::date as as_of
),
-- Отбор и дедупликация в core.market_deal_months: одна строка на сделку, месяц и группу кузова.
base as (
    select *
    from core.market_deal_months(current_setting('app.as_of')::date)
),
agg as (
    select
        month,
        loading_city_gcid,
        unloading_city_gcid,
        body_group,
        weight_category,
        percentile_cont(0.25) within group (order by price_rub::double precision) as p25_trip,
        percentile_cont(0.50) within group (order by price_rub::double precision) as median_trip,
        percentile_cont(0.75) within group (order by price_rub::double precision) as p75_trip,
        avg(price_rub) as avg_trip,
        percentile_cont(0.25) within group (order by price_km) as p25_km,
        percentile_cont(0.50) within group (order by price_km) as median_km,
        percentile_cont(0.75) within group (order by price_km) as p75_km,
        count(*) as trips_count,
        count(distinct organization_id) filter (where price_type in (3, 9)) as carriers_count,
        stddev_samp(price_rub) / nullif(avg(price_rub), 0) as cv_rate,
        max(last_date) as last_trip_date
    from base
    group by month, loading_city_gcid, unloading_city_gcid, body_group, weight_category
)
select
    cur.month,
    cur.loading_city_gcid,
    cur.unloading_city_gcid,
    cur.body_group,
    cur.weight_category,
    origin.city_label,
    destination.city_label,
    round(cur.p25_trip::numeric, 2),
    round(cur.median_trip::numeric, 2),
    round(cur.p75_trip::numeric, 2),
    round(cur.avg_trip, 2),
    round(cur.p25_km::numeric, 2),
    round(cur.median_km::numeric, 2),
    round(cur.p75_km::numeric, 2),
    cur.trips_count,
    cur.carriers_count,
    round(cur.cv_rate, 6),
    round((100 * (cur.median_trip / nullif(prev_month.median_trip, 0) - 1))::numeric, 2),
    round((100 * (cur.median_trip / nullif(prev_year.median_trip, 0) - 1))::numeric, 2),
    case
        when cur.trips_count >= 30
         and cur.carriers_count >= 5
         and params.as_of - cur.last_trip_date <= 30 then 'HIGH'
        when cur.trips_count >= 10 then 'MEDIUM'
        else 'LOW_DATA'
    end,
    cur.last_trip_date,
    params.as_of - cur.last_trip_date,
    cur.month = date_trunc('month', params.as_of)::date
        and params.as_of < (cur.month + interval '1 month' - interval '1 day')::date,
    params.as_of
from agg cur
cross join params
join core.v_city_label origin on origin.gc_id = cur.loading_city_gcid
join core.v_city_label destination on destination.gc_id = cur.unloading_city_gcid
-- Календарное сравнение через self-join: LAG при пропуске месяца взял бы последний доступный.
left join agg prev_month
       on prev_month.loading_city_gcid = cur.loading_city_gcid
      and prev_month.unloading_city_gcid = cur.unloading_city_gcid
      and prev_month.body_group = cur.body_group
      and prev_month.weight_category = cur.weight_category
      and prev_month.month = (cur.month - interval '1 month')::date
left join agg prev_year
       on prev_year.loading_city_gcid = cur.loading_city_gcid
      and prev_year.unloading_city_gcid = cur.unloading_city_gcid
      and prev_year.body_group = cur.body_group
      and prev_year.weight_category = cur.weight_category
      and prev_year.month = (cur.month - interval '1 year')::date;
