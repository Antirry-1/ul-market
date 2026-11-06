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


class AuditFailed(RuntimeError):
    pass


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


def run_audits(conn: psycopg.Connection, relative_path: str) -> dict[str, int]:
    results: dict[str, int] = {}
    failed: list[str] = []
    for audit in parse_audits(read_sql(relative_path)):
        count = conn.execute(f"select count(*) from ({audit.query}) violations").fetchone()[0]
        results[audit.name] = count
        if count:
            log_event(logger, "audit_violations", audit=audit.name, severity=audit.severity, rows=count)
            if audit.severity == "error":
                failed.append(audit.name)
    if failed:
        raise AuditFailed(f"Не пройдены аудиты: {', '.join(failed)}")
    return results
