-- Сводная таблица маршрутной сети: фильтруется по month и reliability_level.
-- Новые колонки только в конец: create or replace view не умеет менять их порядок.
create or replace view mart.v_route_network as
select
    month,
    loading_city,
    unloading_city,
    body_group,
    weight_category,
    p25_trip,
    median_trip,
    p75_trip,
    p25_km,
    median_km,
    p75_km,
    trips_count,
    carriers_count,
    mom_pct,
    yoy_pct,
    reliability_level,
    is_partial_month,
    avg_trip,
    cv_rate
from mart.mart_route_card;

-- История одной ячейки: фильтруется по маршруту, кузову и весу, сортируется по month.
create or replace view mart.v_route_history as
select
    loading_city_gcid,
    unloading_city_gcid,
    loading_city,
    unloading_city,
    body_group,
    weight_category,
    month,
    p25_trip,
    median_trip,
    p75_trip,
    avg_trip,
    p25_km,
    median_km,
    p75_km,
    trips_count,
    carriers_count,
    cv_rate,
    mom_pct,
    yoy_pct,
    reliability_level,
    last_trip_date,
    freshness_days,
    is_partial_month
from mart.mart_route_card;

-- Факт рядом с прогнозом и рыночным коридором ячейки.
-- Порог 30 % примерно равен 1,6 стандартного отклонения шума цены: его превышают около 6 % рейсов.
-- Сделку test сравниваем с коридором месяца погрузки. Для будущей заявки месяца погрузки
-- в витрине ещё нет, и она получает коридор последнего месяца своей ячейки не позже месяца
-- погрузки. Этот месяц показан в corridor_month, давность видна по нему и по reliability_level.
create or replace view mart.v_fact_vs_forecast as
select
    p.price_id,
    p.loading_date,
    p.split,
    p.model_name,
    origin.city_label as loading_city,
    destination.city_label as unloading_city,
    p.body_group,
    f.weight_category,
    p.actual_price_rub,
    p.predicted_price_rub,
    p.abs_error_rub,
    p.ape_pct,
    round(100 * (p.actual_price_rub / nullif(p.predicted_price_rub, 0) - 1), 2) as deviation_pct,
    p.actual_price_rub > p.predicted_price_rub * 1.3 as is_above_forecast,
    c.p25_trip,
    c.median_trip,
    c.p75_trip,
    c.trips_count,
    c.reliability_level,
    p.actual_price_rub > c.p75_trip as is_above_corridor,
    p.outlier_mark,
    p.scored_at,
    p.model_release,
    c.month as corridor_month,
    p.trained_through
from mart.ml_price_predictions p
join core.fact_price f on f.price_id = p.price_id
join core.v_city_label origin on origin.gc_id = p.loading_city_gcid
join core.v_city_label destination on destination.gc_id = p.unloading_city_gcid
-- Первичный ключ витрины начинается с ячейки и кончается месяцем: на строку один короткий проход индекса.
left join lateral (
    select c.month, c.p25_trip, c.median_trip, c.p75_trip, c.trips_count, c.reliability_level
    from mart.mart_route_card c
    where c.loading_city_gcid = p.loading_city_gcid
      and c.unloading_city_gcid = p.unloading_city_gcid
      and c.body_group = p.body_group
      and c.weight_category = f.weight_category
      and c.month <= date_trunc('month', p.loading_date)::date
      and (p.split = 'future' or c.month = date_trunc('month', p.loading_date)::date)
    order by c.month desc
    limit 1
) c on true;
