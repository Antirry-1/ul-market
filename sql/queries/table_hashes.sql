-- Контрольные хэши таблиц: число строк и md5 по отсортированным md5 строк.
-- Служебные поля времени и номера батча исключены: они меняются при каждом запуске.
-- docker compose exec -T postgres psql -U postgres -d dwh -qAtX -v ON_ERROR_STOP=1 < sql/queries/table_hashes.sql

select format(
         'select %L || '' '' || count(*) || ''|'' || md5(string_agg(h, '''' order by h)) from (select md5(row(%s)::text) h from %s) x',
         t, string_agg(quote_ident(c.column_name), ',' order by c.ordinal_position), t)
from unnest(array['raw.prices', 'raw.price_dates', 'raw.price_body_types', 'core.dim_city', 'core.dim_body_type',
                  'core.fact_price', 'core.fact_price_date', 'core.bridge_price_body_group',
                  'dq.dq_quarantine_prices', 'mart.mart_route_card', 'mart.ml_price_predictions',
                  'mart.ml_model_metrics']) with ordinality as l(t, n)
join information_schema.columns c
  on c.table_schema = split_part(t, '.', 1)
 and c.table_name = split_part(t, '.', 2)
 and c.column_name not in ('_batch_id', '_loaded_at', 'scored_at', 'quarantined_at')
group by t, n
order by n
\gexec

-- Сводка рынка: строки упорядочены по ключу, служебных полей в ней нет.
select 'mart.mart_market_summary ' || count(*) || '|'
       || md5(string_agg(m::text, ',' order by route, body_group, weight_category, distance_band, month))
from mart.mart_market_summary m;
