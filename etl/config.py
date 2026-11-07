"""Настройки из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from psycopg.conninfo import make_conninfo


DEFAULT_SOURCE_SHRINK_LIMIT = 0.05


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name}")
    return value


def _share(name: str, default: float) -> float:
    value = os.environ.get(name)
    if not value:
        return default
    share = float(value)
    if not 0 <= share <= 1:
        raise RuntimeError(f"{name} должна быть долей от 0 до 1, получено {value}")
    return share


@dataclass(frozen=True)
class Settings:
    source_dsn: str
    dwh_dsn: str
    as_of: date
    model_dir: Path
    ml_seed: int
    ml_threads: int
    # Какую долю строк окна или справочника источник может потерять за одну загрузку.
    # Больше похоже на неполный снимок, чем на настоящие удаления, и загрузка останавливается.
    source_shrink_limit: float = DEFAULT_SOURCE_SHRINK_LIMIT

    @classmethod
    def from_env(cls) -> "Settings":
        host = _required("DB_HOST")
        port = _required("DB_PORT")
        return cls(
            source_dsn=make_conninfo(
                host=host,
                port=port,
                dbname=_required("SOURCE_DB"),
                user=_required("SOURCE_USER"),
                password=_required("SOURCE_PASSWORD"),
            ),
            dwh_dsn=make_conninfo(
                host=host,
                port=port,
                dbname=_required("DWH_DB"),
                user=_required("DWH_USER"),
                password=_required("DWH_PASSWORD"),
            ),
            as_of=date.fromisoformat(_required("AS_OF")),
            model_dir=Path(_required("MODEL_DIR")),
            ml_seed=int(_required("ML_SEED")),
            ml_threads=int(_required("ML_THREADS")),
            source_shrink_limit=_share("SOURCE_SHRINK_LIMIT", DEFAULT_SOURCE_SHRINK_LIMIT),
        )
