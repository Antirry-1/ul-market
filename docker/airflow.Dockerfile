FROM apache/airflow:2.10.5-python3.11

USER root

# Конвейер живёт в своём окружении: его версии numpy и pandas не должны спорить с ограничениями Airflow.
COPY requirements.txt requirements.lock /opt/pipeline/
RUN python -m venv /opt/pipeline-venv \
    && PIP_USER=false /opt/pipeline-venv/bin/pip install --no-cache-dir \
       -r /opt/pipeline/requirements.txt -c /opt/pipeline/requirements.lock

COPY sql/ /opt/pipeline/sql/
COPY etl/ /opt/pipeline/etl/
COPY ml/ /opt/pipeline/ml/
COPY docker/airflow_db_url.py docker/airflow_trigger.sh /opt/pipeline/docker/
COPY dags/ /opt/airflow/dags/

USER airflow
