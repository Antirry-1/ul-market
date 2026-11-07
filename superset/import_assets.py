"""Загружает дашборд из файлов и подставляет подключение из окружения.

Обычный импорт дашборда не обновляет уже созданные графики и датасеты, поэтому здесь
используется импорт ресурсов: он перезаписывает все объекты, и правка файла доходит до Superset.
Пароль хранится только в окружении: в файлах репозитория вместо него маска.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from flask import g

from superset.app import create_app

ASSETS_DIR = Path("/app/assets/market")
PASSWORD_MASK = "XXXXXXXXXX"


def render_database(text: str) -> str:
    config = yaml.safe_load(text)
    config["sqlalchemy_uri"] = (
        f"postgresql+psycopg2://bi_reader:{PASSWORD_MASK}"
        f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DWH_DB']}"
    )
    config["password"] = os.environ["BI_PASSWORD"]
    return yaml.safe_dump(config, allow_unicode=True)


def read_assets(assets: Path) -> dict[str, str]:
    contents = {}
    for path in sorted(assets.rglob("*.yaml")):
        relative = path.relative_to(assets)
        text = path.read_text(encoding="utf-8")
        contents[str(relative)] = render_database(text) if relative.parts[0] == "databases" else text
    return contents


def main() -> None:
    with create_app().app_context():
        # Модули Superset читают настройки при импорте, поэтому импорт идёт внутри контекста приложения.
        from superset import security_manager
        from superset.commands.importers.v1.assets import ImportAssetsCommand

        g.user = security_manager.find_user(username=os.environ["SUPERSET_ADMIN_USER"])
        ImportAssetsCommand(read_assets(ASSETS_DIR)).run()


if __name__ == "__main__":
    main()
