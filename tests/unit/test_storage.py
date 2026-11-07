import json
from datetime import date

import pytest

from ml.split import SplitBounds
from ml.storage import (
    CURRENT_LINK, FORECAST_CATBOOST_FILE, META_FILE, RELEASE_FILES, SplitMismatch, check_files,
    check_split, current_release, new_release, read_meta,
)


def publish(model_dir, text):
    with new_release(model_dir) as release:
        (release / "model").write_text(text, encoding="utf-8")


def test_new_release_becomes_current(tmp_path):
    publish(tmp_path, "first")
    assert (current_release(tmp_path) / "model").read_text(encoding="utf-8") == "first"


def test_previous_release_is_removed(tmp_path):
    publish(tmp_path, "first")
    publish(tmp_path, "second")
    assert (current_release(tmp_path) / "model").read_text(encoding="utf-8") == "second"
    assert len([path for path in tmp_path.iterdir() if path.name != CURRENT_LINK]) == 1


def test_failed_training_keeps_previous_release(tmp_path):
    publish(tmp_path, "first")
    with pytest.raises(RuntimeError):
        with new_release(tmp_path) as release:
            (release / "model").write_text("half", encoding="utf-8")
            raise RuntimeError("сбой")
    assert (current_release(tmp_path) / "model").read_text(encoding="utf-8") == "first"
    assert len([path for path in tmp_path.iterdir() if path.name != CURRENT_LINK]) == 1


def test_missing_model_gives_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="ml.train"):
        current_release(tmp_path)


def test_meta_release_comes_from_file(tmp_path):
    with new_release(tmp_path) as release:
        (release / META_FILE).write_text(json.dumps({"release": release.name, "params": {}}), encoding="utf-8")
    meta = read_meta(current_release(tmp_path))
    assert meta["release"] == current_release(tmp_path).name


def test_release_without_forecast_model_is_rejected(tmp_path):
    with new_release(tmp_path) as release:
        for name in RELEASE_FILES:
            if name != FORECAST_CATBOOST_FILE:
                (release / name).write_text("", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match=f"нет файлов {FORECAST_CATBOOST_FILE}: .*ml.train"):
        check_files(current_release(tmp_path))


def test_complete_release_passes_file_check(tmp_path):
    with new_release(tmp_path) as release:
        for name in RELEASE_FILES:
            (release / name).write_text("", encoding="utf-8")
    check_files(current_release(tmp_path))


BOUNDS = SplitBounds.from_as_of(date(2026, 10, 26))


def test_split_matches_bounds_of_training():
    check_split({"release": "release-a", "split": BOUNDS.as_dict()}, BOUNDS)


def test_split_bounds_are_written_as_iso_dates():
    assert BOUNDS.as_dict() == {
        "train_start": "2026-07-26", "val_start": "2026-09-21",
        "test_start": "2026-10-05", "as_of": "2026-10-26",
    }


def test_other_as_of_is_rejected():
    earlier = SplitBounds.from_as_of(date(2026, 10, 12))
    with pytest.raises(SplitMismatch, match="ml.train"):
        check_split({"release": "release-a", "split": BOUNDS.as_dict()}, earlier)


def test_release_without_bounds_is_rejected():
    with pytest.raises(SplitMismatch, match="без записанных границ"):
        check_split({"release": "release-a"}, BOUNDS)
