-- Сверка снимка источника с тем, что уже лежит в raw. Идёт до изменений в той же транзакции.
-- Каждая строка неполного снимка проходит правила DQ, поэтому ловить его можно только по объёму.
-- app.shrink_limit - доля строк, которую источник может потерять за загрузку (SOURCE_SHRINK_LIMIT).

-- Без городов все сделки окна ушли бы в карантин, без курсов - все валютные,
-- без кузовов все сделки попали бы в группу 'Прочие'.
-- @audit source_reference_empty error
select 'cities' where not exists (select 1 from tmp_cities)
union all
select 'body_types' where not exists (select 1 from tmp_body_types)
union all
select 'exchange_rates' where not exists (select 1 from tmp_exchange_rates);

-- Справочник приходит целиком, и исчезнувшие строки удаляются из raw.
-- Нарушения - все исчезнувшие строки справочника, если их доля выше разрешённой.
-- @audit source_reference_shrank error
with gone as (
    select 'cities' as reference, r.id
    from raw.cities r
    where not exists (select 1 from tmp_cities t where t.id = r.id)
    union all
    select 'body_types', r.id
    from raw.body_types r
    where not exists (select 1 from tmp_body_types t where t.id = r.id)
    union all
    select 'exchange_rates', r.id
    from raw.exchange_rates r
    where not exists (select 1 from tmp_exchange_rates t where t.id = r.id)
),
totals as (
    select 'cities' as reference, count(*) as total from raw.cities
    union all
    select 'body_types', count(*) from raw.body_types
    union all
    select 'exchange_rates', count(*) from raw.exchange_rates
),
-- Число пропавших считается один раз на справочник. Подзапрос в where считал бы его
-- заново на каждую пропавшую строку, то есть квадратично как раз при массовой потере.
gone_counts as (
    select reference, count(*) as gone
    from gone
    group by reference
)
select g.reference, g.id
from gone g
join gone_counts c on c.reference = g.reference
join totals t on t.reference = g.reference
where c.gone > t.total * current_setting('app.shrink_limit')::numeric;

-- Сделки окна, которых нет в снимке, то есть удалённые в источнике: перенесённые за пределы
-- окна extract дочитывает по id. Поштучно это обычное дело, их считает аудит deal_left_window.
-- Массовая пропажа похожа на сбой источника: без проверки окно было бы стёрто из raw и core.
-- @audit source_window_shrank error
-- left join, а не not exists в списке колонок: там подзапрос шёл бы на каждую строку окна.
with window_deals as (
    select r.id, p.id is null as gone
    from raw.prices r
    left join tmp_prices p on p.id = r.id
    where r.loading_date >= current_setting('app.window_start')::date
      and r.loading_date <  current_setting('app.window_end')::date
),
totals as (
    select count(*) as total, count(*) filter (where gone) as gone
    from window_deals
)
select w.id
from window_deals w
cross join totals t
where w.gone
  and t.gone >= current_setting('app.shrink_min_deals')::int
  and t.gone > t.total * current_setting('app.shrink_limit')::numeric;
