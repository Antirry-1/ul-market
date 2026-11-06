import pytest

from ml.storage import CURRENT_LINK, current_release, new_release


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
