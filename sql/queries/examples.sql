-- Примеры проверки витрин.
-- docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$DWH_DB"' < sql/queries/examples.sql

\echo 'Ячейки с надёжностью HIGH и MEDIUM за сентябрь 2026'
select loading_city, unloading_city, body_group, weight_category,
       p25_trip, median_trip, p75_trip, median_km, trips_count, carriers_count, mom_pct, yoy_pct
from mart.v_route_network
where month = date '2026-09-01'
  and reliability_level in ('HIGH', 'MEDIUM')
order by trips_count desc
limit 10;

\echo 'История коридора: Москва -> Ростов-на-Дону, Тент, FTL'
select month, p25_trip, median_trip, p75_trip, trips_count, carriers_count,
       mom_pct, yoy_pct, reliability_level, is_partial_month
from mart.v_route_history
where loading_city_gcid = 1001
  and unloading_city_gcid = 1008
  and body_group = 'Тент'
  and weight_category = 'FTL'
order by month desc
limit 13;

\echo 'Распределение ячеек по надёжности'
select reliability_level, count(*) as cells, sum(trips_count) as trips
from mart.mart_route_card
group by reliability_level
order by cells desc;

\echo 'Дубли зерна витрины: ожидается 0'
select count(*) as duplicates
from (
    select 1
    from mart.mart_route_card
    group by month, loading_city_gcid, unloading_city_gcid, body_group, weight_category
    having count(*) > 1
) d;

\echo 'Карантин по причинам'
select reason_code, count(*) as deals
from dq.dq_quarantine_prices
group by reason_code
order by deals desc;

\echo 'Каждая сделка источника либо в core, либо в карантине'
select
    (select count(*) from raw.prices) as raw_deals,
    (select count(*) from core.fact_price) as core_deals,
    (select count(distinct price_id) from dq.dq_quarantine_prices) as quarantined_deals;

\echo 'Метрики моделей на test, строки без размеченных выбросов'
select model_name, n_rows, round(r2::numeric, 4) as r2, round(mape_pct::numeric, 2) as mape_pct,
       round(rmse_rub::numeric) as rmse_rub, round(mae_rub::numeric) as mae_rub
from mart.ml_model_metrics
where split = 'test' and slice = 'no_outliers'
order by mape_pct;

\echo 'Факт vs Прогноз: рейсы существенно выше прогноза'
select price_id, loading_date, loading_city, unloading_city, body_group,
       actual_price_rub, predicted_price_rub, deviation_pct, p75_trip, reliability_level
from mart.v_fact_vs_forecast
where split = 'test'
  and is_above_forecast
  and not outlier_mark
order by deviation_pct desc
limit 10;

\echo 'Последние запуски'
select run_id, step, window_start, window_end, status, rows_read, rows_loaded, rows_quarantined,
       finished_at - started_at as duration
from ops.pipeline_runs
order by run_id desc
limit 8;
