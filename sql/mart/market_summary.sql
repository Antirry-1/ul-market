-- Сводка рынка: месяц x направление x группа кузова x вес x плечо, по каждому фильтру есть срез «Все».
-- Отбор строк тот же, что у карточки маршрута. Совпадение проверяет аудит summary_matches_route_card.

create temp table stg_market_summary (like mart.mart_market_summary) on commit drop;

insert into stg_market_summary (
    month, route, body_group, weight_category, distance_band,
    p25_km, median_km, p75_km, median_trip, median_distance, trips_count,
    mom_km_pct, ytd_km_pct, trend_km, is_partial_month, as_of
)
with params as (
    select current_setting('app.as_of')::date as as_of
),
base as (
    select
        f.price_id,
        date_trunc('month', d.price_date)::date as month,
        origin.city_name || ' → ' || destination.city_name as route,
        b.body_group,
        f.weight_category,
        case
            when f.distance < 300 then 'до 300 км'
            when f.distance < 1000 then '300–1000 км'
            else '1000+ км'
        end as distance_band,
        f.price_rub::double precision as price_rub,
        f.price_rub::double precision / nullif(f.distance, 0) as price_km,
        f.distance::double precision as distance
    from core.fact_price f
    join core.fact_price_date d on d.price_id = f.price_id
    join core.bridge_price_body_group b on b.price_id = f.price_id
    join core.dim_city origin on origin.gc_id = f.loading_city_gcid
    join core.dim_city destination on destination.gc_id = f.unloading_city_gcid
    cross join params
    where f.is_fact
      and f.price_type in (1, 2, 3, 4, 5, 9)
      and not f.complex
      and not f.outlier_mark
      and f.price > 0
      and f.distance > 0
      and f.price_rub > 0
      and d.price_date <= params.as_of
    group by f.price_id, date_trunc('month', d.price_date), b.body_group, origin.gc_id, destination.gc_id
),
-- Сделка с двумя группами кузова есть в base дважды. Срез «Все» считается по сделкам,
-- иначе рейс вошёл бы в него два раза и сдвинул перцентили.
deals as (
    select distinct price_id, month, route, weight_category, distance_band, price_rub, price_km, distance
    from base
),
slices as (
    select
        month,
        coalesce(route, 'Все') as route,
        body_group,
        coalesce(weight_category, 'Все') as weight_category,
        coalesce(distance_band, 'Все') as distance_band,
        percentile_cont(0.25) within group (order by price_km) as p25_km,
        percentile_cont(0.50) within group (order by price_km) as median_km,
        percentile_cont(0.75) within group (order by price_km) as p75_km,
        percentile_cont(0.50) within group (order by price_rub) as median_trip,
        percentile_cont(0.50) within group (order by distance) as median_distance,
        count(*) as trips_count
    from base
    group by month, body_group, cube(route, weight_category, distance_band)
    union all
    select
        month,
        coalesce(route, 'Все'),
        'Все',
        coalesce(weight_category, 'Все'),
        coalesce(distance_band, 'Все'),
        percentile_cont(0.25) within group (order by price_km),
        percentile_cont(0.50) within group (order by price_km),
        percentile_cont(0.75) within group (order by price_km),
        percentile_cont(0.50) within group (order by price_rub),
        percentile_cont(0.50) within group (order by distance),
        count(*)
    from deals
    group by month, cube(route, weight_category, distance_band)
)
select
    cur.month,
    cur.route,
    cur.body_group,
    cur.weight_category,
    cur.distance_band,
    round(cur.p25_km::numeric, 2),
    round(cur.median_km::numeric, 2),
    round(cur.p75_km::numeric, 2),
    round(cur.median_trip::numeric, 2),
    round(cur.median_distance::numeric, 1),
    cur.trips_count,
    round((100 * (cur.median_km / nullif(prev_month.median_km, 0) - 1))::numeric, 2),
    round((100 * (cur.median_km / nullif(january.median_km, 0) - 1))::numeric, 2),
    -- Месяцы без рейсов в ряд не попадают: у редкого направления он короче двенадцати точек.
    mart.sparkline(array_agg(cur.median_km::numeric) over (
        partition by cur.route, cur.body_group, cur.weight_category, cur.distance_band
        order by cur.month
        range between interval '11 months' preceding and current row
    )),
    cur.month = date_trunc('month', params.as_of)::date
        and params.as_of < (cur.month + interval '1 month' - interval '1 day')::date,
    params.as_of
from slices cur
cross join params
-- Календарное сравнение через self-join: LAG при пропуске месяца взял бы последний доступный.
left join slices prev_month
       on prev_month.route = cur.route
      and prev_month.body_group = cur.body_group
      and prev_month.weight_category = cur.weight_category
      and prev_month.distance_band = cur.distance_band
      and prev_month.month = (cur.month - interval '1 month')::date
left join slices january
       on january.route = cur.route
      and january.body_group = cur.body_group
      and january.weight_category = cur.weight_category
      and january.distance_band = cur.distance_band
      and january.month = date_trunc('year', cur.month)::date;
