FROM apache/superset:6.0.0

USER root

# В образе нет драйвера PostgreSQL.
RUN uv pip install --python /app/.venv/bin/python --no-cache psycopg2-binary==2.9.10

COPY superset/superset_config.py /app/pythonpath/superset_config.py
COPY superset/init.sh superset/import_assets.py /app/assets/
COPY superset/assets/ /app/assets/market/

USER superset
