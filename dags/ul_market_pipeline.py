"""Ежедневное обновление хранилища и витрины.

Задачи вызывают те же команды, что и запуск без Airflow, поэтому результат не зависит от оркестратора.
Обучение идёт только по запросу: ежедневный запуск применяет готовую модель.

Плановый запуск перегружает не один день, а окно от 35 дней до интервала до 120 дней после него:
так в хранилище попадают заявки, созданные накануне с погрузкой в будущем, и поправленные недавние сделки.

Запуск с параметрами: {"full": true}, {"start_date": "2026-10-01", "end_date": "2026-11-01"},
для переобучения добавьте "retrain": true.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import BranchPythonOperator
from airflow.utils.trigger_rule import TriggerRule
from pipeline_window import load_arguments, retrain_requested

PYTHON = "/opt/pipeline-venv/bin/python"
PIPELINE_DIR = "/opt/pipeline"


def pipeline_task(task_id: str, module_and_args: str, **kwargs: object) -> BashOperator:
    return BashOperator(
        task_id=task_id,
        bash_command=f"{PYTHON} -m {module_and_args}",
        cwd=PIPELINE_DIR,
        **kwargs,
    )


def choose_after_retrain_gate(retrain_task: str, otherwise: str):
    def choose(dag_run, **_: object) -> str:
        return retrain_task if retrain_requested(dag_run.conf) else otherwise

    return choose


with DAG(
    dag_id="ul_market_pipeline",
    description="Загрузка окна, витрина и прогноз тарифа",
    start_date=datetime(2026, 10, 26),
    schedule="@daily",
    # Окна будущих заявок ещё не наступили, догонять историю по расписанию незачем:
    # весь источник загружается одним запуском с {"full": true}.
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=1)},
    user_defined_macros={"load_arguments": load_arguments},
    tags=["market"],
) as dag:
    init_dwh = pipeline_task("init_dwh", "etl.cli init-dwh")
    load = pipeline_task(
        "load",
        "etl.cli load {{ load_arguments(dag_run.conf, data_interval_start, data_interval_end) }}",
    )
    build_marts = pipeline_task("build_marts", "etl.cli build-marts")

    train_gate = BranchPythonOperator(
        task_id="train_gate",
        python_callable=choose_after_retrain_gate("train", "score"),
    )
    train = pipeline_task("train", "ml.train")
    score = pipeline_task("score", "ml.score", trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)

    ablation_gate = BranchPythonOperator(
        task_id="ablation_gate",
        python_callable=choose_after_retrain_gate("ablation", "summary"),
    )
    ablation = pipeline_task("ablation", "ml.ablation")
    summary = pipeline_task("summary", "etl.cli summary", trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)

    init_dwh >> load >> build_marts >> train_gate
    train_gate >> [train, score]
    train >> score
    score >> ablation_gate
    ablation_gate >> [ablation, summary]
    ablation >> summary
