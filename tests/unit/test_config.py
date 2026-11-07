import pytest

from etl.config import DEFAULT_SOURCE_SHRINK_LIMIT, Settings

REQUIRED = {
    "DB_HOST": "db", "DB_PORT": "5432",
    "SOURCE_DB": "src", "SOURCE_USER": "reader", "SOURCE_PASSWORD": "secret",
    "DWH_DB": "dwh", "DWH_USER": "etl", "DWH_PASSWORD": "secret",
    "AS_OF": "2026-10-26", "MODEL_DIR": "/tmp/models", "ML_SEED": "42", "ML_THREADS": "1",
}


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SOURCE_SHRINK_LIMIT", raising=False)
    return monkeypatch


def test_shrink_limit_has_default(env):
    assert Settings.from_env().source_shrink_limit == DEFAULT_SOURCE_SHRINK_LIMIT


@pytest.mark.parametrize(("value", "expected"), [("0", 0.0), ("0.2", 0.2), ("1", 1.0), ("", DEFAULT_SOURCE_SHRINK_LIMIT)])
def test_shrink_limit_is_read_from_env(env, value, expected):
    env.setenv("SOURCE_SHRINK_LIMIT", value)
    assert Settings.from_env().source_shrink_limit == expected


@pytest.mark.parametrize("value", ["-0.1", "1.5"])
def test_shrink_limit_outside_share_is_rejected(env, value):
    env.setenv("SOURCE_SHRINK_LIMIT", value)
    with pytest.raises(RuntimeError, match="SOURCE_SHRINK_LIMIT"):
        Settings.from_env()
