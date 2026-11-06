FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# requirements.lock закрепляет и косвенные зависимости: новая версия не должна менять результат.
COPY requirements.txt requirements.lock ./
RUN pip install -r requirements.txt -c requirements.lock

RUN useradd --create-home --uid 1000 app \
    && mkdir /artifacts \
    && chown app:app /artifacts

COPY sql/ sql/
COPY etl/ etl/
COPY ml/ ml/
COPY tests/ tests/
COPY pytest.ini .

USER app

CMD ["python", "-m", "etl.cli", "run-all"]
