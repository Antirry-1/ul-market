"""Печатает строку подключения к метаданным: пароль задаётся один раз и кодируется для URL."""

import os
from urllib.parse import quote

password = quote(os.environ["AIRFLOW_DB_PASSWORD"], safe="")
host = os.environ["METADATA_DB_HOST"]
port = os.environ["METADATA_DB_PORT"]
# Без перевода строки: Airflow использует вывод команды как есть, и он попал бы в имя базы.
print(f"postgresql+psycopg2://airflow:{password}@{host}:{port}/airflow", end="")
