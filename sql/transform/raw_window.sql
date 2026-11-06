-- Затронутые сделки: всё, что лежит в окне, плюс всё, что пришло из источника.
-- Второе условие нужно для сделки, у которой изменилась loading_date и она сменила окно.
create temp table tmp_affected on commit drop as
select id as price_id
from raw.prices
where loading_date >= current_setting('app.window_start')::date
  and loading_date <  current_setting('app.window_end')::date
union
select id from tmp_prices;

analyze tmp_affected;

delete from raw.price_dates      where price_id in (select price_id from tmp_affected);
delete from raw.price_body_types where price_id in (select price_id from tmp_affected);
delete from raw.prices           where id       in (select price_id from tmp_affected);

insert into raw.prices (id, created_at, updated_at, subscriber_id, organization_id, loading_date,
                        loading_city_gcid, unloading_city_gcid, distance, total_weight, total_volume,
                        body_type, price, currency_code, complex, price_type, item_id, is_fact,
                        nearest_rate_date, meta_item_id, end_loading_date, source, external_item_uuid,
                        outlier_mark, weight_category, volume_category, _batch_id)
select id, created_at, updated_at, subscriber_id, organization_id, loading_date,
       loading_city_gcid, unloading_city_gcid, distance, total_weight, total_volume,
       body_type, price, currency_code, complex, price_type, item_id, is_fact,
       nearest_rate_date, meta_item_id, end_loading_date, source, external_item_uuid,
       outlier_mark, weight_category, volume_category, current_setting('app.batch_id')::bigint
from tmp_prices;

insert into raw.price_dates (id, price_id, price_date, _batch_id)
select id, price_id, price_date, current_setting('app.batch_id')::bigint
from tmp_price_dates;

insert into raw.price_body_types (id, price_id, body_type_id, _batch_id)
select id, price_id, body_type_id, current_setting('app.batch_id')::bigint
from tmp_price_body_types;
