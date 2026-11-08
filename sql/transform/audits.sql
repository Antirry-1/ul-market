-- Каждый запрос возвращает нарушения: ноль строк значит проверка пройдена.
-- error откатывает загрузку окна, warn только попадает в журнал.

-- @audit every_row_routed error
select p.id
from tmp_prices p
left join core.fact_price f on f.price_id = p.id
left join (select distinct price_id from dq.dq_quarantine_prices) q on q.price_id = p.id
where (f.price_id is null) = (q.price_id is null);

-- @audit every_fact_has_body_group error
select f.price_id
from core.fact_price f
join tmp_prices p on p.id = f.price_id
where not exists (select 1 from core.bridge_price_body_group b where b.price_id = f.price_id);

-- Сделка была в окне, но источник её больше не отдаёт: её удалили.
-- Сделку, перенесённую за пределы окна, extract дочитывает по id, поэтому сюда она не попадает.
-- @audit deal_left_window warn
select a.price_id
from tmp_affected a
where not exists (select 1 from tmp_prices p where p.id = a.price_id);

-- @audit fact_without_price_dates warn
select f.price_id
from core.fact_price f
join tmp_prices p on p.id = f.price_id
where not exists (select 1 from core.fact_price_date d where d.price_id = f.price_id);

-- @audit source_category_differs_from_rule warn
select p.id
from tmp_prices p
where nullif(btrim(p.weight_category), '') <> core.weight_category(p.total_weight)
   or nullif(btrim(p.volume_category), '') <> core.volume_category(p.total_volume);

-- Дата курса в источнике: последний курс не позже created_at, иначе ближайший следующий.
-- Проверка только сообщает о расхождении, сохранённая дата не меняется.
-- @audit nearest_rate_date_differs_from_rule warn
select p.id
from tmp_prices p
cross join lateral (
    select coalesce(
        (select max(r.setting_date) from raw.exchange_rates r
         where r.num_code = p.currency_code and r.setting_date <= p.created_at),
        (select min(r.setting_date) from raw.exchange_rates r
         where r.num_code = p.currency_code and r.setting_date > p.created_at)
    ) as expected
) e
where p.nearest_rate_date is not null
  and p.nearest_rate_date is distinct from e.expected;

-- @audit is_fact_differs_from_price_type warn
select p.id
from tmp_prices p
where p.is_fact is distinct from (p.price_type in (1, 2, 3, 4, 5, 9));

-- @audit duplicate_exchange_rates warn
select num_code, setting_date
from raw.exchange_rates
group by num_code, setting_date
having count(*) > 1;

-- @audit unknown_body_type_codes warn
select l.body_type_id
from tmp_price_body_types l
where not exists (select 1 from core.dim_body_type b where b.body_type_id = l.body_type_id);
