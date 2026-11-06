"""Итог прогона в логе."""

from __future__ import annotations

import logging

from etl.config import Settings
from etl.db import connect
from etl.log import log_event

logger = logging.getLogger(__name__)

SUMMARY_QUERY = """
select
    (select count(*) from core.fact_price) as fact_rows,
    (select count(distinct price_id) from dq.dq_quarantine_prices) as quarantined_deals,
    (select count(*) from mart.mart_route_card) as mart_rows,
    (select count(*) from mart.ml_price_predictions) as predictions
"""

METRICS_QUERY = """
select model_name, n_rows, round(r2::numeric, 4), round(mape_pct::numeric, 2),
       round(rmse_rub::numeric), round(mae_rub::numeric)
from mart.ml_model_metrics
where split = 'test' and slice = 'no_outliers'
order by mape_pct desc
"""


def log_summary(settings: Settings) -> None:
    with connect(settings.dwh_dsn) as conn:
        cursor = conn.execute(SUMMARY_QUERY)
        names = [column.name for column in cursor.description]
        log_event(logger, "pipeline_summary", **dict(zip(names, cursor.fetchone())))
        for model, n_rows, r2, mape, rmse, mae in conn.execute(METRICS_QUERY).fetchall():
            log_event(logger, "model_summary", model=model, n_rows=n_rows, r2=r2,
                      mape_pct=mape, rmse_rub=rmse, mae_rub=mae)
