-- Одна сделка может иметь несколько причин отбраковки.
create table if not exists dq.dq_quarantine_prices (
    price_id       bigint not null,
    reason_code    text not null,
    loading_date   date not null,
    raw_row        jsonb not null,
    _batch_id      bigint not null,
    quarantined_at timestamptz not null default now(),
    primary key (price_id, reason_code)
);

-- Новый код причины должен дойти и до уже созданной таблицы, поэтому ограничение пересоздаётся.
-- Но только когда набор кодов поменялся: пересоздание берёт эксклюзивную блокировку таблицы
-- и проверяет все её строки, а init-dwh идёт перед каждым запуском DAG.
-- Набор в базе читается из определения ограничения вида ARRAY['КОД'::text, ...].
do $$
declare
    codes constant text[] := array[
        'PRICE_NON_POSITIVE', 'RUB_PRICE_TOO_LOW', 'DISTANCE_NON_POSITIVE',
        'WEIGHT_NEGATIVE', 'VOLUME_NEGATIVE', 'VALUE_NOT_FINITE',
        'LOADING_CITY_NOT_FOUND', 'UNLOADING_CITY_NOT_FOUND',
        'FX_RATE_MISSING', 'FX_RATE_INVALID', 'PRICE_RUB_OUT_OF_RANGE'
    ];
    installed text[];
begin
    select array(
        select match[1]
        from regexp_matches(pg_get_constraintdef(c.oid), '''([A-Z_]+)''', 'g') as match
        order by 1
    )
    into installed
    from pg_constraint c
    where c.conrelid = 'dq.dq_quarantine_prices'::regclass
      and c.conname = 'dq_quarantine_prices_reason_code_check';

    if installed is distinct from array(select code from unnest(codes) as code order by 1) then
        alter table dq.dq_quarantine_prices drop constraint if exists dq_quarantine_prices_reason_code_check;
        execute format(
            'alter table dq.dq_quarantine_prices add constraint dq_quarantine_prices_reason_code_check '
            'check (reason_code in (%s))',
            (select string_agg(quote_literal(code), ', ') from unnest(codes) as code)
        );
    end if;
end
$$;

create index if not exists dq_quarantine_loading_date_idx on dq.dq_quarantine_prices (loading_date);
