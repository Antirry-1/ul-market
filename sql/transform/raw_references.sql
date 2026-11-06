-- Справочники приходят целиком: обновляем совпавшие строки и убираем исчезнувшие.

insert into raw.cities (id, full_name_local, full_name_gc, gc_id, name, longitude, latitude,
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
    _loaded_at      = now();
delete from raw.cities r where not exists (select 1 from tmp_cities t where t.id = r.id);

insert into raw.body_types (id, name, "id_from_1C", std_id, _batch_id)
select id, name, "id_from_1C", std_id, current_setting('app.batch_id')::bigint
from tmp_body_types
on conflict (id) do update set
    name         = excluded.name,
    "id_from_1C" = excluded."id_from_1C",
    std_id       = excluded.std_id,
    _batch_id    = excluded._batch_id,
    _loaded_at   = now();
delete from raw.body_types r where not exists (select 1 from tmp_body_types t where t.id = r.id);

insert into raw.exchange_rates (id, num_code, value, setting_date, _batch_id)
select id, num_code, value, setting_date, current_setting('app.batch_id')::bigint
from tmp_exchange_rates
on conflict (id) do update set
    num_code     = excluded.num_code,
    value        = excluded.value,
    setting_date = excluded.setting_date,
    _batch_id    = excluded._batch_id,
    _loaded_at   = now();
delete from raw.exchange_rates r where not exists (select 1 from tmp_exchange_rates t where t.id = r.id);
