# Ручная проверка

Порядок, по которому я проверяю проект после изменений: чистый запуск, сверка данных по хэшам, повторный прогон и дополнительные профили. Команды выполняются из корня проекта. Основная часть занимает минут 15, вместе с Superset, Airflow и ClickHouse около часа.

Понадобятся Docker Compose 5.6.1, около 3 ГБ на диске и ещё до 3,5 ГБ на каждый профиль. Если места мало, поднимайте профили по одному. Порты по умолчанию: 55432 для PostgreSQL, 8088 для Superset, 8081 для Airflow, 58123 для ClickHouse. Если какой-то занят, скопируйте `.env.example` в `.env` и поменяйте нужный `*_HOST_PORT`. Логины и пароли тоже лежат в `.env.example`.

## 1. Чистый запуск

```bash
# удалит данные прошлых запусков, в том числе Superset, Airflow и ClickHouse
docker compose --profile superset --profile airflow --profile clickhouse down -v
docker-compose up --build -d
docker wait ul-market-pipeline-1
docker compose logs pipeline | grep pipeline_summary
```

Профили в `down` нужны: без них `-v` удаляет только том PostgreSQL и артефакты модели. Метаданные Superset и Airflow, логи Airflow и данные ClickHouse тогда остаются, и в Airflow будут видны запуски прошлого стенда.

`docker wait` должен напечатать `0`, а в логе должно появиться событие `pipeline_summary`. В `docker compose ps -a` остаётся работать только `postgres`, `source-seed` и `pipeline` завершаются с кодом 0.

Ключ `-d` нужен только для удобства. Можно запустить просто `docker-compose up` и выполнять остальное во втором терминале.

## 2. Хэши таблиц

```bash
docker compose exec -T postgres psql -U postgres -d dwh -qAtX -v ON_ERROR_STOP=1 < sql/queries/table_hashes.sql
```

Запрос выводит число строк и md5 по каждой таблице. Время загрузки, номер батча, время скоринга и версия модели в хэш не входят, поэтому от запуска к запуску он не меняется. Должно получиться 13 строк:

| Таблица | Строк и хэш |
|---|---|
| `raw.prices` | `300000\|8206cf3ace793713417fef9986a55505` |
| `raw.price_dates` | `336802\|a3e6b35c8685da2e2c263e31f4fc3d27` |
| `raw.price_body_types` | `325131\|72ad7d81f33ff20d86e3976633a7f3af` |
| `core.dim_city` | `40\|b48593cf631a66550e24abd80b7a7197` |
| `core.dim_body_type` | `19\|5c6fb1f9021a756d2f5d256426a1cb25` |
| `core.fact_price` | `299797\|9f54ce1dd9631fdea0de1a8b0c5ad27b` |
| `core.fact_price_date` | `336576\|fa72940d3371604bf133572e83f0eddf` |
| `core.bridge_price_body_group` | `335077\|e19c42eb2847248ab06cc94506b697a2` |
| `dq.dq_quarantine_prices` | `203\|b0fda1dbc3152eb3e890827107b46f8a` |
| `mart.mart_route_card` | `203644\|33a31b9e318fae32264d0f1b774fef90` |
| `mart.ml_price_predictions` | `17969\|c0c7163e87bacb76697a735ae11837a3` |
| `mart.ml_model_metrics` | `93\|fa4fe00a7c949086fff20c37db793e68` |
| `mart.mart_market_summary` | `1032935\|9a06699da8fd3d58d2511fffa10fe505` |

Если строк меньше 13, какой-то таблицы нет. Генератор и модель работают с фиксированным зерном, но на другом процессоре или другой сборке CatBoost хэши `ml_price_predictions` и `ml_model_metrics` могут отличаться. Тогда смотрите метрики из следующего шага, а остальные 11 хэшей должны совпасть в любом случае.

## 3. Витрины, карантин и модель

```bash
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$DWH_DB"' < sql/queries/examples.sql
```

Что должно получиться:

- дублей зерна витрины 0;
- в карантине 164 сделки с `PRICE_NON_POSITIVE` и 39 с `FX_RATE_MISSING`;
- из 300 000 сделок источника 299 797 в `core` и 203 в карантине, то есть каждая учтена ровно один раз;
- ячеек `LOW_DATA` 202 710, `MEDIUM` 924, `HIGH` 10;
- у маршрута Москва → Ростов-на-Дону, Тент, FTL 13 месяцев истории без пропусков, поэтому `mom_pct` и `yoy_pct` заполнены везде, а октябрь 2026 отмечен `is_partial_month`;
- у заявок на декабрь в `corridor_month` стоит октябрь 2026: месяца погрузки в витрине ещё нет, и прогноз стоит рядом с последним коридором своей ячейки;
- в журнале запусков шаги `load`, `build-marts`, `ml-train`, `ml-score` и `ml-ablation` в статусе `success`, `load` прочитал 300 000 строк, загрузил 299 797, в карантин отправил 203.

Метрики на test, 12 808 строк без размеченных выбросов:

| Модель | R² | MAPE, % | RMSE | MAE |
|---|---:|---:|---:|---:|
| `catboost_price_v1` | 0,9296 | 13,65 | 50 774 | 25 809 |
| `ridge_v1` | 0,9271 | 13,59 | 51 647 | 26 177 |
| `baseline_rate_per_km_v1` | 0,7172 | 42,78 | 101 726 | 62 112 |
| `ablation_no_price_type` | 0,9261 | 14,13 | 51 998 | 26 674 |
| `leak_demo_price_per_km` | 0,9870 | 5,71 | 21 847 | 10 407 |

`leak_demo_price_per_km` специально обучена с утечкой, чтобы было видно, как она выглядит в метриках. Прогнозы она не пишет.

Отдельно стоит проверить, что у будущих заявок нет факта:

```bash
docker compose exec -T postgres psql -U postgres -d dwh -c "
select split, count(*), count(actual_price_rub) as with_actual, count(abs_error_rub) as with_error,
       min(trained_through), max(trained_through)
from mart.ml_price_predictions group by split order by split"
```

У `future` должно быть 4 891 строка с нулями в `with_actual` и `with_error`, у `test` 13 078 строк с фактом во всех. В `trained_through` у `future` везде `2026-10-26`, у `test` везде `2026-10-04`: будущие заявки оценивает модель, обученная на всей истории до даты снимка, а `test` модель, которая тестового периода не видела.

## 4. Тесты

```bash
docker compose run --rm pipeline pytest
docker compose run --rm pipeline pytest -m stand -s
```

Первая команда даёт 288 passed. Тесты создают свои базы `ul_test_*` и рабочие данные не трогают. В `.env.example` стоит `REQUIRE_DB=1`, поэтому без доступной базы интеграционные тесты падают, а не пропускаются молча. Вторая даёт 4 passed: она сравнивает ошибку модели с пределом, который задаёт шум генератора.

Те же тесты запускает GitHub Actions ([.github/workflows/tests.yml](../.github/workflows/tests.yml)). Его шаги можно повторить локально, на отдельном проекте compose и маленьком источнике:

```bash
export COMPOSE_PROJECT_NAME=ul-market-ci
docker compose build pipeline
docker compose run --rm -e N_PRICES=2000 source-seed
docker compose run --rm --no-deps pipeline pytest
docker compose down -v
unset COMPOSE_PROJECT_NAME
```

Результат тот же, 288 passed, вместе со сборкой около полутора минут, если образ уже в кэше. Без `COMPOSE_PROJECT_NAME` команды попали бы в основной проект `ul-market`, и `down -v` в конце удалил бы его тома вместе с загруженным источником и хранилищем.

## 5. Повторный прогон

```bash
docker compose run --rm pipeline python -m etl.cli run-all --start-date 2026-10-01 --end-date 2026-11-01
docker compose exec -T postgres psql -U postgres -d dwh -qAtX -v ON_ERROR_STOP=1 < sql/queries/table_hashes.sql
```

`run-all` заново грузит октябрь, пересобирает витрины, переобучает модель и пересчитывает прогнозы. Хэши после него должны остаться такими же, как в шаге 2.

## 6. Superset

```bash
docker-compose --profile superset up --build -d
docker wait ul-market-superset-init-1
```

После `0` от `docker wait` откройте http://localhost:8088 (логин `admin`, пароль `local_superset_admin_change_me`, это значение `SUPERSET_ADMIN_PASSWORD` из `.env.example`) и дашборд «Рынок ставок: маршруты и прогноз». На всех четырёх вкладках графики должны строиться без ошибок. На «Истории маршрута» по умолчанию выбран Москва → Ростов-на-Дону, Тент, FTL. Таблица «История ячейки» показывает все 37 месяцев. Последние 13 должны совпасть с историей из шага 3, где запрос ограничен 13 строками, медиана за сентябрь 2026 равна 126 395,50. Жёлтый ⚠ у «Топ-10 направлений» и «200 рейсов с наибольшим превышением прогноза» ошибкой не считается: таблицы показывают первые N строк, и Superset так предупреждает, что лимит строк достигнут.

## 7. Airflow

```bash
docker-compose --profile airflow up --build -d
docker wait ul-market-airflow-trigger-1
```

`airflow-trigger` запускает DAG на всём источнике с переобучением и завершается с его статусом, так что `0` здесь значит, что DAG прошёл. В интерфейсе на http://localhost:8081 (логин `admin`, пароль `local_airflow_admin_change_me`, это значение `_AIRFLOW_WWW_USER_PASSWORD` из `.env.example`) у запуска `full_*` 10 из 11 задач зелёные. Задача `clickhouse_mirror` пропущена: ClickHouse в этом шаге не запущен, и `clickhouse_gate` её отключает. Хэши из шага 2 после этого не меняются.

## 8. ClickHouse

```bash
docker-compose --profile clickhouse up --build -d
docker wait ul-market-clickhouse-mirror-1
docker compose --profile clickhouse exec clickhouse sh -c \
  'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" -d "$CLICKHOUSE_DB" -q "select count() from mart_route_card final"'
```

`clickhouse-mirror` перед публикацией сверяет итоги по месяцам с PostgreSQL и при расхождении падает, поэтому `0` уже означает, что зеркало совпало. Если вместе с этим профилем запущен и `airflow`, зеркало обновляет ещё и задача DAG `clickhouse_mirror`: в журнале `ops.pipeline_runs` тогда несколько записей `clickhouse-mirror`, и все в статусе `success`. В таблице должно быть 203 644 строки. Примеры запросов дают те же числа, что и в PostgreSQL:

```bash
docker compose --profile clickhouse exec -T clickhouse sh -c \
  'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" -d "$CLICKHOUSE_DB" --multiquery' < sql/clickhouse/examples.sql
```

### Как посмотреть базу

В базе `market` после зеркала одна таблица, `mart_route_card`. Временная `mart_route_card_staging` удаляется сразу после подмены. Логин `market`, пароль `local_clickhouse_change_me`, это значение `CLICKHOUSE_PASSWORD` из `.env.example`.

Проще всего открыть консоль внутри контейнера:

```bash
docker compose --profile clickhouse exec clickhouse sh -c \
  'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" -d "$CLICKHOUSE_DB"'
```

В ней можно выполнить, например:

```sql
show tables;
describe table mart_route_card;
select month, count() from mart_route_card final group by month order by month;
select * from mart_route_card final limit 5 format Vertical;
```

Без `final` в выборке могут попасться дубли ключа, которые `ReplacingMergeTree` ещё не слил. Выход из консоли: `\q`.

С хоста ClickHouse доступен только по HTTP, на порту 58123:

- в браузере http://localhost:58123/play: встроенный редактор запросов, логин и пароль вводятся вверху справа;
- из консоли: `curl -u market:local_clickhouse_change_me 'http://localhost:58123/?database=market' --data-binary 'show tables'`;
- в DBeaver или DataGrip: драйвер ClickHouse, хост `localhost`, порт `58123`, база `market`. Нативный порт 9000 наружу не проброшен, поэтому клиенту нужен HTTP-протокол.

## Между профилями и в конце

Каждый `up` с профилем заново прогоняет `pipeline`, так что это заодно ещё одна проверка повторного запуска. Чтобы освободить место перед следующим профилем:

```bash
docker compose --profile superset down     # или airflow, clickhouse; данные остаются
docker rmi ul-market-superset:local        # ul-market-airflow:local после Airflow
```

Остановить весь стенд и сохранить данные:

```bash
docker compose --profile superset --profile airflow --profile clickhouse down
```

Просто `docker compose down` без `--profile` убирает только `postgres`, `source-seed` и `pipeline`. Контейнеры профилей остаются работать, а сеть пишет `Resource is still in use`.

Убрать всё, включая данные:

```bash
docker compose --profile superset --profile airflow --profile clickhouse down -v
```
