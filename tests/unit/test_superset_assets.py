import re
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "superset" / "assets"

# Медианы и перцентили не складываются, а сделка с двумя кузовами входит в две ячейки.
FORBIDDEN_AGGREGATE = re.compile(
    r"\b(avg|sum)\s*\(\s*(p25_\w+|median_\w+|p75_\w+|mom_pct|yoy_pct|trips_count|carriers_count)",
    re.IGNORECASE,
)


def texts(folder: str) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in (ASSETS / folder).rglob("*.yaml")}


def top_level_uuid(text: str) -> str:
    return re.search(r"^uuid: (\S+)$", text, re.MULTILINE).group(1)


def test_every_chart_points_to_an_existing_dataset():
    datasets = {top_level_uuid(text) for text in texts("datasets").values()}
    for name, text in texts("charts").items():
        assert re.search(r"^dataset_uuid: (\S+)$", text, re.MULTILINE).group(1) in datasets, name


def test_dashboard_contains_every_chart_and_nothing_else():
    charts = {top_level_uuid(text) for text in texts("charts").values()}
    dashboard = texts("dashboards")["market_rates.yaml"]
    assert set(re.findall(r"^ +uuid: (\S+)$", dashboard, re.MULTILINE)) == charts


def test_dashboard_filters_point_to_existing_datasets():
    datasets = {top_level_uuid(text) for text in texts("datasets").values()}
    dashboard = texts("dashboards")["market_rates.yaml"]
    filter_datasets = set(re.findall(r"datasetUuid: (\S+)", dashboard))
    assert filter_datasets and filter_datasets <= datasets


def test_repository_files_hold_only_a_password_mask():
    database = texts("databases")["market_dwh.yaml"]
    assert "bi_reader:XXXXXXXXXX@" in database
    assert not re.search(r"^password:", database, re.MULTILINE)


@pytest.mark.parametrize("name", sorted(texts("charts")))
def test_chart_does_not_aggregate_ready_statistics(name):
    assert not FORBIDDEN_AGGREGATE.search(texts("charts")[name])


def test_forbidden_aggregate_pattern_catches_averaged_median():
    assert FORBIDDEN_AGGREGATE.search("sqlExpression: AVG(median_trip)")
    assert not FORBIDDEN_AGGREGATE.search("sqlExpression: MAX(median_trip)")
