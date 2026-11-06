"""Запись метрик в хранилище и файлы модели."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg

MAIN_MODEL = "catboost_price_v1"
RIDGE_MODEL = "ridge_v1"
BASELINE_MODEL = "baseline_rate_per_km_v1"

CATBOOST_FILE = "catboost.cbm"
RIDGE_FILE = "ridge.joblib"
BASELINE_FILE = "baseline.joblib"
META_FILE = "train_meta.json"
CURRENT_LINK = "current"


@contextmanager
def new_release(model_dir: Path) -> Iterator[Path]:
    """Каталог новой версии модели. Файлы связаны между собой, поэтому меняются только вместе.

    Подмена символьной ссылки атомарна: читатель видит либо прошлый набор, либо новый.
    """
    model_dir.mkdir(parents=True, exist_ok=True)
    release = Path(tempfile.mkdtemp(prefix="release-", dir=model_dir))
    try:
        yield release
    except BaseException:
        shutil.rmtree(release, ignore_errors=True)
        raise

    current = model_dir / CURRENT_LINK
    previous = current.resolve() if current.exists() else None
    pending = model_dir / f"{CURRENT_LINK}.pending"
    pending.unlink(missing_ok=True)
    pending.symlink_to(release.name)
    os.replace(pending, current)
    if previous is not None:
        shutil.rmtree(previous, ignore_errors=True)


def current_release(model_dir: Path) -> Path:
    current = model_dir / CURRENT_LINK
    if not current.exists():
        raise FileNotFoundError(f"В {model_dir} нет обученной модели: сначала запустите ml.train")
    return current.resolve()


def write_metrics(
    conn: psycopg.Connection,
    model_name: str,
    split: str,
    metrics_by_slice: dict[str, dict],
) -> None:
    conn.execute(
        "delete from mart.ml_model_metrics where model_name = %s and split = %s",
        (model_name, split),
    )
    with conn.cursor() as cursor:
        cursor.executemany(
            """
            insert into mart.ml_model_metrics
                (model_name, split, slice, n_rows, r2, mape_pct, rmse_rub, mae_rub, mdape_pct, wape_pct)
            values
                (%(model_name)s, %(split)s, %(slice)s, %(n_rows)s, %(r2)s, %(mape_pct)s,
                 %(rmse_rub)s, %(mae_rub)s, %(mdape_pct)s, %(wape_pct)s)
            """,
            [
                {"model_name": model_name, "split": split, "slice": name, **values}
                for name, values in metrics_by_slice.items()
            ],
        )
