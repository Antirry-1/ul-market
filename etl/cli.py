"""Точка входа пайплайна."""

from __future__ import annotations

import argparse
from datetime import date

from etl.config import Settings
from etl.extract import source_date_range
from etl.init_dwh import init_dwh
from etl.load import load_window
from etl.log import setup_logging
from etl.marts import build_marts
from etl.summary import log_summary


def add_window_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("окно загрузки, полуинтервал по loading_date")
    group.add_argument("--start-date", type=date.fromisoformat, help="включительно")
    group.add_argument("--end-date", type=date.fromisoformat, help="не включительно")
    group.add_argument("--full", action="store_true", help="всё окно источника")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="etl.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-dwh", help="создать схемы и таблицы хранилища")
    add_window_arguments(commands.add_parser("load", help="загрузить окно дат"))
    commands.add_parser("build-marts", help="пересобрать витрину")
    run_all_parser = commands.add_parser("run-all", help="весь конвейер, по умолчанию на окне источника")
    add_window_arguments(run_all_parser)
    return parser


def resolve_window(args: argparse.Namespace, settings: Settings) -> tuple[date, date]:
    start, end = requested_window(args, settings)
    if start >= end:
        raise SystemExit(f"Пустое окно: --start-date {start} должна быть раньше --end-date {end}")
    return start, end


def requested_window(args: argparse.Namespace, settings: Settings) -> tuple[date, date]:
    if args.command == "run-all" and not (args.start_date or args.end_date):
        return source_date_range(settings.source_dsn)
    if args.full:
        if args.start_date or args.end_date:
            raise SystemExit("--full нельзя сочетать с --start-date и --end-date")
        return source_date_range(settings.source_dsn)
    if not (args.start_date and args.end_date):
        raise SystemExit("Укажите --start-date и --end-date или --full")
    return args.start_date, args.end_date


def run_all(settings: Settings, start: date, end: date) -> None:
    # ML-зависимости тяжёлые и не нужны шагам загрузки.
    from ml.ablation import run_ablation
    from ml.score import score
    from ml.train import train

    load_window(settings, start, end)
    build_marts(settings)
    train(settings)
    score(settings)
    run_ablation(settings)
    log_summary(settings)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    setup_logging()
    settings = Settings.from_env()
    if args.command == "init-dwh":
        init_dwh(settings.dwh_dsn)
    elif args.command == "load":
        load_window(settings, *resolve_window(args, settings))
    elif args.command == "build-marts":
        build_marts(settings)
    elif args.command == "run-all":
        start, end = resolve_window(args, settings)
        init_dwh(settings.dwh_dsn)
        run_all(settings, start, end)


if __name__ == "__main__":
    main()
