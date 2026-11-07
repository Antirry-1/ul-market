#!/usr/bin/env bash
# Выполняется один раз, при создании тома метаданных.
set -euo pipefail

psql -X -q -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v airflow_password="$AIRFLOW_DB_PASSWORD" -v superset_password="$SUPERSET_DB_PASSWORD" <<'SQL'
select format('create role airflow login password %L', :'airflow_password') \gexec
select format('create role superset login password %L', :'superset_password') \gexec
create database airflow owner airflow;
create database superset owner superset;
SQL
