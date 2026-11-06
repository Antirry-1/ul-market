create table if not exists ops.pipeline_runs (
    run_id           bigint generated always as identity primary key,
    step             text not null,
    window_start     date,
    window_end       date,
    status           text not null check (status in ('running', 'success', 'failed')),
    rows_read        bigint,
    rows_loaded      bigint,
    rows_quarantined bigint,
    started_at       timestamptz not null default now(),
    finished_at      timestamptz,
    details          jsonb not null default '{}'::jsonb
);
