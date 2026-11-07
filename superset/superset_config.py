"""Настройки Superset: всё берётся из окружения."""

import os

from sqlalchemy.engine import URL

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

SQLALCHEMY_DATABASE_URI = URL.create(
    "postgresql+psycopg2",
    username="superset",
    password=os.environ["SUPERSET_DB_PASSWORD"],
    host=os.environ["METADATA_DB_HOST"],
    port=int(os.environ["METADATA_DB_PORT"]),
    database="superset",
).render_as_string(hide_password=False)

# Единая светлая тема: переключение на тёмную здесь не нужно.
THEME_DEFAULT = {
    "algorithm": "default",
    "token": {
        "colorPrimary": "#e8590c",
        "colorLink": "#e8590c",
        "colorBgLayout": "#f3f5f7",
        "colorTextBase": "#16202b",
        "borderRadius": 10,
    },
}
THEME_DARK = None

# Числа в русском формате: 1 234,5.
D3_FORMAT = {"decimal": ",", "thousands": " ", "grouping": [3], "currency": ["", " ₽"]}

EXTRA_CATEGORICAL_COLOR_SCHEMES = [
    {
        "id": "market",
        "label": "Рынок ставок",
        "isDefault": True,
        "colors": ["#e8590c", "#3d4b59", "#1f8a4c", "#c92a2a", "#8a96a3"],
    },
    # Линия и полоса коридора одного цвета: иначе полоса получает следующий цвет шкалы.
    {
        "id": "market_band",
        "label": "Коридор ставки",
        "colors": ["#e8590c"],
    },
]

EXTRA_SEQUENTIAL_COLOR_SCHEMES = [
    {
        "id": "market_heat",
        "label": "Рынок ставок",
        "isDiverging": False,
        "colors": ["#fdf0e8", "#b5470b"],
    },
]
