"""Разбор параметров запуска. Модуль не зависит от Airflow, поэтому покрыт обычными тестами."""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta

# Заявки заводятся заранее: на дату снимка в источнике лежат заявки с погрузкой до 110 дней вперёд.
# Окно только за вчерашний день пропустило бы их, и прогноз по ним не обновлялся бы.
# 120 дней покрывают этот горизонт с запасом.
DAILY_HORIZON = timedelta(days=120)
# Задним числом сделки не заводятся, но после погрузки их могут поправить или удалить.
# 35 дней покрывают 30-дневный порог свежести витрины и несколько пропущенных запусков.
DAILY_LOOKBACK = timedelta(days=35)
# Предел для одной задачи DAG. Полная загрузка или обучение идут минуты, ожидание занятого
# хранилища ограничено PIPELINE_LOCK_TIMEOUT в 15 минут. Задача, которая идёт дольше, зависла:
# Airflow её снимает, и следующий шаг закрывает её запись в журнале как прерванную.
TASK_TIMEOUT = timedelta(hours=1)


def load_arguments(conf: Mapping[str, object], interval_start: datetime, interval_end: datetime) -> str:
    """Аргументы команды load: окно из conf, иначе интервал запуска DAG с отступами.

    Плановый запуск грузит [начало интервала - DAILY_LOOKBACK, конец интервала + DAILY_HORIZON).
    Строка подставляется в bash, поэтому даты проходят разбор, а не копируются как есть.
    """
    full = bool(conf.get("full"))
    start = conf.get("start_date")
    end = conf.get("end_date")
    if full and (start or end):
        raise ValueError("full нельзя сочетать со start_date и end_date")
    if full:
        return "--full"
    if bool(start) != bool(end):
        raise ValueError("start_date и end_date задаются только вместе")
    if start:
        return _window(date.fromisoformat(str(start)), date.fromisoformat(str(end)))
    return _window(interval_start.date() - DAILY_LOOKBACK, interval_end.date() + DAILY_HORIZON)


def _window(start: date, end: date) -> str:
    return f"--start-date {start.isoformat()} --end-date {end.isoformat()}"


def retrain_requested(conf: Mapping[str, object]) -> bool:
    return bool(conf.get("retrain"))


# Профиль clickhouse необязателен: без него хоста clickhouse в сети compose нет.
CLICKHOUSE_PING_TIMEOUT = 5


def _ping(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=CLICKHOUSE_PING_TIMEOUT) as response:
            return response.read().strip() == b"Ok."
    except (urllib.error.URLError, OSError):
        return False


def clickhouse_available(env: Mapping[str, str], ping: Callable[[str], bool] = _ping) -> bool:
    """Обновлять ли зеркало: ClickHouse задан в окружении и отвечает на /ping."""
    host = env.get("CLICKHOUSE_HOST")
    port = env.get("CLICKHOUSE_PORT")
    if not (host and port):
        return False
    return ping(f"http://{host}:{port}/ping")
