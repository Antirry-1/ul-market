-- Копия источника без бизнес-правил и внешних ключей.
-- price_group_id и price_group_stats не загружаются: в них агрегаты по целевой цене.

create table if not exists raw.prices (
    id                  bigint primary key,
    created_at          timestamp(0),
    updated_at          timestamp(0),
    subscriber_id       integer,
    organization_id     integer,
    loading_date        date not null,
    loading_city_gcid   integer,
    unloading_city_gcid integer,
    distance            double precision,
    total_weight        double precision,
    total_volume        double precision,
    body_type           integer,
    price               double precision,
    currency_code       varchar(3),
    complex             boolean,
    price_type          smallint,
    item_id             integer,
    is_fact             boolean,
    nearest_rate_date   date,
    meta_item_id        bigint,
    end_loading_date    date,
    source              varchar(255),
    external_item_uuid  uuid,
    outlier_mark        boolean,
    weight_category     varchar(4),
    volume_category     varchar(4),
    _batch_id           bigint not null,
    _loaded_at          timestamptz not null default now()
);
create index if not exists raw_prices_loading_date_idx on raw.prices (loading_date);

create table if not exists raw.price_dates (
    id         bigint not null,
    price_id   bigint not null,
    price_date date not null,
    _batch_id  bigint not null,
    _loaded_at timestamptz not null default now(),
    primary key (price_id, price_date)
);

create table if not exists raw.price_body_types (
    id           bigint not null,
    price_id     bigint not null,
    body_type_id integer not null,
    _batch_id    bigint not null,
    _loaded_at   timestamptz not null default now(),
    primary key (price_id, body_type_id)
);

create table if not exists raw.cities (
    id              bigint primary key,
    full_name_local varchar(255),
    full_name_gc    varchar(255),
    gc_id           integer not null,
    name            varchar(255),
    longitude       double precision,
    latitude        double precision,
    created_at      timestamp(0),
    updated_at      timestamp(0),
    _batch_id       bigint not null,
    _loaded_at      timestamptz not null default now()
);
create index if not exists raw_cities_gc_id_idx on raw.cities (gc_id);

create table if not exists raw.body_types (
    id           bigint primary key,
    name         varchar(255),
    "id_from_1C" integer,
    std_id       integer,
    _batch_id    bigint not null,
    _loaded_at   timestamptz not null default now()
);

create table if not exists raw.exchange_rates (
    id           bigint primary key,
    num_code     varchar(255) not null,
    value        double precision not null,
    setting_date date not null,
    _batch_id    bigint not null,
    _loaded_at   timestamptz not null default now()
);
create index if not exists raw_exchange_rates_code_date_idx on raw.exchange_rates (num_code, setting_date);
