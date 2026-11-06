"""Настройки из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from psycopg.conninfo import make_conninfo


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name}")
    return value


@dataclass(frozen=True)
class Settings:
    source_dsn: str
    dwh_dsn: str
    as_of: date
    model_dir: Path
    ml_seed: int
    ml_threads: int

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
        )
