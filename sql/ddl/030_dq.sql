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

-- Ограничение пересоздаётся, чтобы новый код причины дошёл и до уже созданной таблицы.
alter table dq.dq_quarantine_prices drop constraint if exists dq_quarantine_prices_reason_code_check;
alter table dq.dq_quarantine_prices add constraint dq_quarantine_prices_reason_code_check
    check (reason_code in (
        'PRICE_NON_POSITIVE', 'RUB_PRICE_TOO_LOW', 'DISTANCE_NON_POSITIVE',
        'WEIGHT_NEGATIVE', 'VOLUME_NEGATIVE', 'VALUE_NOT_FINITE',
        'LOADING_CITY_NOT_FOUND', 'UNLOADING_CITY_NOT_FOUND',
        'FX_RATE_MISSING', 'FX_RATE_INVALID', 'PRICE_RUB_OUT_OF_RANGE'
    ));

create index if not exists dq_quarantine_loading_date_idx on dq.dq_quarantine_prices (loading_date);
