-- default privileges покрывают таблицы, созданные позже; all tables только существующие.
grant usage on schema mart to bi_reader;
grant select on all tables in schema mart to bi_reader;
alter default privileges in schema mart grant select on tables to bi_reader;
