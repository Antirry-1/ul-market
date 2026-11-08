"""Запись метрик в хранилище и файлы модели."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg

from ml.split import SplitBounds

MAIN_MODEL = "catboost_price_v1"
RIDGE_MODEL = "ridge_v1"
BASELINE_MODEL = "baseline_rate_per_km_v1"

# Модель по train: по ней считаются метрики test.
CATBOOST_FILE = "catboost.cbm"
RIDGE_FILE = "ridge.joblib"
BASELINE_FILE = "baseline.joblib"
# Та же модель, обученная на всей истории до as_of: по ней строится прогноз будущих заявок.
FORECAST_CATBOOST_FILE = "catboost_forecast.cbm"
FORECAST_RIDGE_FILE = "ridge_forecast.joblib"
META_FILE = "train_meta.json"
CURRENT_LINK = "current"

RELEASE_FILES = (CATBOOST_FILE, RIDGE_FILE, BASELINE_FILE, FORECAST_CATBOOST_FILE, FORECAST_RIDGE_FILE,
                 META_FILE)


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


def check_files(release: Path) -> None:
    """Версия должна содержать все файлы: скоринг берёт из неё обе модели сразу."""
    missing = [name for name in RELEASE_FILES if not (release / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"В версии {release.name} нет файлов {', '.join(missing)}: переобучите модель, python -m ml.train"
        )


def read_meta(release: Path) -> dict:
    """Параметры версии. Поле release связывает прогнозы и метрики с этим набором файлов."""
    return json.loads((release / META_FILE).read_text(encoding="utf-8"))


class SplitMismatch(RuntimeError):
    """Модель обучена на других границах выборок, чем задают текущие настройки."""


def check_split(meta: dict, bounds: SplitBounds) -> None:
    """Сверяет границы, на которых обучена модель, с границами текущего запуска.

    Без сверки после смены AS_OF test мог бы лечь на строки, которые модель видела при обучении,
    и метрики вышли бы завышенными без всякого предупреждения. Версия без записанных границ
    тоже отклоняется: проверить её нечем.
    """
    trained = meta.get("split")
    expected = bounds.as_dict()
    if trained != expected:
        raise SplitMismatch(
            f"Модель {meta['release']} обучена на границах {trained or 'без записанных границ'}, "
            f"а текущий запуск задаёт {expected}. Переобучите модель: python -m ml.train"
        )


class FeaturesMismatch(RuntimeError):
    """Модель обучена на другом наборе признаков, чем строит текущий код."""


def check_features(meta: dict, features: tuple[str, ...]) -> None:
    """Сверяет признаки версии с признаками текущего кода.

    Скоринг подаёт модели колонки по списку из версии. После переименования или удаления
    признака такой колонки в данных уже нет, и запуск падал бы с KeyError на середине.
    """
    trained = meta.get("features")
    if trained != list(features):
        missing = sorted(set(trained or ()) - set(features))
        added = sorted(set(features) - set(trained or ()))
        raise FeaturesMismatch(
            f"Модель {meta['release']} обучена на другом наборе признаков "
            f"(нет в коде: {missing or '-'}, новые: {added or '-'}). "
            "Переобучите модель: python -m ml.train"
        )


def write_metrics(
    conn: psycopg.Connection,
    model_name: str,
    split: str,
    metrics_by_slice: dict[str, dict],
    *,
    model_release: str,
) -> None:
    conn.execute(
        "delete from mart.ml_model_metrics where model_name = %s and split = %s",
        (model_name, split),
    )
    with conn.cursor() as cursor:
        cursor.executemany(
            """
            insert into mart.ml_model_metrics
                (model_name, split, slice, n_rows, r2, mape_pct, rmse_rub, mae_rub, mdape_pct, wape_pct,
                 model_release)
            values
                (%(model_name)s, %(split)s, %(slice)s, %(n_rows)s, %(r2)s, %(mape_pct)s,
                 %(rmse_rub)s, %(mae_rub)s, %(mdape_pct)s, %(wape_pct)s, %(model_release)s)
            """,
            [
                {"model_name": model_name, "split": split, "slice": name, "model_release": model_release,
                 **values}
                for name, values in metrics_by_slice.items()
            ],
        )
