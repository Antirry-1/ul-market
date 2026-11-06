-- Для RUB курс не нужен: пустая nearest_rate_date у рублёвой сделки не является ошибкой.
-- RUB_PRICE_TOO_LOW только для положительных цен, иначе цена 0 получила бы два кода.
-- Верхние границы держат значения в разрядности витрины: иначе одна сделка уронит всю сборку.
insert into dq.dq_quarantine_prices (price_id, reason_code, loading_date, raw_row, _batch_id)
select
    p.id,
    rule.reason_code,
    p.loading_date,
    to_jsonb(p),
    current_setting('app.batch_id')::bigint
from tmp_prices p
left join core.v_fx_rate fx
       on fx.num_code = p.currency_code
      and fx.setting_date = p.nearest_rate_date
left join (select distinct gc_id from raw.cities) loading_city
       on loading_city.gc_id = p.loading_city_gcid
left join (select distinct gc_id from raw.cities) unloading_city
       on unloading_city.gc_id = p.unloading_city_gcid
cross join lateral (
    select p.price * case when p.currency_code = '643' then 1 else fx.value end as price_rub
) converted
cross join lateral (values
    ('PRICE_NON_POSITIVE',       p.price <= 0),
    ('RUB_PRICE_TOO_LOW',        p.currency_code = '643' and p.price > 0 and p.price <= 1000),
    ('DISTANCE_NON_POSITIVE',    p.distance <= 0),
    ('WEIGHT_NEGATIVE',          p.total_weight < 0),
    ('VOLUME_NEGATIVE',          p.total_volume < 0),
    ('VALUE_NOT_FINITE',         not (core.is_finite(p.price) and core.is_finite(p.distance)
                                      and core.is_finite(p.total_weight) and core.is_finite(p.total_volume))),
    ('LOADING_CITY_NOT_FOUND',   loading_city.gc_id is null),
    ('UNLOADING_CITY_NOT_FOUND', unloading_city.gc_id is null),
    ('FX_RATE_MISSING',          p.currency_code <> '643' and fx.value is null),
    ('FX_RATE_INVALID',          p.currency_code <> '643' and (fx.value <= 0 or not core.is_finite(fx.value))),
    ('PRICE_RUB_OUT_OF_RANGE',   core.is_finite(converted.price_rub) and core.is_finite(p.distance) and (
                                     converted.price_rub >= 1e9
                                     or converted.price_rub / nullif(p.distance, 0) >= 1e9
                                     or (p.currency_code <> '643'
                                         and converted.price_rub > 0 and converted.price_rub < 1)))
) as rule (reason_code, failed)
where rule.failed;
