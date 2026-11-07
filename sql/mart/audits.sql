-- Проверки временной таблицы до публикации: ноль строк значит проверка пройдена.
-- Запрос возвращает ключ ячейки: первые строки попадают в лог как образец нарушений.

-- @audit mart_not_empty error
select 1
where not exists (select 1 from stg_route_card);

-- @audit grain_is_unique error
select month, loading_city_gcid, unloading_city_gcid, body_group, weight_category
from stg_route_card
group by 1, 2, 3, 4, 5
having count(*) > 1;

-- Сводка рынка задаёт направление текстом и сверяется с карточкой по нему. Совпадение
-- по тексту равно совпадению по gc_id, только пока у разных городов разные названия.
-- @audit city_labels_are_unique error
select month, loading_city, unloading_city, body_group, weight_category
from stg_route_card
group by 1, 2, 3, 4, 5
having count(*) > 1;

-- @audit corridor_is_ordered error
select month, loading_city_gcid, unloading_city_gcid, body_group, weight_category
from stg_route_card
where not (p25_trip <= median_trip and median_trip <= p75_trip)
   or not (p25_km <= median_km and median_km <= p75_km)
   or p25_trip <= 0
   or p25_km <= 0;

-- @audit carriers_not_above_trips error
select month, loading_city_gcid, unloading_city_gcid, body_group, weight_category
from stg_route_card
where carriers_count > trips_count or trips_count < 1;

-- @audit no_future_rows error
select month, loading_city_gcid, unloading_city_gcid, body_group, weight_category
from stg_route_card
where last_trip_date > as_of or freshness_days < 0;

-- @audit cv_is_null_for_single_trip error
select month, loading_city_gcid, unloading_city_gcid, body_group, weight_category
from stg_route_card
where (trips_count < 2) <> (cv_rate is null);

-- @audit reliability_matches_rule error
select month, loading_city_gcid, unloading_city_gcid, body_group, weight_category
from stg_route_card
where reliability_level <> case
    when trips_count >= 30 and carriers_count >= 5 and freshness_days <= 30 then 'HIGH'
    when trips_count >= 10 then 'MEDIUM'
    else 'LOW_DATA'
end;

-- @audit mom_requires_previous_month error
select c.month, c.loading_city_gcid, c.unloading_city_gcid, c.body_group, c.weight_category
from stg_route_card c
where c.mom_pct is not null
  and not exists (
      select 1
      from stg_route_card p
      where p.loading_city_gcid = c.loading_city_gcid
        and p.unloading_city_gcid = c.unloading_city_gcid
        and p.body_group = c.body_group
        and p.weight_category = c.weight_category
        and p.month = (c.month - interval '1 month')::date
  );

-- Независимый пересчёт: сумма рейсов по ячейкам равна числу пар сделка, месяц, кузов в core.
-- @audit trips_reconcile_with_core error
select mart.trips as mart_trips, core_side.trips as core_trips
from (select sum(trips_count) as trips from stg_route_card) mart
cross join (
    select count(*) as trips
    from (
        select distinct f.price_id, date_trunc('month', d.price_date), b.body_group
        from core.fact_price f
        join core.fact_price_date d on d.price_id = f.price_id
        join core.bridge_price_body_group b on b.price_id = f.price_id
        where f.is_fact
          and f.price_type in (1, 2, 3, 4, 5, 9)
          and not f.complex
          and not f.outlier_mark
          and f.price_rub > 0
          and d.price_date <= current_setting('app.as_of')::date
    ) cells
) core_side
where mart.trips <> core_side.trips;
