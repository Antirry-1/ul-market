-- Проверки сводки рынка до публикации: ноль строк значит проверка пройдена.
-- Запрос возвращает ключ среза: первые строки попадают в лог как образец нарушений.

-- @audit summary_not_empty error
select 1
where not exists (select 1 from stg_market_summary);

-- @audit summary_corridor_is_ordered error
select month, route, body_group, weight_category, distance_band
from stg_market_summary
where not (p25_km <= median_km and median_km <= p75_km)
   or p25_km <= 0
   or trips_count < 1;

-- Срез по направлению, кузову и весу обязан совпасть с ячейкой карточки маршрута, в обе стороны.
-- Соединение по тексту направления точное: city_labels_are_unique в mart/audits.sql.
-- @audit summary_matches_route_card error
select coalesce(s.month, c.month) as month, coalesce(s.route, c.route) as route,
       coalesce(s.body_group, c.body_group) as body_group,
       coalesce(s.weight_category, c.weight_category) as weight_category
from (
    select *
    from stg_market_summary
    where route <> 'Все' and body_group <> 'Все' and weight_category <> 'Все' and distance_band = 'Все'
) s
full join (
    select *, loading_city || ' → ' || unloading_city as route
    from stg_route_card
) c
  on c.month = s.month
 and c.route = s.route
 and c.body_group = s.body_group
 and c.weight_category = s.weight_category
where s.month is null
   or c.month is null
   or (s.p25_km, s.median_km, s.p75_km, s.median_trip, s.trips_count)
      is distinct from (c.p25_km, c.median_km, c.p75_km, c.median_trip, c.trips_count);

-- Независимый пересчёт: в срезе «Все» рейс считается один раз, даже если у сделки две группы кузова.
-- @audit summary_counts_each_deal_once error
select summary.trips as summary_trips, core_side.trips as core_trips
from (
    select sum(trips_count) as trips
    from stg_market_summary
    where route = 'Все' and body_group = 'Все' and weight_category = 'Все' and distance_band = 'Все'
) summary
cross join (
    select count(*) as trips
    from (
        select distinct f.price_id, date_trunc('month', d.price_date)
        from core.fact_price f
        join core.fact_price_date d on d.price_id = f.price_id
        join core.bridge_price_body_group b on b.price_id = f.price_id
        where f.is_fact
          and f.price_type in (1, 2, 3, 4, 5, 9)
          and not f.complex
          and not f.outlier_mark
          and f.price_rub > 0
          and d.price_date <= current_setting('app.as_of')::date
    ) deals
) core_side
where summary.trips <> core_side.trips;

-- @audit summary_mom_requires_previous_month error
select c.month, c.route, c.body_group, c.weight_category, c.distance_band
from stg_market_summary c
where c.mom_km_pct is not null
  and not exists (
      select 1
      from stg_market_summary p
      where p.route = c.route
        and p.body_group = c.body_group
        and p.weight_category = c.weight_category
        and p.distance_band = c.distance_band
        and p.month = (c.month - interval '1 month')::date
  );
