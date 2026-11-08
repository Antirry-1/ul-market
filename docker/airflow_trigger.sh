#!/usr/bin/env bash
# Запускает DAG на всём окне источника с переобучением и ждёт его завершения.
set -euo pipefail

dag_id=ul_market_pipeline
run_id="full_$(date -u +%Y%m%dT%H%M%S)"
# Общий срок ожидания в секундах: и разбора DAG, и самого запуска. Полный запуск идёт минуты,
# поэтому дольше ждать незачем: без срока недоступная база метаданных держала бы up бесконечно.
timeout_seconds="${AIRFLOW_TRIGGER_TIMEOUT:-3600}"

check_deadline() {
  if (( SECONDS >= timeout_seconds )); then
    echo "airflow-trigger: $1 дольше ${timeout_seconds} с (AIRFLOW_TRIGGER_TIMEOUT), последнее состояние: $2" >&2
    exit 1
  fi
}

until airflow dags details "$dag_id" > /dev/null 2>&1; do
  check_deadline "планировщик не прочитал DAG" "нет DAG"
  echo "airflow-trigger: жду, пока планировщик прочитает DAG"
  sleep 5
done

airflow dags trigger "$dag_id" --run-id "$run_id" --conf '{"full": true, "retrain": true}' > /dev/null
echo "airflow-trigger: запущен $run_id"

while true; do
  state=$(airflow dags list-runs -d "$dag_id" -o json 2> /dev/null | python -c '
import json, sys
run_id = sys.argv[1]
print(next((run["state"] for run in json.load(sys.stdin) if run["run_id"] == run_id), "unknown"))
' "$run_id" 2> /dev/null || echo "unknown")
  case "$state" in
    success)
      echo "airflow-trigger: $run_id завершён успешно"
      exit 0
      ;;
    failed)
      echo "airflow-trigger: $run_id завершился ошибкой" >&2
      exit 1
      ;;
  esac
  check_deadline "$run_id не завершился" "$state"
  sleep 15
done
