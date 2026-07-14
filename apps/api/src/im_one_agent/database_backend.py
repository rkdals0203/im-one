from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Protocol

from im_one_agent.sample_data import connect_database

DEFAULT_DATABASE_BACKEND = "sqlite"
SUPPORTED_DATABASE_BACKENDS = ("sqlite",)
MAX_QUERY_PLAN_STEPS = 8
MAX_QUERY_PLAN_DETAIL_LENGTH = 240


@dataclass(frozen=True)
class QueryExecutionResult:
    rows: list[dict[str, object]]
    columns: list[str]
    column_metadata: list[dict[str, object]]
    query_plan_summary: list[str]
    pre_execution_row_count: int | None
    pre_execution_row_count_status: str
    pre_execution_check_ms: float
    execution_ms: float
    error_issue: str | None = None


@dataclass
class SqliteAuthorizerState:
    denied_tables: set[str]
    denied_operations: set[str]


class QueryExecutionBackend(Protocol):
    name: str

    def execute_validated_sql(
        self,
        db_path: str,
        sql: str,
        timeout_ms: int,
        allowed_tables: set[str] | None = None,
    ) -> QueryExecutionResult:
        ...


class SqliteExecutionBackend:
    name = "sqlite"

    def execute_validated_sql(
        self,
        db_path: str,
        sql: str,
        timeout_ms: int,
        allowed_tables: set[str] | None = None,
    ) -> QueryExecutionResult:
        connection = connect_database(db_path)
        authorizer_state = install_sqlite_authorizer(connection, allowed_tables)
        query_plan_summary = build_sqlite_query_plan_summary(connection, sql)
        pre_execution_row_count, pre_execution_row_count_status, pre_execution_check_ms = (
            build_sqlite_pre_execution_row_count(connection, sql, timeout_ms=timeout_ms)
        )
        started_at = time.perf_counter()
        timeout_state = {"timed_out": False}

        if timeout_ms:

            def stop_on_timeout() -> int:
                if (time.perf_counter() - started_at) * 1000 > timeout_ms:
                    timeout_state["timed_out"] = True
                    return 1
                return 0

            connection.set_progress_handler(stop_on_timeout, 1000)

        try:
            cursor = connection.execute(sql)
            rows = [dict(row) for row in cursor.fetchall()]
            columns = [description[0] for description in cursor.description or []]
            column_metadata = build_column_metadata(columns, rows)
            error_issue = None
        except sqlite3.Error as exc:
            rows = []
            columns = []
            column_metadata = []
            error_issue = (
                f"SQL 실행 시간 제한 초과: {timeout_ms}ms"
                if timeout_state["timed_out"]
                else f"SQL 실행 오류: {exc}"
            )
        finally:
            if timeout_ms:
                connection.set_progress_handler(None, 0)
            connection.set_authorizer(None)
            connection.close()

        if authorizer_state and (authorizer_state.denied_tables or authorizer_state.denied_operations):
            error_issue = format_sqlite_authorization_issue(authorizer_state)

        return QueryExecutionResult(
            rows=rows,
            columns=columns,
            column_metadata=column_metadata,
            query_plan_summary=query_plan_summary,
            pre_execution_row_count=pre_execution_row_count,
            pre_execution_row_count_status=pre_execution_row_count_status,
            pre_execution_check_ms=pre_execution_check_ms,
            execution_ms=round((time.perf_counter() - started_at) * 1000, 3),
            error_issue=error_issue,
        )


def install_sqlite_authorizer(
    connection: sqlite3.Connection,
    allowed_tables: set[str] | None,
) -> SqliteAuthorizerState | None:
    if allowed_tables is None:
        return None

    normalized_allowed = {table.lower() for table in allowed_tables}
    state = SqliteAuthorizerState(denied_tables=set(), denied_operations=set())
    mutating_action_names = {
        "SQLITE_INSERT",
        "SQLITE_UPDATE",
        "SQLITE_DELETE",
        "SQLITE_ALTER_TABLE",
        "SQLITE_CREATE_TABLE",
        "SQLITE_CREATE_INDEX",
        "SQLITE_CREATE_TRIGGER",
        "SQLITE_CREATE_VIEW",
        "SQLITE_DROP_TABLE",
        "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TRIGGER",
        "SQLITE_DROP_VIEW",
        "SQLITE_ATTACH",
        "SQLITE_DETACH",
        "SQLITE_PRAGMA",
    }
    mutating_actions = {
        action_code
        for action_code in (getattr(sqlite3, name, None) for name in mutating_action_names)
        if isinstance(action_code, int)
    }

    def authorize(
        action_code: int,
        arg1: str | None,
        arg2: str | None,
        db_name: str | None,
        trigger_or_view: str | None,
    ) -> int:
        if action_code == sqlite3.SQLITE_READ:
            table_name = (arg1 or "").lower()
            if table_name and table_name not in normalized_allowed:
                state.denied_tables.add(table_name)
                return sqlite3.SQLITE_DENY
        elif action_code in mutating_actions:
            state.denied_operations.add(str(arg1 or action_code))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    return state


def format_sqlite_authorization_issue(state: SqliteAuthorizerState) -> str:
    issues: list[str] = []
    if state.denied_tables:
        issues.append("허용되지 않은 테이블 접근: " + ", ".join(sorted(state.denied_tables)))
    if state.denied_operations:
        issues.append("허용되지 않은 DB 작업: " + ", ".join(sorted(state.denied_operations)))
    return "DB 권한 정책 차단: " + "; ".join(issues)


def configured_database_backend_name() -> str:
    return os.getenv("IM_ONE_DB_BACKEND", DEFAULT_DATABASE_BACKEND).strip().lower() or DEFAULT_DATABASE_BACKEND


def available_database_backend_names() -> tuple[str, ...]:
    return SUPPORTED_DATABASE_BACKENDS


def execution_backend_for_name(name: str | None = None) -> QueryExecutionBackend:
    backend_name = (name or configured_database_backend_name()).strip().lower()
    if backend_name == "sqlite":
        return SqliteExecutionBackend()
    raise ValueError(
        "지원하지 않는 DB backend입니다: "
        + backend_name
        + ". 현재 지원: "
        + ", ".join(available_database_backend_names())
    )


def build_sqlite_query_plan_summary(
    connection: sqlite3.Connection,
    sql: str,
    max_steps: int = MAX_QUERY_PLAN_STEPS,
) -> list[str]:
    try:
        plan_rows = connection.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
    except sqlite3.Error as exc:
        return [f"query plan unavailable: {exc}"]

    summary: list[str] = []
    for row in plan_rows[:max_steps]:
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        detail = row["detail"] if "detail" in keys else row[-1]
        summary.append(str(detail)[:MAX_QUERY_PLAN_DETAIL_LENGTH])
    if len(plan_rows) > max_steps:
        summary.append(f"... {len(plan_rows) - max_steps} more query plan steps")
    return summary


def build_sqlite_pre_execution_row_count(
    connection: sqlite3.Connection,
    sql: str,
    timeout_ms: int,
) -> tuple[int | None, str, float]:
    started_at = time.perf_counter()
    timeout_state = {"timed_out": False}

    if timeout_ms:

        def stop_on_timeout() -> int:
            if (time.perf_counter() - started_at) * 1000 > timeout_ms:
                timeout_state["timed_out"] = True
                return 1
            return 0

        connection.set_progress_handler(stop_on_timeout, 1000)

    try:
        row = connection.execute(
            f"SELECT COUNT(*) AS row_count FROM ({sql}) AS im_one_pre_execution_count"
        ).fetchone()
        count = int(row["row_count"] if row is not None else 0)
        return count, "checked", round((time.perf_counter() - started_at) * 1000, 3)
    except sqlite3.Error as exc:
        status = f"timeout after {timeout_ms}ms" if timeout_state["timed_out"] else f"unavailable: {exc}"
        return None, status, round((time.perf_counter() - started_at) * 1000, 3)
    finally:
        if timeout_ms:
            connection.set_progress_handler(None, 0)


def build_column_metadata(columns: list[str], rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "name": column,
            "ordinal": index,
            "inferred_type": infer_column_type(row.get(column) for row in rows),
        }
        for index, column in enumerate(columns)
    ]


def infer_column_type(values: object) -> str:
    observed_types: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            observed_types.add("boolean")
        elif isinstance(value, int):
            observed_types.add("integer")
        elif isinstance(value, float):
            observed_types.add("number")
        else:
            observed_types.add("text")

    if not observed_types:
        return "unknown"
    if observed_types <= {"integer", "number"}:
        return "number" if "number" in observed_types else "integer"
    if len(observed_types) == 1:
        return next(iter(observed_types))
    return "mixed"
