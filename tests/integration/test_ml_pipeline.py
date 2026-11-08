"""Сквозной ML-контур на маленьком наборе."""

import json
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
from ml.storage import (
    CURRENT_LINK, FORECAST_CATBOOST_FILE, MAIN_MODEL, META_FILE, FeaturesMismatch, SplitMismatch, current_release,
)
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


def test_reload_drops_predictions_of_deals_no_longer_scored(trained, deals, load, query):
    future = query("select price_id from mart.ml_price_predictions where split = 'future' order by 1")
    broken, bid, deleted = (row[0] for row in query(
        "select price_id from mart.ml_price_predictions where split = 'test' order by 1 limit 3"
    ))
    deals.execute("update prices set price = 0 where id = %s", (broken,))
    deals.execute("update prices set price_type = 6, is_fact = false where id = %s", (bid,))
    deals.execute("delete from prices where id = %s", (deleted,))
    before = query("select count(*) from mart.ml_price_predictions")[0][0]

    load()

    left = {row[0] for row in query("select price_id from mart.ml_price_predictions")}
    assert not {broken, bid, deleted} & left
    assert len(left) == before - 3
    # Прогнозы сделок, которые модель по-прежнему оценивает, ждут ml.score и не удаляются.
    assert {row[0] for row in future} <= left


RELEASES = """
    select 'predictions', model_release from mart.ml_price_predictions
    union
    select 'metrics', model_release from mart.ml_model_metrics
    order by 1, 2
"""


def test_predictions_and_metrics_share_model_release(trained, query, settings):
    release = current_release(settings.model_dir).name
    assert release.startswith("release-")
    assert query(RELEASES) == [("metrics", release), ("predictions", release)]

    # Через журнал запусков версия ведёт к шагу обучения, который её создал.
    ((train_run_id, train_release),) = query(
        "select run_id, details->>'model_release' from ops.pipeline_runs where step = 'ml-train'"
    )
    ((score_release, score_train_run_id),) = query(
        "select details->>'model_release', (details->>'train_run_id')::bigint "
        "from ops.pipeline_runs where step = 'ml-score'"
    )
    assert train_release == score_release == release
    assert score_train_run_id == train_run_id


def test_retraining_changes_model_release(trained, query, settings):
    before = current_release(settings.model_dir).name
    train(settings)
    after = current_release(settings.model_dir).name
    assert after != before
    # Метрики val пишет обучение, поэтому они уже новые, а прогнозы и test остаются от прежней версии до скоринга.
    assert query("select distinct model_release from mart.ml_model_metrics where split = 'val'") == [(after,)]
    assert query("select distinct model_release from mart.ml_price_predictions") == [(before,)]

    score(settings)
    assert query(RELEASES) == [("metrics", after), ("predictions", after)]


def test_training_reads_test_period_only_after_evaluated_model(deals, load, settings, fast_candidates,
                                                              monkeypatch):
    load()
    seen = []
    original = ml.train.read_features
    original_fit = ml.train.fit_main_model

    def spy(conn, start, end=None):
        frame = original(conn, start, end)
        seen.append(("read", frame))
        return frame

    def fit_spy(frame, *args):
        seen.append(("fit", frame))
        return original_fit(frame, *args)

    monkeypatch.setattr(ml.train, "read_features", spy)
    monkeypatch.setattr(ml.train, "fit_main_model", fit_spy)
    train(settings)

    bounds = SplitBounds.from_as_of(settings.as_of)
    steps = [step for step, _ in seen]
    assert steps == ["read", "fit", "read", "fit"]
    (_, before_test), (_, evaluated), (_, recent), (_, history) = seen
    # Модель для метрик обучена раньше, чем прочитан тестовый период.
    assert before_test["loading_date"].min() >= bounds.train_start
    assert evaluated["loading_date"].max() < bounds.test_start
    # Модель для будущих заявок видит всю историю до as_of включительно, но не дальше.
    assert recent["loading_date"].min() >= bounds.test_start
    assert history["loading_date"].max() == bounds.as_of
    assert not history["outlier_mark"].any()


def predictions_by_split(query, split):
    return query(
        "select price_id, predicted_price_rub from mart.ml_price_predictions where split = %s order by 1",
        (split,),
    )


def test_test_prices_reach_only_forecast_model(trained, query, settings):
    test_before = predictions_by_split(query, "test")
    future_before = predictions_by_split(query, "future")
    query("update core.fact_price set price_rub = price_rub * 3 "
          "where loading_date between '2026-10-05' and '2026-10-26' returning 1")
    train(settings)
    score(settings)
    # Цены test не влияют на прогноз test: модель для метрик их не видит.
    assert predictions_by_split(query, "test") == test_before
    # Модель для будущих заявок на них учится.
    assert predictions_by_split(query, "future") != future_before


def test_trained_through_names_the_model_of_each_split(trained, query):
    assert query(
        "select split, min(trained_through), max(trained_through) from mart.ml_price_predictions "
        "group by 1 order by 1"
    ) == [("future", date(2026, 10, 26), date(2026, 10, 26)), ("test", date(2026, 10, 4), date(2026, 10, 4))]


def test_score_refuses_release_without_forecast_model(trained, settings):
    (current_release(settings.model_dir) / FORECAST_CATBOOST_FILE).unlink()
    with pytest.raises(FileNotFoundError, match=f"{FORECAST_CATBOOST_FILE}.*ml.train"):
        score(settings)


def test_fact_vs_forecast_view_keeps_prediction_grain(trained, query):
    assert query("select count(*) from mart.v_fact_vs_forecast") == query(
        "select count(*) from mart.ml_price_predictions"
    )


def test_test_rows_compare_with_corridor_of_loading_month(trained, query):
    ((with_corridor, other_month),) = query(
        """
        select count(corridor_month),
               count(*) filter (where corridor_month <> date_trunc('month', loading_date)::date)
        from mart.v_fact_vs_forecast
        where split = 'test'
        """
    )
    assert with_corridor > 0
    assert other_month == 0


def test_future_rows_get_corridor_of_latest_month_of_cell(trained, query):
    rows = query(
        """
        select v.corridor_month, v.median_trip, c.median_trip
        from mart.v_fact_vs_forecast v
        join core.fact_price f using (price_id)
        left join mart.mart_route_card c
               on c.loading_city_gcid = f.loading_city_gcid
              and c.unloading_city_gcid = f.unloading_city_gcid
              and c.body_group = v.body_group
              and c.weight_category = v.weight_category
              and c.month = v.corridor_month
        where v.split = 'future' and v.loading_date >= '2026-11-01'
        """
    )
    assert rows
    # В витрине нет месяцев позже октября, поэтому коридор берётся из него.
    assert all(month == date(2026, 10, 1) and view == mart for month, view, mart in rows)


def test_failed_metrics_write_keeps_previous_release(deals, load, settings, fast_candidates,
                                                     query, monkeypatch):
    load()
    train(settings)
    previous = current_release(settings.model_dir)
    val_versions = "select model_name, xmin::text from mart.ml_model_metrics where split = 'val' order by 1"
    metrics_before = query(val_versions)

    original = ml.train.storage.write_metrics
    calls = []

    def fail_on_second_model(conn, *args, **kwargs):
        calls.append(args[0])
        if len(calls) == 2:
            raise RuntimeError("запись метрик упала")
        original(conn, *args, **kwargs)

    monkeypatch.setattr(ml.train.storage, "write_metrics", fail_on_second_model)
    with pytest.raises(RuntimeError, match="запись метрик упала"):
        train(settings)

    assert current_release(settings.model_dir) == previous
    assert [path for path in settings.model_dir.iterdir() if path.name != CURRENT_LINK] == [previous]
    # Метрики первой модели успели записаться, но откатились вместе с транзакцией.
    assert query(val_versions) == metrics_before


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
    assert query(
        "select distinct model_release from mart.ml_model_metrics where model_name in (%s, %s)",
        (NO_PRICE_TYPE_MODEL, LEAK_DEMO_MODEL),
    ) == [(current_release(settings.model_dir).name,)]


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


def test_score_refuses_model_trained_on_other_split(trained, query, settings):
    # С более ранним AS_OF test лёг бы на строки train, и метрики вышли бы завышенными.
    before = query(PREDICTIONS_HASH)
    earlier = replace(settings, as_of=date(2026, 10, 12))
    with pytest.raises(SplitMismatch, match="Переобучите"):
        score(earlier)
    with pytest.raises(SplitMismatch):
        run_ablation(earlier)
    assert query(PREDICTIONS_HASH) == before
    assert query("select status, details->>'error' like '%%Переобучите%%' from ops.pipeline_runs "
                 "where step = 'ml-score' order by run_id desc limit 1") == [("failed", True)]


def test_score_refuses_model_with_other_features(trained, query, settings):
    # Версия, обученная до переименования признака, ждёт колонку, которой код больше не строит.
    meta_path = current_release(settings.model_dir) / META_FILE
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["features"] = ["origin_city" if name == "origin_city_id" else name for name in meta["features"]]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    before = query(PREDICTIONS_HASH)
    with pytest.raises(FeaturesMismatch, match="нет в коде: \\['origin_city'\\].*Переобучите"):
        score(settings)
    with pytest.raises(FeaturesMismatch):
        run_ablation(settings)
    assert query(PREDICTIONS_HASH) == before


@pytest.fixture
def season_twins(deals):
    """Пары одинаковых сделок, у которых различается только дата погрузки, в один день недели.

    Пара test лежит после последней даты обучения модели для метрик, пара future после as_of.
    """
    pairs = {
        "test": (date(2026, 10, 10), date(2026, 10, 24)),
        "future": (date(2026, 11, 28), date(2027, 1, 23)),
    }
    return {
        split: [deals.add_deal(loading_date=day, distance=1200.0, price=105000.0) for day in days]
        for split, days in pairs.items()
    }


def test_season_does_not_go_past_training(season_twins, load, settings, fast_candidates, query):
    # Деревья не продолжают сезонность за пределы обучения, поэтому сезон таких дат берётся
    # с последней даты обучения своей модели, и пара получает одинаковый прогноз.
    load()
    build_marts(settings)
    train(settings)
    score(settings)
    for split, ids in season_twins.items():
        rows = query(
            "select split, predicted_price_rub from mart.ml_price_predictions where price_id = any(%s) order by 1",
            (ids,),
        )
        assert [row[0] for row in rows] == [split, split]
        assert rows[0][1] == rows[1][1], split
