-- Справочники приходят целиком: обновляем изменившиеся строки и убираем исчезнувшие.
-- Неизменённая строка не переписывается. Тогда _batch_id и _loaded_at показывают загрузку,
-- которая строку поменяла, а не последнюю, и таблица не копит мёртвые версии строк.

insert into raw.cities as r (id, full_name_local, full_name_gc, gc_id, name, longitude, latitude,
                             created_at, updated_at, _batch_id)
select id, full_name_local, full_name_gc, gc_id, name, longitude, latitude,
       created_at, updated_at, current_setting('app.batch_id')::bigint
from tmp_cities
on conflict (id) do update set
    full_name_local = excluded.full_name_local,
    full_name_gc    = excluded.full_name_gc,
    gc_id           = excluded.gc_id,
    name            = excluded.name,
    longitude       = excluded.longitude,
    latitude        = excluded.latitude,
    created_at      = excluded.created_at,
    updated_at      = excluded.updated_at,
    _batch_id       = excluded._batch_id,
    _loaded_at      = now()
where (r.full_name_local, r.full_name_gc, r.gc_id, r.name, r.longitude, r.latitude,
       r.created_at, r.updated_at)
      is distinct from
      (excluded.full_name_local, excluded.full_name_gc, excluded.gc_id, excluded.name,
       excluded.longitude, excluded.latitude, excluded.created_at, excluded.updated_at);
delete from raw.cities r where not exists (select 1 from tmp_cities t where t.id = r.id);

insert into raw.body_types as r (id, name, "id_from_1C", std_id, _batch_id)
select id, name, "id_from_1C", std_id, current_setting('app.batch_id')::bigint
from tmp_body_types
on conflict (id) do update set
    name         = excluded.name,
    "id_from_1C" = excluded."id_from_1C",
    std_id       = excluded.std_id,
    _batch_id    = excluded._batch_id,
    _loaded_at   = now()
where (r.name, r."id_from_1C", r.std_id)
      is distinct from (excluded.name, excluded."id_from_1C", excluded.std_id);
delete from raw.body_types r where not exists (select 1 from tmp_body_types t where t.id = r.id);

insert into raw.exchange_rates as r (id, num_code, value, setting_date, _batch_id)
select id, num_code, value, setting_date, current_setting('app.batch_id')::bigint
from tmp_exchange_rates
on conflict (id) do update set
    num_code     = excluded.num_code,
    value        = excluded.value,
    setting_date = excluded.setting_date,
    _batch_id    = excluded._batch_id,
    _loaded_at   = now()
where (r.num_code, r.value, r.setting_date)
      is distinct from (excluded.num_code, excluded.value, excluded.setting_date);
delete from raw.exchange_rates r where not exists (select 1 from tmp_exchange_rates t where t.id = r.id);
