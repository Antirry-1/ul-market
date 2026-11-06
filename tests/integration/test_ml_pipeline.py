"""Сквозной ML-контур на маленьком наборе."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

import ml.train
from etl.marts import build_marts
import ml.ablation
from ml.ablation import LEAK_DEMO_MODEL, NO_PRICE_TYPE_MODEL, run_ablation
from ml.boosting import BoostingParams
from ml.score import score
from ml.split import SplitBounds
from ml.storage import MAIN_MODEL
from ml.train import train

PREDICTIONS_HASH = """
    select count(*),
           md5(string_agg(price_id || '|' || predicted_price_rub || '|' || split, ',' order by price_id))
    from mart.ml_price_predictions
"""


@pytest.fixture
def deals(source):
    start = date(2026, 7, 26)
    for number in range(200):
        loading_date = start + timedelta(days=number % 113)
        distance = 300.0 + (number * 37) % 2000
        source.add_deal(
            loading_date=loading_date,
            distance=distance,
            price=6000.0 + 80.0 * distance + (number * 97) % 3000,
            unloading_city_gcid=1003 if number % 2 else 1004,
        )
    source.add_deal(loading_date=date(2026, 10, 5), price=50000.0)
    source.add_deal(loading_date=date(2026, 10, 4), price=50000.0)
    source.add_deal(loading_date=date(2026, 10, 10), price=9000000.0, outlier_mark=True)
    source.add_deal(loading_date=date(2026, 8, 10), price=9000000.0, outlier_mark=True)
    source.add_deal(loading_date=date(2026, 11, 20), price=70000.0)
    source.add_deal(loading_date=date(2026, 10, 10), price_type=6)
    source.add_deal(loading_date=date(2026, 11, 21), complex=True)
    return source


@pytest.fixture
def fast_candidates(monkeypatch):
    monkeypatch.setattr(ml.train, "CANDIDATES", (
        BoostingParams(depth=3, log_target=True, iterations=30),
        BoostingParams(depth=3, log_target=True, iterations=30, level_offset=True),
    ))


@pytest.fixture
def trained(deals, load, settings, fast_candidates):
    load()
    build_marts(settings)
    train(settings)
    score(settings)


def test_future_has_no_actuals_even_with_source_price(trained, query):
    rows = query(
        """
        select actual_price_rub, abs_error_rub, ape_pct, predicted_price_rub
        from mart.ml_price_predictions
        where split = 'future'
        """
    )
    assert rows
    assert all(row[:3] == (None, None, None) and row[3] > 0 for row in rows)


def test_split_boundary_goes_to_test_only(trained, query):
    assert query(
        "select split, count(*) from mart.ml_price_predictions where loading_date = '2026-10-05' group by 1"
    )[0][0] == "test"
    assert query("select count(*) from mart.ml_price_predictions where loading_date < '2026-10-05'") == [(0,)]


def test_test_rows_have_errors_and_keep_outlier_flag(trained, query):
    assert query(
        """
        select count(*)
        from mart.ml_price_predictions
        where split = 'test'
          and (actual_price_rub is null or abs_error_rub is null or ape_pct is null)
        """
    ) == [(0,)]
    assert query("select count(*) from mart.ml_price_predictions where outlier_mark") == [(1,)]


def test_bids_and_complex_routes_are_not_scored(trained, query):
    assert query(
        """
        select count(*)
        from mart.ml_price_predictions p
        join core.fact_price f using (price_id)
        where not f.is_fact or f.complex
        """
    ) == [(0,)]


def test_price_id_is_unique_per_model(trained, query):
    ((total, distinct),) = query("select count(*), count(distinct price_id) from mart.ml_price_predictions")
    assert total == distinct


def test_required_metrics_are_written(trained, query):
    rows = query(
        "select r2, mape_pct, rmse_rub, mae_rub from mart.ml_model_metrics "
        "where model_name = %s and split = 'test' and slice = 'no_outliers'",
        (MAIN_MODEL,),
    )
    assert len(rows) == 1
    assert all(value is not None for value in rows[0])


def test_repeat_gives_same_predictions(trained, query, settings):
    before = query(PREDICTIONS_HASH)
    train(settings)
    score(settings)
    assert query(PREDICTIONS_HASH) == before


def test_training_reads_only_rows_before_test_period(deals, load, settings, fast_candidates, monkeypatch):
    load()
    seen = []
    original = ml.train.read_features

    def spy(conn, start, end=None):
        frame = original(conn, start, end)
        seen.append(frame)
        return frame

    monkeypatch.setattr(ml.train, "read_features", spy)
    train(settings)

    bounds = SplitBounds.from_as_of(settings.as_of)
    assert seen[0]["loading_date"].max() < bounds.test_start
    assert seen[0]["loading_date"].min() >= bounds.train_start


def test_fact_vs_forecast_view_keeps_prediction_grain(trained, query):
    assert query("select count(*) from mart.v_fact_vs_forecast") == query(
        "select count(*) from mart.ml_price_predictions"
    )


@pytest.fixture
def level_model(deals, load, settings, monkeypatch):
    monkeypatch.setattr(ml.train, "CANDIDATES", (
        BoostingParams(depth=3, log_target=True, iterations=30, level_offset=True),
    ))
    load()
    train(settings)
    score(settings)


def test_ablation_writes_metrics_but_not_predictions(level_model, query, settings):
    run_ablation(settings)
    assert query(
        """
        select model_name, count(*) > 0
        from mart.ml_model_metrics
        where model_name in (%s, %s) and split = 'test' and slice = 'no_outliers'
        group by 1
        order by 1
        """,
        (NO_PRICE_TYPE_MODEL, LEAK_DEMO_MODEL),
    ) == sorted([(NO_PRICE_TYPE_MODEL, True), (LEAK_DEMO_MODEL, True)])
    assert query("select distinct model_name from mart.ml_price_predictions") == [(MAIN_MODEL,)]


def test_ablation_level_model_does_not_see_price_type(level_model, settings, monkeypatch):
    seen = []
    original = ml.ablation.LogRidge

    def spy(categorical):
        seen.append(categorical)
        return original(categorical)

    monkeypatch.setattr(ml.ablation, "LogRidge", spy)
    run_ablation(settings)
    assert "price_type" not in seen[0]
    assert "price_type" in seen[1]


def test_score_without_trained_model_is_clear(deals, load, settings):
    load()
    empty = replace(settings, model_dir=settings.model_dir / "empty")
    with pytest.raises(FileNotFoundError, match="ml.train"):
        score(empty)
