from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass


FORBIDDEN_PATTERNS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|attach|detach|pragma|vacuum|copy)\b",
    re.IGNORECASE,
)
TABLE_PATTERN = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
CTE_PATTERN = re.compile(r"(?:\bwith|,)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", re.IGNORECASE)
LIMIT_PATTERN = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ValidationResult:
    allowed: bool
    sql: str
    issues: tuple[str, ...]
    referenced_tables: tuple[str, ...]


def validate_sql(
    sql: str,
    allowed_tables: set[str],
    connection: sqlite3.Connection | None = None,
    max_limit: int = 100,
) -> ValidationResult:
    normalized_sql = sql.strip()
    issues: list[str] = []

    if not normalized_sql:
        return ValidationResult(False, normalized_sql, ("SQL이 비어 있습니다.",), ())

    lowered = normalized_sql.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        issues.append("읽기 전용 SELECT/WITH 조회만 허용됩니다.")

    if ";" in normalized_sql:
        issues.append("단일 SQL 문만 허용되며 세미콜론은 사용할 수 없습니다.")

    if "--" in normalized_sql or "/*" in normalized_sql or "*/" in normalized_sql:
        issues.append("SQL 주석은 허용되지 않습니다.")

    if FORBIDDEN_PATTERNS.search(normalized_sql):
        issues.append("데이터 변경 또는 운영 위험 명령은 허용되지 않습니다.")

    cte_names = set(CTE_PATTERN.findall(normalized_sql))
    referenced_tables = tuple(sorted(set(TABLE_PATTERN.findall(normalized_sql)) - cte_names))
    unknown_tables = [table for table in referenced_tables if table not in allowed_tables]
    if unknown_tables:
        issues.append(f"허용되지 않은 테이블 참조: {', '.join(unknown_tables)}")

    limit_match = LIMIT_PATTERN.search(normalized_sql)
    if not limit_match:
        issues.append("조회량 제한을 위해 LIMIT 절이 필요합니다.")
    elif int(limit_match.group(1)) > max_limit:
        issues.append(f"LIMIT은 {max_limit} 이하만 허용됩니다.")

    if connection is not None and not issues:
        try:
            connection.execute(f"EXPLAIN QUERY PLAN {normalized_sql}")
        except sqlite3.Error as exc:
            issues.append(f"SQL 문법 또는 실행 계획 오류: {exc}")

    return ValidationResult(
        allowed=not issues,
        sql=normalized_sql,
        issues=tuple(issues),
        referenced_tables=referenced_tables,
    )
