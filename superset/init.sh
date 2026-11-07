#!/usr/bin/env bash
# Готовит метаданные и загружает дашборд из файлов. Повторный запуск обновляет объекты, а не дублирует их.
set -euo pipefail

superset db upgrade

if ! superset fab list-users | grep -q "username:$SUPERSET_ADMIN_USER "; then
  superset fab create-admin \
    --username "$SUPERSET_ADMIN_USER" \
    --password "$SUPERSET_ADMIN_PASSWORD" \
    --firstname Admin --lastname Admin --email admin@example.com
fi

superset init

python /app/assets/import_assets.py
