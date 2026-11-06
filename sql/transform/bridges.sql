-- Группа кузова на сделку: по связям, при их отсутствии по prices.body_type, затем 'Не указан'.
-- Неизвестный код относится к 'Прочие'. union убирает повторы внутри одной группы.
insert into core.bridge_price_body_group (price_id, body_group, _batch_id)
select price_id, body_group, current_setting('app.batch_id')::bigint
from (
    select f.price_id, coalesce(b.body_group, 'Прочие') as body_group
    from core.fact_price f
    join tmp_price_body_types l on l.price_id = f.price_id
    left join core.dim_body_type b on b.body_type_id = l.body_type_id
    union
    select
        f.price_id,
        case when f.body_type is null then 'Не указан' else coalesce(b.body_group, 'Прочие') end
    from core.fact_price f
    join tmp_prices p on p.id = f.price_id
    left join core.dim_body_type b on b.body_type_id = f.body_type
    where not exists (select 1 from tmp_price_body_types l where l.price_id = f.price_id)
) groups;

insert into core.fact_price_date (price_id, price_date, _batch_id)
select d.price_id, d.price_date, current_setting('app.batch_id')::bigint
from tmp_price_dates d
join core.fact_price f on f.price_id = d.price_id;
