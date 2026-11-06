#!/usr/bin/env bash
# Создаёт роли и базы, затем заполняет источник.
# Заполненный источник не трогает: schema.sql и synthetic.sql не идемпотентны.
set -euo pipefail

export PGHOST="$DB_HOST" PGPORT="$DB_PORT" PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD"

admin() {
  psql -X -q -v ON_ERROR_STOP=1 "$@"
}

admin -d postgres \
  -v source_db="$SOURCE_DB" -v source_user="$SOURCE_USER" -v source_password="$SOURCE_PASSWORD" \
  -v dwh_db="$DWH_DB" -v dwh_user="$DWH_USER" -v dwh_password="$DWH_PASSWORD" \
  -v bi_password="$BI_PASSWORD" <<'SQL'
select format('create role %I login', r)
from unnest(array[:'source_user', :'dwh_user', 'bi_reader']) as r
where not exists (select 1 from pg_roles where rolname = r)
\gexec

select format('alter role %I password %L', :'source_user', :'source_password') \gexec
select format('alter role %I password %L', :'dwh_user', :'dwh_password') \gexec
select format('alter role bi_reader password %L', :'bi_password') \gexec
select format('alter role %I createdb', :'dwh_user') \gexec
select format('alter role %I set default_transaction_read_only = on', :'source_user') \gexec
alter role bi_reader set default_transaction_read_only = on;

select format('create database %I', :'source_db')
where not exists (select 1 from pg_database where datname = :'source_db')
\gexec

select format('create database %I owner %I', :'dwh_db', :'dwh_user')
where not exists (select 1 from pg_database where datname = :'dwh_db')
\gexec

select format('revoke all on database %I from public', :'source_db') \gexec
select format('revoke all on database %I from public', :'dwh_db') \gexec
select format('grant connect on database %I to %I', :'source_db', :'source_user') \gexec
select format('grant connect on database %I to bi_reader', :'dwh_db') \gexec
SQL

seeded=$(admin -d "$SOURCE_DB" -Atc "select to_regclass('public.prices') is not null")

if [ "$seeded" = "t" ]; then
  echo "source-seed: источник уже заполнен, пропускаю генерацию"
else
  echo "source-seed: генерация источника, n_prices=$N_PRICES"
  # Одна транзакция: при сбое в базе не останется половины данных.
  admin -1 -d "$SOURCE_DB" -v n_prices="$N_PRICES" \
    -f /seed/sql/schema.sql -f /seed/sql/synthetic.sql > /dev/null
fi

# price_group_stats и остальные служебные таблицы роли чтения недоступны.
admin -d "$SOURCE_DB" -v source_user="$SOURCE_USER" <<'SQL'
select format('grant usage on schema public to %I', :'source_user') \gexec
select format(
  'grant select on prices, price_dates, price_body_types, cities, body_types, exchange_rates to %I',
  :'source_user'
) \gexec
SQL

rows=$(admin -d "$SOURCE_DB" -Atc "select count(*) from prices")
echo "source-seed: готово, prices=$rows"
