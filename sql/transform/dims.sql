-- gc_id в источнике не уникален: остаётся запись с минимальным id.
-- Строки измерений не удаляются, на них ссылаются сделки из других окон.
insert into core.dim_city (gc_id, source_id, city_name, region, latitude, longitude)
select distinct on (gc_id)
    gc_id,
    id,
    btrim(split_part(full_name_local, ',', 1)),
    case
        when strpos(full_name_local, ',') > 0
        then nullif(btrim(substr(full_name_local, strpos(full_name_local, ',') + 1)), '')
    end,
    latitude,
    longitude
from raw.cities
order by gc_id, id
on conflict (gc_id) do update set
    source_id = excluded.source_id,
    city_name = excluded.city_name,
    region    = excluded.region,
    latitude  = excluded.latitude,
    longitude = excluded.longitude;

insert into core.dim_body_type (body_type_id, name, std_id, body_group)
select
    id,
    name,
    std_id,
    case std_id
        when 56 then 'Тент'
        when 4  then 'Рефрижератор'
        when 2  then 'Изотерм'
        when 15 then 'Борт'
        else 'Прочие'
    end
from raw.body_types
on conflict (body_type_id) do update set
    name       = excluded.name,
    std_id     = excluded.std_id,
    body_group = excluded.body_group;
