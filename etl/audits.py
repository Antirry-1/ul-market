"""SQL-аудиты: запрос возвращает нарушения, ноль строк значит проверка пройдена."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import psycopg

from etl.db import read_sql
from etl.log import log_event

logger = logging.getLogger(__name__)

AUDIT_HEADER = re.compile(r"^-- @audit (\S+) (error|warn)[ \t]*$", re.MULTILINE)


SEVERITY_LEVELS = {"error": logging.ERROR, "warn": logging.WARNING}

# Сколько строк нарушения показать в логе и журнале, чтобы найти их без повторного запроса.
SAMPLE_ROWS = 5


class AuditFailed(RuntimeError):
    """results хранит число нарушений по всем аудитам файла, а не только по упавшим.

    samples хранит первые строки нарушений по каждому аудиту, где они нашлись.
    """

    def __init__(
        self,
        message: str,
        results: dict[str, int] | None = None,
        samples: dict[str, list] | None = None,
    ) -> None:
        super().__init__(message)
        self.results = dict(results or {})
        self.samples = dict(samples or {})


@dataclass(frozen=True)
class Audit:
    name: str
    severity: str
    query: str


def parse_audits(text: str) -> list[Audit]:
    headers = list(AUDIT_HEADER.finditer(text))
    audits = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        lines = [
            line for line in text[header.end():end].splitlines()
            if line.strip() and not line.lstrip().startswith("--")
        ]
        query = "\n".join(lines).rstrip().rstrip(";")
        audits.append(Audit(header.group(1), header.group(2), query))
    return audits


def _plain(value: object) -> object:
    """Значение для JSON журнала: даты и numeric строкой."""
    return value if value is None or isinstance(value, (bool, int, float, str)) else str(value)


def sample_rows(rows: list[tuple]) -> list:
    """Строки нарушений без столбца с их общим числом. Ключ из одного столбца идёт без обёртки."""
    return [_plain(row[1]) if len(row) == 2 else [_plain(value) for value in row[1:]] for row in rows]


def run_audits(conn: psycopg.Connection, relative_path: str) -> dict[str, int]:
    results: dict[str, int] = {}
    samples: dict[str, list] = {}
    failed: list[str] = []
    for audit in parse_audits(read_sql(relative_path)):
        # Окно считается до limit, поэтому один проход даёт и общее число, и образец строк.
        rows = conn.execute(
            f"select count(*) over (), violations.* from ({audit.query}) violations limit {SAMPLE_ROWS}"
        ).fetchall()
        count = rows[0][0] if rows else 0
        results[audit.name] = count
        if count:
            samples[audit.name] = sample_rows(rows)
            log_event(logger, "audit_violations", level=SEVERITY_LEVELS[audit.severity],
                      audit=audit.name, severity=audit.severity, rows=count, sample=samples[audit.name])
            if audit.severity == "error":
                failed.append(audit.name)
    if failed:
        raise AuditFailed(f"Не пройдены аудиты: {', '.join(failed)}", results, samples)
    return results
