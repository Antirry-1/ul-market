#!/usr/bin/env bash
# Запускает DAG на всём окне источника с переобучением и ждёт его завершения.
set -euo pipefail

dag_id=ul_market_pipeline
run_id="full_$(date -u +%Y%m%dT%H%M%S)"

until airflow dags details "$dag_id" > /dev/null 2>&1; do
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
' "$run_id")
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
  sleep 15
done
