-- Сумма в рублях фиксируется при загрузке: курс не пересчитывается задним числом.
-- primary_body_group нужна модели как одно значение на сделку, витрина использует мост.
-- Группа берётся из связей сделки, как в мосте: иначе прогноз не найдёт коридор своей ячейки.
insert into core.fact_price (
    price_id, created_at, loading_date, end_loading_date, loading_city_gcid, unloading_city_gcid,
    distance, total_weight, total_volume, weight_category, volume_category, body_type,
    primary_body_group, price, currency_code, rate_value, price_rub, price_type,
    is_fact, complex, outlier_mark, organization_id, _batch_id
)
select
    p.id,
    p.created_at,
    p.loading_date,
    p.end_loading_date,
    p.loading_city_gcid,
    p.unloading_city_gcid,
    p.distance,
    p.total_weight,
    p.total_volume,
    coalesce(nullif(btrim(p.weight_category), ''), core.weight_category(p.total_weight)),
    coalesce(nullif(btrim(p.volume_category), ''), core.volume_category(p.total_volume)),
    p.body_type,
    case
        when links.groups is null and p.body_type is null then 'Не указан'
        when links.groups is null or coalesce(own_type.body_group, 'Прочие') = any (links.groups)
            then coalesce(own_type.body_group, 'Прочие')
        else links.groups[1]
    end,
    p.price,
    p.currency_code,
    case when p.currency_code = '643' then 1 else fx.value end,
    round((p.price * case when p.currency_code = '643' then 1 else fx.value end)::numeric, 2),
    p.price_type,
    p.is_fact,
    p.complex,
    p.outlier_mark,
    p.organization_id,
    current_setting('app.batch_id')::bigint
from tmp_prices p
left join core.v_fx_rate fx
       on fx.num_code = p.currency_code
      and fx.setting_date = p.nearest_rate_date
left join core.dim_body_type own_type
       on own_type.body_type_id = p.body_type
left join (
    select l.price_id, array_agg(coalesce(b.body_group, 'Прочие') order by l.body_type_id) as groups
    from tmp_price_body_types l
    left join core.dim_body_type b on b.body_type_id = l.body_type_id
    group by l.price_id
) links on links.price_id = p.id
where not exists (
    select 1 from dq.dq_quarantine_prices q where q.price_id = p.id
);
