from etl.audits import parse_audits

TEXT = """
-- Общий комментарий файла.

-- @audit first error
select 1
from a;

-- Комментарий ко второй проверке.
-- @audit second warn
select 2;
"""


def test_audits_are_split_by_header():
    audits = parse_audits(TEXT)
    assert [(audit.name, audit.severity) for audit in audits] == [("first", "error"), ("second", "warn")]
    assert audits[0].query == "select 1\nfrom a"
    assert audits[1].query == "select 2"
