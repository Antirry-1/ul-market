-- FINAL сводит возможные дубли ключа до слияния частей.

-- Самые плотные маршруты за месяц
select loading_city, unloading_city, body_group, weight_category,
       p25_trip, median_trip, p75_trip, median_km, trips_count, carriers_count, reliability_level
from mart_route_card final
where month = '2026-09-01'
  and reliability_level != 'LOW_DATA'
order by trips_count desc, loading_city, unloading_city, body_group, weight_category
limit 10;

-- История коридора одной ячейки
select month, p25_trip, median_trip, p75_trip, trips_count, mom_pct, yoy_pct, reliability_level
from mart_route_card final
where loading_city = 'Москва'
  and unloading_city = 'Ростов-на-Дону'
  and body_group = 'Тент'
  and weight_category = 'FTL'
order by month desc
limit 13;

-- Ячейки по надёжности за каждый месяц
select month, reliability_level, count() as cells
from mart_route_card final
group by month, reliability_level
order by month desc, reliability_level;
