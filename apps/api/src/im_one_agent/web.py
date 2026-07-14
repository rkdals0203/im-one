from __future__ import annotations

import csv
import hmac
import io
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any

from im_one_agent.conversation import sanitize_conversation_context
from im_one_agent.database_backend import configured_database_backend_name
from im_one_agent.domain import (
    AS_OF_DATE,
    BUSINESS_RULES,
    DEFAULT_BRANCH_ID,
    METRICS,
    MAX_QUESTION_LENGTH,
    ROLE_TABLE_POLICY,
    TABLES,
    normalize_branch_id,
    normalize_question_text,
    normalize_user_role,
)
from im_one_agent.env import load_project_env
from im_one_agent.evaluation import (
    PRD_EVALUATION_THRESHOLDS,
    build_evaluation_case_summary,
    build_verified_question_manifest,
    evaluation_threshold_failures,
)
from im_one_agent.export_utils import sanitize_csv_cell
from im_one_agent.graph import build_agent, has_execution_failure
from im_one_agent.preflight import build_preflight_report, preflight_requirements_for_profile, run_preflight
from im_one_agent.response import format_validation_evidence, retry_guidance_for
from im_one_agent.sample_data import connect_database, ensure_demo_database
from im_one_agent.schema_retrieval import (
    configured_embedding_base_url,
    configured_embedding_model,
    local_embedding_no_auth_enabled,
    remote_embeddings_configured,
)
from im_one_agent.sql_generator import (
    configured_llm_base_url,
    configured_llm_model,
    llm_endpoint_configured,
    local_llm_no_auth_enabled,
)

load_project_env()

DEFAULT_DB_PATH = "data/im_one_demo.sqlite"
DEFAULT_AUDIT_PATH = "logs/audit.jsonl"
DEFAULT_FEEDBACK_PATH = "logs/feedback.jsonl"
MAX_SESSIONS = int(os.getenv("IM_ONE_MAX_SESSIONS", "100"))
MAX_JSON_BODY_BYTES = int(os.getenv("IM_ONE_MAX_JSON_BODY_BYTES", "65536"))
MAX_FEEDBACK_COMMENT_LENGTH = 1000
ALLOWED_FEEDBACK_RATINGS = {"up", "down"}
ALLOWED_FEEDBACK_CATEGORIES = {
    "semantic_mapping",
    "sql_generation",
    "result_explanation",
    "ui_workflow",
}
SESSION_CONTEXTS: dict[str, dict[str, Any]] = {}
SESSION_RESULTS: dict[str, dict[str, Any]] = {}
PROCESS_STARTED_AT = time.time()
RUNTIME_METRICS: dict[str, int] = {
    "queries_total": 0,
    "queries_allowed_total": 0,
    "queries_blocked_total": 0,
    "exports_total": 0,
    "feedback_total": 0,
    "errors_total": 0,
    "llm_generation_failures_total": 0,
}
SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
TRUSTED_HEADER_USER_PATTERN = re.compile(r"^[A-Za-z0-9._@-]{1,120}$")
PUBLIC_GET_PATHS = {"/api/health", "/api/demo-questions"}
PROTECTED_GET_PATHS = {
    "/api/metrics",
    "/api/feedback-summary",
    "/api/audit-summary",
    "/api/catalog",
    "/api/catalog-governance",
    "/api/evaluation-summary",
    "/api/verified-questions",
    "/api/readiness",
}
PROTECTED_POST_PATHS = {"/api/query", "/api/export", "/api/feedback"}


@dataclass(frozen=True)
class RequestIdentity:
    user_id: str
    role: str
    branch_id: int
    auth_mode: str


@dataclass(frozen=True)
class QueryRequestPayload:
    question: str
    role: str
    branch_id: int
    session_id: str
    conversation_context: dict[str, Any]


class PayloadValidationError(ValueError):
    """Raised when an API request payload is syntactically valid but unusable."""


class PayloadTooLargeError(ValueError):
    """Raised when an API request body exceeds the configured safety budget."""


def serialize(value: Any) -> Any:
    if is_dataclass(value):
        return serialize(asdict(value))
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize(item) for item in value]
    return value


def normalize_session_id(raw_session_id: object | None) -> str:
    if isinstance(raw_session_id, str) and raw_session_id.strip():
        candidate = raw_session_id.strip()[:80]
        if SESSION_ID_PATTERN.fullmatch(candidate):
            return candidate
    return uuid.uuid4().hex


def read_json_body(headers: Any, body_stream: Any, max_bytes: int = MAX_JSON_BODY_BYTES) -> dict[str, Any]:
    try:
        content_length = int(headers.get("Content-Length", "0"))
    except (TypeError, ValueError) as exc:
        raise PayloadValidationError("요청 형식이 올바르지 않습니다.") from exc

    if content_length < 0 or content_length > max_bytes:
        raise PayloadTooLargeError(f"요청 본문은 {max_bytes}바이트 이하로 전송해주세요.")

    try:
        body = body_stream.read(content_length).decode("utf-8")
        payload = json.loads(body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PayloadValidationError("요청 형식이 올바르지 않습니다.") from exc

    if not isinstance(payload, dict):
        raise PayloadValidationError("요청 JSON은 객체여야 합니다.")
    return payload


def parse_query_payload(payload: dict[str, Any]) -> QueryRequestPayload:
    try:
        question = normalize_question_text(payload.get("question", ""))
    except ValueError as exc:
        raise PayloadValidationError(str(exc)) from exc

    role = str(payload.get("role", "branch_manager")).strip() or "branch_manager"
    try:
        branch_id = int(payload.get("branchId", 1))
    except (TypeError, ValueError) as exc:
        raise PayloadValidationError("branchId는 숫자여야 합니다.") from exc

    session_id = normalize_session_id(payload.get("sessionId"))
    raw_context = payload.get("conversationContext", {})
    payload_context = raw_context if isinstance(raw_context, dict) else {}
    conversation_context = sanitize_conversation_context(
        {**SESSION_CONTEXTS.get(session_id, {}), **payload_context}
    )

    return QueryRequestPayload(
        question=question,
        role=role,
        branch_id=branch_id,
        session_id=session_id,
        conversation_context=conversation_context,
    )


def normalize_export_type(value: object) -> str:
    export_type = str(value or "csv").strip().lower()
    if export_type not in {"csv", "report"}:
        raise PayloadValidationError("exportType은 csv 또는 report여야 합니다.")
    return export_type


def escape_markdown_table_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    text = text.replace("\\", "\\\\")
    text = text.replace("|", "\\|")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    return text


def build_csv_document(columns: list[str], rows: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([sanitize_csv_cell(column) for column in columns])
    for row in rows:
        writer.writerow([sanitize_csv_cell(row.get(column, "")) for column in columns])
    return output.getvalue()


def build_report_draft(result: dict[str, Any]) -> str:
    columns = result.get("columns", [])
    column_metadata = result.get("columnMetadata", [])
    query_plan = result.get("queryPlan") or result.get("query_plan_summary") or []
    pre_execution_row_count = result.get("preExecutionRowCount")
    pre_execution_row_count_status = result.get("preExecutionRowCountStatus", "")
    pre_execution_check_ms = result.get("preExecutionCheckMs")
    rows = result.get("rows", [])
    preview_rows = rows[:10]
    validation = result.get("validation", {})
    metrics = result.get("metrics", [])
    tables = result.get("tables", [])
    trace_items = result.get("executionTrace", [])
    assumptions = validation.get("assumptions") or result.get("clarificationOptions", [])
    generation_assumptions = result.get("generationAssumptions", [])
    metric_lines = [
        f"- {metric.get('name', '')}: {metric.get('definition', metric.get('description', ''))}"
        for metric in metrics
        if isinstance(metric, dict)
    ]
    table_lines = [
        f"- {table.get('name', '')}: {', '.join(table.get('columns', []))}"
        for table in tables
        if isinstance(table, dict)
    ]
    trace_lines = [
        f"- {item.get('node', '')}: {item.get('status', '')} - {item.get('detail', '')}"
        for item in trace_items
        if isinstance(item, dict)
    ]
    validation_evidence_lines = [
        f"- {line}"
        for line in format_validation_evidence(
            bool(validation.get("allowed")),
            validation.get("issues", []),
            validation.get("referenced_tables", []),
        )
    ]
    lines = [
        "# iM One NL2SQL 조회 보고서 초안",
        "",
        f"- 질문: {result.get('question', '')}",
        f"- 역할: {result.get('role', '')}",
        f"- 지점 범위: branch_id={result.get('branchId', '')}",
        f"- 검증 상태: {validation.get('allowed')}",
        f"- 생성 엔진: {result.get('generationEngine', '')}",
        f"- LLM 모델: {result.get('llmModel', '')}",
        f"- 프롬프트 버전: {result.get('promptVersion', '')}",
        f"- 조회 행 수: {result.get('rowCount', 0)}",
        (
            f"- 실행 전 row count 확인: {pre_execution_row_count} rows"
            + (f" ({pre_execution_check_ms} ms)" if pre_execution_check_ms is not None else "")
            if pre_execution_row_count is not None
            else f"- 실행 전 row count 확인: {pre_execution_row_count_status or '없음'}"
        ),
        "- 데이터 주의: 합성 데이터 기반 POC 결과이며, 보고나 의사결정 전 기준과 SQL을 검토해야 합니다.",
        "",
        "## 생성 기준",
        "",
        str(result.get("generationReason", "")),
        "",
        "## Semantic Context",
        "",
        "### Metrics",
        "",
        *(metric_lines or ["- 없음"]),
        "",
        "### Tables",
        "",
        *(table_lines or ["- 없음"]),
        "",
        "### Column Metadata",
        "",
        *(
            [
                "- "
                + f"{column.get('ordinal', '')}: {column.get('name', '')} "
                + f"({column.get('inferred_type', column.get('inferredType', 'unknown'))})"
                for column in column_metadata
                if isinstance(column, dict)
            ]
            or ["- 없음"]
        ),
        "",
        "## 설명",
        "",
        str(result.get("explanation", "")),
        "",
        "## 검증 근거",
        "",
        f"- SQL validation: {'통과' if validation.get('allowed') else '차단'}",
        *validation_evidence_lines,
        f"- Validation issues: {', '.join(validation.get('issues', [])) or '없음'}",
        f"- Referenced tables: {', '.join(validation.get('referenced_tables', [])) or '없음'}",
        f"- Generation assumptions: {', '.join(generation_assumptions) if generation_assumptions else '없음'}",
        f"- Clarification options: {', '.join(assumptions) if assumptions else '없음'}",
        "",
        "## Query Plan",
        "",
        *([f"- {step}" for step in query_plan] or ["- 없음"]),
        "",
        "## Execution Trace",
        "",
        *(trace_lines or ["- 없음"]),
        "",
        "## SQL",
        "",
        "```sql",
        str(result.get("sql", "")),
        "```",
        "",
        "## 결과 미리보기",
        "",
    ]
    if not columns or not preview_rows:
        lines.append("조건에 맞는 데이터가 없습니다.")
        return "\n".join(lines)

    lines.append("| " + " | ".join(escape_markdown_table_cell(column) for column in columns) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in preview_rows:
        lines.append("| " + " | ".join(escape_markdown_table_cell(row.get(column, "")) for column in columns) + " |")
    if len(rows) > len(preview_rows):
        lines.append("")
        lines.append(f"_외 {len(rows) - len(preview_rows)}개 행 생략_")
    return "\n".join(lines)


def feedback_path() -> Path:
    return Path(os.getenv("IM_ONE_FEEDBACK_PATH", DEFAULT_FEEDBACK_PATH))


def sanitize_feedback_value(value: object, max_length: int = 2000) -> str:
    return str(value or "").strip()[:max_length]


def normalize_feedback_rating(value: object) -> str:
    normalized = sanitize_feedback_value(value, 40)
    if normalized in ALLOWED_FEEDBACK_RATINGS:
        return normalized
    return ""


def normalize_feedback_category(value: object) -> str:
    normalized = sanitize_feedback_value(value, 80)
    if normalized in ALLOWED_FEEDBACK_CATEGORIES:
        return normalized
    return "uncategorized"


def normalize_feedback_comment(value: object) -> str:
    return sanitize_feedback_value(value, MAX_FEEDBACK_COMMENT_LENGTH)


def build_feedback_event(
    payload: dict[str, Any],
    identity: RequestIdentity,
    session_result: dict[str, Any] | None,
) -> dict[str, Any]:
    event = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user_id": identity.user_id,
        "auth_mode": identity.auth_mode,
        "user_role": identity.role,
        "branch_id": identity.branch_id,
        "session_id": sanitize_feedback_value(payload.get("sessionId"), 80),
        "rating": normalize_feedback_rating(payload.get("rating")),
        "category": normalize_feedback_category(payload.get("category")),
        "comment": normalize_feedback_comment(payload.get("comment")),
    }
    if session_result:
        event.update(
            {
                "question": session_result.get("question", ""),
                "generated_sql": session_result.get("sql", ""),
                "validation_allowed": session_result.get("validation", {}).get("allowed"),
                "semantic_metrics": [metric.get("name") for metric in session_result.get("metrics", [])],
                "referenced_tables": [table.get("name") for table in session_result.get("tables", [])],
            }
        )
    return event


def append_feedback_event(event: dict[str, Any], path: Path | None = None) -> None:
    target_path = path or feedback_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_feedback_events(path: Path | None = None) -> list[dict[str, Any]]:
    target_path = path or feedback_path()
    if not target_path.exists():
        return []

    events: list[dict[str, Any]] = []
    with target_path.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def sorted_counter(counter: dict[str, int]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def increment_counter(counter: dict[str, int], key: object, fallback: str = "unknown") -> None:
    normalized_key = sanitize_feedback_value(key, 120) or fallback
    counter[normalized_key] = counter.get(normalized_key, 0) + 1


def feedback_list_values(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def backlog_key_for_event(event: dict[str, Any]) -> str:
    metrics = [sanitize_feedback_value(metric, 120) for metric in feedback_list_values(event.get("semantic_metrics"))]
    tables = [sanitize_feedback_value(table, 120) for table in feedback_list_values(event.get("referenced_tables"))]
    category = normalize_feedback_category(event.get("category"))
    if metrics:
        return f"{category}:metric:{metrics[0]}"
    if tables:
        return f"{category}:table:{tables[0]}"
    question = sanitize_feedback_value(event.get("question"), 120)
    return f"{category}:question:{question or 'unknown'}"


def build_feedback_backlog(events: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for event in events:
        key = backlog_key_for_event(event)
        item = grouped.setdefault(
            key,
            {
                "key": key,
                "category": normalize_feedback_category(event.get("category")),
                "count": 0,
                "down_count": 0,
                "unrated_count": 0,
                "metrics": set(),
                "tables": set(),
                "roles": set(),
                "sample_questions": [],
                "recent_comments": [],
                "last_seen": "",
            },
        )
        item["count"] += 1
        if normalize_feedback_rating(event.get("rating")) == "down":
            item["down_count"] += 1
        if not normalize_feedback_rating(event.get("rating")):
            item["unrated_count"] += 1
        item["last_seen"] = max(str(item["last_seen"]), sanitize_feedback_value(event.get("created_at"), 40))
        for metric in feedback_list_values(event.get("semantic_metrics")):
            normalized_metric = sanitize_feedback_value(metric, 120)
            if normalized_metric:
                item["metrics"].add(normalized_metric)
        for table in feedback_list_values(event.get("referenced_tables")):
            normalized_table = sanitize_feedback_value(table, 120)
            if normalized_table:
                item["tables"].add(normalized_table)
        role = sanitize_feedback_value(event.get("user_role"), 80)
        if role:
            item["roles"].add(role)
        question = sanitize_feedback_value(event.get("question"), 240)
        if question and question not in item["sample_questions"]:
            item["sample_questions"].append(question)
        comment = sanitize_feedback_value(event.get("comment"), 240)
        if comment and comment not in item["recent_comments"]:
            item["recent_comments"].append(comment)

    backlog = []
    for item in grouped.values():
        priority_score = item["down_count"] * 3 + item["unrated_count"] + item["count"]
        backlog.append(
            {
                "key": item["key"],
                "category": item["category"],
                "priority_score": priority_score,
                "count": item["count"],
                "down_count": item["down_count"],
                "unrated_count": item["unrated_count"],
                "metrics": sorted(item["metrics"]),
                "tables": sorted(item["tables"]),
                "roles": sorted(item["roles"]),
                "sample_questions": item["sample_questions"][:3],
                "recent_comments": item["recent_comments"][:3],
                "last_seen": item["last_seen"],
                "suggested_action": suggested_feedback_action(item["category"]),
            }
        )

    return sorted(backlog, key=lambda item: (-item["priority_score"], item["key"]))[:limit]


def suggested_feedback_action(category: str) -> str:
    return {
        "semantic_mapping": "Review metric definitions, synonyms, filters, and join paths in the semantic layer.",
        "sql_generation": "Add or adjust verified SQL examples and SQL generation rules for this question pattern.",
        "result_explanation": "Clarify explanation wording for period, aggregation, filters, assumptions, or validation evidence.",
        "ui_workflow": "Review the workbench interaction, copy/export/report flow, or monitoring visibility.",
        "correctness": "Compare the generated SQL and result shape with gold SQL before promoting the question.",
    }.get(category, "Review the captured question, generated SQL, and referenced schema before updating the catalog.")


def build_feedback_summary(path: Path | None = None, recent_limit: int = 10) -> dict[str, Any]:
    events = load_feedback_events(path)
    by_rating: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_metric: dict[str, int] = {}
    by_table: dict[str, int] = {}
    by_role: dict[str, int] = {}

    for event in events:
        increment_counter(by_rating, event.get("rating"), fallback="unrated")
        increment_counter(by_category, event.get("category"), fallback="uncategorized")
        increment_counter(by_role, event.get("user_role"), fallback="unknown")
        for metric in feedback_list_values(event.get("semantic_metrics")):
            increment_counter(by_metric, metric)
        for table in feedback_list_values(event.get("referenced_tables")):
            increment_counter(by_table, table)

    recent = [
        {
            "created_at": event.get("created_at", ""),
            "user_id": event.get("user_id", ""),
            "user_role": event.get("user_role", ""),
            "branch_id": event.get("branch_id"),
            "rating": event.get("rating", ""),
            "category": event.get("category", ""),
            "comment": event.get("comment", ""),
            "question": event.get("question", ""),
            "semantic_metrics": feedback_list_values(event.get("semantic_metrics")),
            "referenced_tables": feedback_list_values(event.get("referenced_tables")),
            "validation_allowed": event.get("validation_allowed"),
        }
        for event in reversed(events[-recent_limit:])
    ]

    return {
        "total": len(events),
        "path": str(path or feedback_path()),
        "by_rating": sorted_counter(by_rating),
        "by_category": sorted_counter(by_category),
        "by_metric": sorted_counter(by_metric),
        "by_table": sorted_counter(by_table),
        "by_role": sorted_counter(by_role),
        "semantic_backlog": build_feedback_backlog(events),
        "recent": recent,
    }


def load_audit_events(path: Path | None = None) -> list[dict[str, Any]]:
    target_path = path or Path(DEFAULT_AUDIT_PATH)
    if not target_path.exists():
        return []

    events: list[dict[str, Any]] = []
    with target_path.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def parse_audit_list_value(value: object) -> list[object]:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return parsed
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return feedback_list_values(value)


def load_database_audit_events(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    target_path = Path(db_path or DEFAULT_DB_PATH)
    if str(target_path) != ":memory:" and not target_path.exists():
        return []

    try:
        connection = connect_database(target_path)
        try:
            rows = connection.execute(
                """
                SELECT
                    audit_id,
                    created_at,
                    user_id,
                    auth_mode,
                    user_role,
                    original_question,
                    question,
                    selected_semantic_metrics,
                    semantic_metrics,
                    generated_sql,
                    llm_generated_sql,
                    policy_applied_sql,
                    validated_sql,
                    sql_policy_transformations,
                    generation_engine,
                    llm_model,
                    prompt_version,
                    validation_status,
                    execution_status,
                    validation_issues,
                    referenced_tables,
                    row_count,
                    pre_execution_row_count,
                    pre_execution_row_count_status,
                    pre_execution_check_ms,
                    query_plan_summary,
                    execution_ms,
                    blocked_reason
                FROM query_audit_log
                ORDER BY audit_id
                """
            ).fetchall()
        finally:
            connection.close()
    except Exception:
        return []

    return [
        {
            "created_at": row["created_at"],
            "timestamp": row["created_at"],
            "user_id": row["user_id"],
            "auth_mode": row["auth_mode"],
            "user_role": row["user_role"],
            "original_question": row["original_question"],
            "question": row["question"],
            "selected_semantic_metrics": parse_audit_list_value(row["selected_semantic_metrics"]),
            "semantic_metrics": parse_audit_list_value(row["semantic_metrics"]),
            "generated_sql": row["generated_sql"],
            "llm_generated_sql": row["llm_generated_sql"],
            "policy_applied_sql": row["policy_applied_sql"],
            "validated_sql": row["validated_sql"],
            "sql_policy_transformations": parse_audit_list_value(row["sql_policy_transformations"]),
            "generation_engine": row["generation_engine"],
            "llm_model": row["llm_model"],
            "prompt_version": row["prompt_version"],
            "validation_status": row["validation_status"],
            "execution_status": row["execution_status"],
            "validation_issues": parse_audit_list_value(row["validation_issues"]),
            "referenced_tables": parse_audit_list_value(row["referenced_tables"]),
            "row_count": int(row["row_count"] or 0),
            "pre_execution_row_count": (
                int(row["pre_execution_row_count"])
                if row["pre_execution_row_count"] is not None
                else None
            ),
            "pre_execution_row_count_status": row["pre_execution_row_count_status"],
            "pre_execution_check_ms": row["pre_execution_check_ms"],
            "query_plan_summary": parse_audit_list_value(row["query_plan_summary"]),
            "execution_ms": row["execution_ms"],
            "blocked_reason": row["blocked_reason"],
            "audit_source": "query_audit_log",
        }
        for row in rows
    ]


def build_audit_summary(
    path: Path | None = None,
    recent_limit: int = 20,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    database_events = load_database_audit_events(db_path) if db_path is not None else []
    events = database_events or load_audit_events(path)
    audit_source = "query_audit_log" if database_events else "jsonl"
    by_validation_status: dict[str, int] = {}
    by_execution_status: dict[str, int] = {}
    by_role: dict[str, int] = {}
    by_engine: dict[str, int] = {}
    by_model: dict[str, int] = {}
    by_metric: dict[str, int] = {}
    by_table: dict[str, int] = {}
    by_blocked_reason: dict[str, int] = {}
    total_rows_returned = 0

    for event in events:
        increment_counter(by_validation_status, event.get("validation_status"))
        increment_counter(by_execution_status, event.get("execution_status"))
        increment_counter(by_role, event.get("user_role"))
        increment_counter(by_engine, event.get("generation_engine"))
        increment_counter(by_model, event.get("llm_model"))
        row_count = event.get("row_count", 0)
        if isinstance(row_count, int):
            total_rows_returned += row_count
        for metric in feedback_list_values(event.get("semantic_metrics") or event.get("selected_semantic_metrics")):
            increment_counter(by_metric, metric)
        for table in feedback_list_values(event.get("referenced_tables")):
            increment_counter(by_table, table)
        if event.get("blocked_reason"):
            increment_counter(by_blocked_reason, event.get("blocked_reason"), fallback="blocked")

    executed_count = by_execution_status.get("executed", 0)
    blocked_count = by_execution_status.get("blocked", 0)
    failed_count = by_execution_status.get("failed", 0)
    recent = [
        {
            "created_at": event.get("created_at") or event.get("timestamp", ""),
            "user_id": event.get("user_id", ""),
            "auth_mode": event.get("auth_mode", ""),
            "user_role": event.get("user_role", ""),
            "branch_id": event.get("branch_id"),
            "question": event.get("question") or event.get("original_question", ""),
            "validation_status": event.get("validation_status", ""),
            "execution_status": event.get("execution_status", ""),
            "row_count": event.get("row_count", 0),
            "generation_engine": event.get("generation_engine", ""),
            "llm_model": event.get("llm_model", ""),
            "prompt_version": event.get("prompt_version", ""),
            "semantic_metrics": feedback_list_values(
                event.get("semantic_metrics") or event.get("selected_semantic_metrics")
            ),
            "referenced_tables": feedback_list_values(event.get("referenced_tables")),
            "blocked_reason": event.get("blocked_reason"),
        }
        for event in reversed(events[-recent_limit:])
    ]

    return {
        "total": len(events),
        "source": audit_source,
        "path": str(db_path if database_events else path or Path(DEFAULT_AUDIT_PATH)),
        "executed_count": executed_count,
        "blocked_count": blocked_count,
        "failed_count": failed_count,
        "total_rows_returned": total_rows_returned,
        "by_validation_status": sorted_counter(by_validation_status),
        "by_execution_status": sorted_counter(by_execution_status),
        "by_role": sorted_counter(by_role),
        "by_engine": sorted_counter(by_engine),
        "by_model": sorted_counter(by_model),
        "by_metric": sorted_counter(by_metric),
        "by_table": sorted_counter(by_table),
        "by_blocked_reason": sorted_counter(by_blocked_reason),
        "recent": recent,
    }


def expected_api_token() -> str | None:
    token = os.getenv("IM_ONE_API_TOKEN", "").strip()
    return token or None


def expected_trusted_proxy_token() -> str | None:
    token = os.getenv("IM_ONE_TRUSTED_PROXY_TOKEN", "").strip()
    return token or None


def token_matches(provided: object, expected: str) -> bool:
    return hmac.compare_digest(str(provided), expected)


def auth_mode() -> str:
    configured = os.getenv("IM_ONE_AUTH_MODE", "").strip().lower()
    if configured == "trusted_headers":
        return "trusted_headers"
    if expected_api_token() is not None:
        return "api_token"
    return "none"


def normalize_trusted_header_user(value: object) -> str:
    user_id = str(value or "").strip()
    if not user_id or not TRUSTED_HEADER_USER_PATTERN.fullmatch(user_id):
        return ""
    return user_id


def trusted_header_user(headers: Any) -> str:
    for header_name in ("X-IM-One-User", "X-Forwarded-User", "X-Authenticated-User"):
        raw_user_id = headers.get(header_name, "")
        if str(raw_user_id or "").strip():
            return normalize_trusted_header_user(raw_user_id)
    return ""


def is_trusted_header_authorized(headers: Any) -> bool:
    if not trusted_header_user(headers):
        return False

    expected_proxy_token = expected_trusted_proxy_token()
    if expected_proxy_token is None:
        return True

    return token_matches(headers.get("X-IM-One-Trusted-Proxy-Token", ""), expected_proxy_token)


def is_authorized(headers: Any, token: str | None = None) -> bool:
    if auth_mode() == "trusted_headers":
        return is_trusted_header_authorized(headers)

    expected = expected_api_token() if token is None else token
    if expected is None:
        return True

    authorization = str(headers.get("Authorization", ""))
    bearer_prefix = "Bearer "
    if authorization.startswith(bearer_prefix) and token_matches(authorization[len(bearer_prefix):], expected):
        return True
    return token_matches(headers.get("X-IM-One-Token", ""), expected)


def resolve_request_identity(headers: Any, payload_role: str, payload_branch_id: int) -> RequestIdentity:
    mode = auth_mode()
    if mode == "trusted_headers":
        role = normalize_user_role(str(headers.get("X-IM-One-Role", "")).strip())
        branch_id = normalize_branch_id(str(headers.get("X-IM-One-Branch-ID", "")).strip() or DEFAULT_BRANCH_ID)
        return RequestIdentity(
            user_id=trusted_header_user(headers),
            role=role,
            branch_id=branch_id,
            auth_mode=mode,
        )

    return RequestIdentity(
        user_id="local-demo",
        role=normalize_user_role(payload_role),
        branch_id=normalize_branch_id(payload_branch_id),
        auth_mode=mode,
    )


def increment_metric(name: str, amount: int = 1) -> None:
    RUNTIME_METRICS[name] = RUNTIME_METRICS.get(name, 0) + amount


def store_session_result(session_id: str, response: dict[str, Any]) -> None:
    if session_id in SESSION_CONTEXTS:
        SESSION_CONTEXTS.pop(session_id, None)
        SESSION_RESULTS.pop(session_id, None)

    SESSION_CONTEXTS[session_id] = sanitize_conversation_context(response.get("conversationContext", {}))
    SESSION_RESULTS[session_id] = response

    while len(SESSION_CONTEXTS) > MAX_SESSIONS:
        oldest_session_id = next(iter(SESSION_CONTEXTS))
        SESSION_CONTEXTS.pop(oldest_session_id, None)
        SESSION_RESULTS.pop(oldest_session_id, None)


def session_result_accessible(result: dict[str, Any], identity: RequestIdentity) -> bool:
    result_auth_mode = str(result.get("authMode", "none"))
    result_user_id = str(result.get("userId", result.get("user_id", "")))
    result_role = normalize_user_role(str(result.get("role", result.get("user_role", ""))))
    result_branch_id = normalize_branch_id(result.get("branchId", result.get("branch_id")))

    if identity.auth_mode == "none" and result_auth_mode == "none":
        return True
    if identity.auth_mode != result_auth_mode:
        return False
    if identity.auth_mode == "trusted_headers":
        return (
            result_user_id == identity.user_id
            and result_role == identity.role
            and result_branch_id == identity.branch_id
        )
    if identity.auth_mode == "api_token":
        return result_user_id == identity.user_id
    return False


def session_result_for_feedback(session_id: str, identity: RequestIdentity) -> tuple[dict[str, Any] | None, HTTPStatus | None, str | None]:
    result = SESSION_RESULTS.get(session_id)
    if not result:
        return None, HTTPStatus.NOT_FOUND, "피드백을 남길 실행 결과가 없습니다."
    if not session_result_accessible(result, identity):
        return None, HTTPStatus.FORBIDDEN, "해당 세션 결과에 접근할 권한이 없습니다."
    return result, None, None


def endpoint_health_payload(
    configured: bool,
    model: str,
    base_url: str,
    auth: str,
    include_sensitive: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "configured": configured,
        "auth": auth,
    }
    if include_sensitive:
        payload["model"] = model
        payload["base_url"] = base_url
    return payload


def build_health_payload(db_path: str, include_sensitive: bool = False) -> dict[str, Any]:
    db_ok = True
    db_error = None
    try:
        ensure_demo_database(db_path)
    except Exception as exc:  # pragma: no cover - defensive health reporting
        db_ok = False
        db_error = str(exc)

    database_payload: dict[str, Any] = {"ok": db_ok, "backend": configured_database_backend_name()}
    if include_sensitive:
        database_payload["path"] = db_path
        database_payload["error"] = db_error
    elif db_error:
        database_payload["error"] = "unavailable"

    llm_base_url = configured_llm_base_url()
    embedding_base_url = configured_embedding_base_url()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": database_payload,
        "llm": endpoint_health_payload(
            llm_endpoint_configured(),
            configured_llm_model(),
            llm_base_url,
            endpoint_auth_status(local_llm_no_auth_enabled(llm_base_url)),
            include_sensitive=include_sensitive,
        ),
        "embedding": endpoint_health_payload(
            remote_embeddings_configured(),
            configured_embedding_model(),
            embedding_base_url,
            endpoint_auth_status(local_embedding_no_auth_enabled(embedding_base_url)),
            include_sensitive=include_sensitive,
        ),
        "auth": {
            "mode": auth_mode(),
            "api_token_required": expected_api_token() is not None,
            "trusted_headers_required": auth_mode() == "trusted_headers",
            "trusted_proxy_token_required": auth_mode() == "trusted_headers"
            and expected_trusted_proxy_token() is not None,
        },
        "uptime_seconds": round(time.time() - PROCESS_STARTED_AT, 3),
    }


def endpoint_auth_status(local_no_auth_enabled: bool) -> str:
    if os.getenv("OPENAI_API_KEY"):
        return "api_key"
    if local_no_auth_enabled:
        return "local_no_auth"
    return "missing"


def build_metrics_payload() -> dict[str, Any]:
    return {
        "metrics": dict(sorted(RUNTIME_METRICS.items())),
        "sessions": {
            "contexts": len(SESSION_CONTEXTS),
            "results": len(SESSION_RESULTS),
        },
        "uptime_seconds": round(time.time() - PROCESS_STARTED_AT, 3),
    }


def normalize_readiness_profile(value: object | None) -> str | None:
    profile = str(value or "").strip().lower()
    if not profile:
        return None
    if profile in {"poc", "pilot"}:
        return profile
    raise PayloadValidationError("profile은 poc 또는 pilot이어야 합니다.")


def normalize_readiness_live_checks(value: object | None) -> bool:
    raw_value = str(value or "").strip().lower()
    if not raw_value:
        return False
    if raw_value in {"1", "true", "yes", "y", "on"}:
        return True
    if raw_value in {"0", "false", "no", "n", "off"}:
        return False
    raise PayloadValidationError("live는 true 또는 false여야 합니다.")


def build_prd_evaluation_gate_payload(profile: str | None) -> dict[str, Any]:
    summary = build_evaluation_case_summary()
    coverage_metrics = {
        "total_cases": summary["total_cases"],
        "core_demo_total": summary["core_cases"],
        "non_blocked_total": summary["non_blocked_cases"],
        "blocked_total": summary["blocked_cases"],
        "gold_compared_total": summary["gold_covered_cases"],
    }
    coverage_thresholds = {
        key: PRD_EVALUATION_THRESHOLDS[key]
        for key in (
            "min_total_cases",
            "min_core_demo_total",
            "min_non_blocked_total",
            "min_blocked_total",
            "min_gold_compared_total",
        )
    }
    coverage_failures = evaluation_threshold_failures(
        coverage_metrics,
        **coverage_thresholds,
    )
    coverage_gate = {
        "passed": not coverage_failures,
        "status": "passed" if not coverage_failures else "failed",
        "failure_count": len(coverage_failures),
        "failures": [
            {
                "name": "prd_evaluation_coverage_failed",
                "count": len(coverage_failures),
                "details": list(coverage_failures),
            }
        ]
        if coverage_failures
        else [],
        "metrics": coverage_metrics,
        "thresholds": coverage_thresholds,
    }
    if not profile:
        return {
            "passed": coverage_gate["passed"],
            "status": "not_required",
            "failure_count": coverage_gate["failure_count"],
            "failures": coverage_gate["failures"],
            "thresholds": PRD_EVALUATION_THRESHOLDS,
            "coverage": summary,
            "coverage_gate": coverage_gate,
        }
    failures = [
        {
            "name": "prd_evaluation_evidence_not_run",
            "count": 1,
            "details": [
                "Run python -m im_one_agent.evidence --profile "
                f"{profile} --live-checks --strict or python -m im_one_agent.evaluate --strict-prd."
            ],
        }
    ] + coverage_gate["failures"]
    return {
        "passed": False,
        "status": "not_run",
        "failure_count": len(failures),
        "failures": failures,
        "thresholds": PRD_EVALUATION_THRESHOLDS,
        "coverage": summary,
        "coverage_gate": coverage_gate,
    }


def build_readiness_gate_payload(
    report: dict[str, Any],
    profile_requests_live_checks: bool,
    live_checks: bool,
    prd_evaluation_gate: dict[str, Any],
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    required_failed = int(report["summary"]["required_failed"])
    if required_failed:
        failures.append(
            {
                "name": "required_readiness_failed",
                "count": required_failed,
                "details": report["summary"]["required_failed_names"],
            }
        )
    if profile_requests_live_checks and not live_checks:
        failures.append(
            {
                "name": "live_checks_not_run",
                "count": 1,
                "details": ["live=true was not requested for a profile that requires live LLM or embedding checks."],
            }
        )
    if not prd_evaluation_gate["passed"]:
        failures.append(
            {
                "name": "prd_evaluation_gate_not_passed",
                "count": prd_evaluation_gate["failure_count"],
                "details": [detail for failure in prd_evaluation_gate["failures"] for detail in failure["details"]],
            }
        )
    return {
        "passed": not failures,
        "status": "passed" if not failures else "failed",
        "failure_count": len(failures),
        "failures": failures,
    }


def build_readiness_payload(
    db_path: str,
    profile: str | None = None,
    live_checks: bool = False,
) -> dict[str, Any]:
    requirements = preflight_requirements_for_profile(profile)
    profile_requests_live_checks = bool(requirements.get("check_llm") or requirements.get("check_embedding"))
    if not live_checks:
        requirements["check_llm"] = False
        requirements["check_embedding"] = False
    checks = run_preflight(db_path=db_path, **requirements)
    report = build_preflight_report(checks, profile=profile, db_path=db_path)
    report["profile_applied"] = profile is not None
    report["live_checks_enabled"] = live_checks
    report["live_checks_requested"] = live_checks and profile_requests_live_checks
    report["prd_evaluation_gate"] = build_prd_evaluation_gate_payload(profile)
    report["readiness_gate"] = build_readiness_gate_payload(
        report,
        profile_requests_live_checks,
        live_checks,
        report["prd_evaluation_gate"],
    )
    return report


def metric_is_visible_for_role(metric_tables: tuple[str, ...], allowed_tables: set[str]) -> bool:
    return set(metric_tables).issubset(allowed_tables)


def build_catalog_payload(role: str = "branch_manager") -> dict[str, Any]:
    normalized_role = normalize_user_role(role)
    allowed_tables = ROLE_TABLE_POLICY[normalized_role]
    visible_tables = [
        serialize(table)
        for table in TABLES.values()
        if table.name in allowed_tables and table.name != "query_audit_log"
    ]
    visible_metrics = [
        serialize(metric)
        for metric in METRICS
        if metric_is_visible_for_role(metric.tables, allowed_tables)
    ]

    return {
        "asOfDate": AS_OF_DATE,
        "syntheticData": True,
        "role": normalized_role,
        "allowedTables": sorted(allowed_tables),
        "tables": visible_tables,
        "metrics": visible_metrics,
        "businessRules": list(BUSINESS_RULES),
        "roles": {
            role_name: sorted(table_names)
            for role_name, table_names in sorted(ROLE_TABLE_POLICY.items())
        },
    }


def build_catalog_governance_payload(role: str = "branch_manager") -> dict[str, Any]:
    normalized_role = normalize_user_role(role)
    catalog = build_catalog_payload(normalized_role)
    issues: list[dict[str, Any]] = []
    table_names = set(TABLES)
    business_table_names = {name for name in table_names if name not in {"query_audit_log", "demo_dataset_metadata"}}

    for metric in METRICS:
        missing_fields = [
            field_name
            for field_name in (
                "description",
                "definition",
                "keywords",
                "tables",
                "related_columns",
                "date_column",
                "default_period",
                "join_paths",
                "default_grouping",
                "sample_question",
            )
            if not getattr(metric, field_name)
        ]
        if missing_fields:
            issues.append(
                {
                    "metric": metric.name,
                    "severity": "error",
                    "issue": "missing_fields",
                    "detail": ", ".join(missing_fields),
                }
            )

        unknown_tables = sorted(set(metric.tables) - table_names)
        if unknown_tables:
            issues.append(
                {
                    "metric": metric.name,
                    "severity": "error",
                    "issue": "unknown_tables",
                    "detail": ", ".join(unknown_tables),
                }
            )

        unknown_columns = metric_unknown_columns(metric)
        if unknown_columns:
            issues.append(
                {
                    "metric": metric.name,
                    "severity": "error",
                    "issue": "unknown_columns",
                    "detail": ", ".join(unknown_columns[:5]),
                }
            )

    exposed_metric_names = {metric["name"] for metric in catalog["metrics"]}
    role_coverage = {
        role_name: {
            "allowed_tables": sorted(table_names_for_role - {"query_audit_log", "demo_dataset_metadata"}),
            "visible_metric_count": sum(
                1 for metric in METRICS if metric_is_visible_for_role(metric.tables, table_names_for_role)
            ),
        }
        for role_name, table_names_for_role in sorted(ROLE_TABLE_POLICY.items())
    }
    orphan_business_tables = sorted(
        table_name
        for table_name in business_table_names
        if not any(table_name in metric.tables for metric in METRICS)
    )

    if orphan_business_tables:
        issues.append(
            {
                "metric": "",
                "severity": "warning",
                "issue": "tables_without_metrics",
                "detail": ", ".join(orphan_business_tables),
            }
        )

    return {
        "status": "passed" if not any(issue["severity"] == "error" for issue in issues) else "failed",
        "role": normalized_role,
        "asOfDate": AS_OF_DATE,
        "syntheticData": True,
        "metricCount": len(METRICS),
        "visibleMetricCount": len(exposed_metric_names),
        "tableCount": len(catalog["tables"]),
        "issueCount": len(issues),
        "issues": issues,
        "roleCoverage": role_coverage,
        "requiredMetricFields": [
            "description",
            "definition",
            "keywords",
            "tables",
            "related_columns",
            "date_column",
            "default_period",
            "join_paths",
            "default_grouping",
            "sample_question",
        ],
    }


def metric_unknown_columns(metric: Any) -> list[str]:
    unknown: list[str] = []
    for column_ref in metric.related_columns:
        if "." not in column_ref:
            unknown.append(str(column_ref))
            continue
        table_name, column_name = column_ref.split(".", 1)
        table = TABLES.get(table_name)
        if table is None or column_name not in table.columns:
            unknown.append(str(column_ref))
    return unknown


def build_metric_selection_reasons(context: Any) -> list[dict[str, Any]]:
    matched_metric_names = {metric.name for metric in context.matched_metrics}
    metric_by_name = {metric.name: metric for metric in context.matched_metrics}
    reasons: list[dict[str, Any]] = []
    for score in context.retrieval_scores:
        reason_parts = [
            f"keyword_hits={score.keyword_hits}",
            f"token_overlap={score.token_overlap}",
            f"vector_similarity={score.vector_similarity}",
            f"total_score={score.total_score}",
        ]
        metric = metric_by_name.get(score.metric_name)
        reasons.append(
            {
                "metric": score.metric_name,
                "selected": score.metric_name in matched_metric_names,
                "reason": ", ".join(reason_parts),
                "keywordHits": score.keyword_hits,
                "tokenOverlap": score.token_overlap,
                "vectorSimilarity": score.vector_similarity,
                "totalScore": score.total_score,
                "embeddingSource": score.embedding_source,
                "tables": list(metric.tables) if metric else [],
            }
        )
    return reasons


def build_table_selection_reasons(context: Any) -> list[dict[str, Any]]:
    matched_metrics = context.matched_metrics
    table_reasons: list[dict[str, Any]] = []
    for table in context.tables:
        source_metrics = [metric.name for metric in matched_metrics if table.name in metric.tables]
        table_reasons.append(
            {
                "table": table.name,
                "sourceMetrics": source_metrics,
                "reason": f"selected from matched metrics: {', '.join(source_metrics) or 'none'}",
            }
        )
    return table_reasons


def build_execution_trace(result: dict[str, Any], graph_runtime: str) -> list[dict[str, Any]]:
    validation = result["validation"]
    context = result["context"]
    generated = result["generated"]
    rows = result.get("rows", [])
    columns = result.get("columns", [])
    column_metadata = result.get("column_metadata", [])
    query_plan = result.get("query_plan_summary", [])
    pre_execution_row_count = result.get("pre_execution_row_count")
    pre_execution_row_count_status = result.get("pre_execution_row_count_status")
    pre_execution_check_ms = result.get("pre_execution_check_ms")
    metrics = [metric.name for metric in context.matched_metrics]
    table_names = [table.name for table in context.tables]
    retrieval_scores = [
        {
            "metric": score.metric_name,
            "keywordHits": score.keyword_hits,
            "tokenOverlap": score.token_overlap,
            "vectorSimilarity": score.vector_similarity,
            "totalScore": score.total_score,
            "embeddingSource": score.embedding_source,
        }
        for score in context.retrieval_scores
    ]
    metric_selection_reasons = build_metric_selection_reasons(context)
    table_selection_reasons = build_table_selection_reasons(context)
    execution_failed = has_execution_failure(result)
    if validation.allowed:
        execution_status = "executed"
        execution_detail = f"{len(rows)} rows, {len(columns)} columns"
        if pre_execution_row_count is not None:
            execution_detail = f"{execution_detail} · precheck {pre_execution_row_count} rows"
        if query_plan:
            execution_detail = f"{execution_detail} · plan {len(query_plan)} steps"
    elif execution_failed:
        execution_status = "failed"
        execution_detail = f"failed after {result.get('execution_ms')} ms"
    else:
        execution_status = "skipped"
        execution_detail = "skipped"
    semantic_detail = ", ".join(metrics) if metrics else "-"
    if metrics:
        semantic_detail = f"{semantic_detail} · confidence={context.retrieval_confidence}"
    schema_detail = ", ".join(table_names) or "-"
    if metric_selection_reasons:
        selected_reason = next(
            (reason for reason in metric_selection_reasons if reason["selected"]),
            metric_selection_reasons[0],
        )
        schema_detail = f"{schema_detail} · reason={selected_reason['metric']} ({selected_reason['reason']})"
    generation_detail = " / ".join(
        part
        for part in (generated.engine, generated.model, generated.prompt_version)
        if part
    )
    database_audit_status = str(
        result.get(
            "database_audit_status",
            "skipped_read_only" if os.getenv("IM_ONE_DB_READONLY") else "recorded",
        )
    )
    database_audit_error = result.get("database_audit_error")
    audit_trace_status = "partial" if database_audit_status == "failed" else "recorded"
    audit_trace_detail = {
        "recorded": "jsonl + query_audit_log",
        "skipped_read_only": "jsonl; query_audit_log skipped in read-only mode",
        "failed": "jsonl + query_audit_log failed",
    }.get(database_audit_status, f"jsonl + query_audit_log status={database_audit_status}")

    return [
        {
            "node": "Question Intake",
            "status": "completed",
            "detail": f"role={result.get('user_role', 'branch_manager')}, branch_id={result.get('branch_id', 1)}",
            "metadata": {
                "question": result["question"],
                "graphRuntime": graph_runtime,
                "userId": result.get("user_id", "local-demo"),
                "authMode": result.get("auth_mode", "none"),
            },
        },
        {
            "node": "Semantic Layer",
            "status": "matched" if metrics else "empty",
            "detail": semantic_detail,
            "metadata": {
                "metrics": metrics,
                "confidence": context.retrieval_confidence,
                "clarificationOptions": list(context.clarification_options),
            },
        },
        {
            "node": "Schema Retrieval",
            "status": "selected" if table_names else "empty",
            "detail": schema_detail,
            "metadata": {
                "tables": table_names,
                "retrievalScores": retrieval_scores,
                "metricSelectionReasons": metric_selection_reasons,
                "tableSelectionReasons": table_selection_reasons,
            },
        },
        {
            "node": "SQL Generation",
            "status": "blocked" if generated.error else "completed",
            "detail": generation_detail,
            "metadata": {
                "engine": generated.engine,
                "model": generated.model,
                "promptVersion": generated.prompt_version,
                "reason": generated.reason,
                "error": generated.error,
                "assumptions": list(generated.assumptions),
                "llmGeneratedSql": result.get("llm_generated_sql", generated.sql),
                "policyAppliedSql": result.get("policy_applied_sql", validation.sql),
                "policyTransformations": list(result.get("sql_policy_transformations", [])),
            },
        },
        {
            "node": "SQL Validation",
            "status": "passed" if validation.allowed else "blocked",
            "detail": "passed" if validation.allowed else "; ".join(validation.issues),
            "metadata": {
                "issues": list(validation.issues),
                "referencedTables": list(validation.referenced_tables),
                "validatedSql": validation.sql,
            },
        },
        {
            "node": "Query Execution",
            "status": execution_status,
            "detail": execution_detail,
            "metadata": {
                "rowCount": len(rows),
                "columns": columns,
                "columnMetadata": column_metadata,
                "queryPlan": query_plan,
                "preExecutionRowCount": pre_execution_row_count,
                "preExecutionRowCountStatus": pre_execution_row_count_status,
                "preExecutionCheckMs": pre_execution_check_ms,
                "executionMs": result.get("execution_ms"),
                "referencedTables": list(validation.referenced_tables),
            },
        },
        {
            "node": "Audit Log",
            "status": audit_trace_status,
            "detail": audit_trace_detail,
            "metadata": {
                "auditPath": result.get("audit_path", DEFAULT_AUDIT_PATH),
                "databaseAuditStatus": database_audit_status,
                "databaseAuditError": database_audit_error,
                "databaseAuditSkipped": database_audit_status == "skipped_read_only",
            },
        },
    ]


def run_agent(
    question: str,
    role: str,
    branch_id: int,
    conversation_context: dict[str, Any],
    db_path: str,
    audit_path: str,
    user_id: str = "local-demo",
    request_auth_mode: str = "none",
) -> dict[str, Any]:
    ensure_demo_database(db_path)
    agent = build_agent()
    result = agent.invoke(
        {
            "question": question,
            "user_id": user_id,
            "auth_mode": request_auth_mode,
            "user_role": role,
            "branch_id": branch_id,
            "conversation_context": conversation_context,
            "db_path": db_path,
            "audit_path": audit_path,
        }
    )

    validation = result["validation"]
    context = result["context"]
    generated = result["generated"]
    graph_runtime = agent.__class__.__name__
    normalized_role = str(result.get("user_role", role))
    normalized_branch_id = int(result.get("branch_id", branch_id))

    next_context = sanitize_conversation_context({
        "previous_question": question,
        "previous_sql": validation.sql,
        "previous_columns": result.get("columns", []),
        "previous_row_count": len(result.get("rows", [])),
        "previous_rows_sample": result.get("rows", [])[:5],
        "previous_metrics": [metric.name for metric in context.matched_metrics],
        "previous_tables": [table.name for table in context.tables],
        "previous_validation_allowed": validation.allowed,
    })

    return {
        "question": question,
        "user_id": user_id,
        "userId": user_id,
        "authMode": request_auth_mode,
        "user_role": normalized_role,
        "role": normalized_role,
        "branch_id": normalized_branch_id,
        "branchId": normalized_branch_id,
        "conversationContext": serialize(next_context),
        "columns": result.get("columns", []),
        "columnMetadata": result.get("column_metadata", []),
        "queryPlan": result.get("query_plan_summary", []),
        "preExecutionRowCount": result.get("pre_execution_row_count"),
        "preExecutionRowCountStatus": result.get("pre_execution_row_count_status"),
        "preExecutionCheckMs": result.get("pre_execution_check_ms"),
        "rows": result.get("rows", []),
        "rowCount": len(result.get("rows", [])),
        "executionMs": result.get("execution_ms"),
        "sql": validation.sql,
        "generatedSql": result.get("llm_generated_sql", generated.sql),
        "llmGeneratedSql": result.get("llm_generated_sql", generated.sql),
        "policyAppliedSql": result.get("policy_applied_sql", validation.sql),
        "validatedSql": validation.sql,
        "sqlPolicyTransformations": list(result.get("sql_policy_transformations", [])),
        "validation": serialize(validation),
        "metrics": [serialize(metric) for metric in context.matched_metrics],
        "tables": [serialize(table) for table in context.tables],
        "businessRules": list(context.business_rules),
        "retrievalConfidence": context.retrieval_confidence,
        "clarificationOptions": list(context.clarification_options),
        "generationReason": generated.reason,
        "generationAssumptions": list(generated.assumptions),
        "generationEngine": generated.engine,
        "llmModel": generated.model,
        "promptVersion": generated.prompt_version,
        "databaseAuditStatus": result.get("database_audit_status"),
        "databaseAuditError": result.get("database_audit_error"),
        "retryGuidance": retry_guidance_for(validation),
        "explanation": result.get("explanation", ""),
        "answer": result.get("answer", ""),
        "executionTrace": serialize(
            build_execution_trace({**result, "user_role": normalized_role, "branch_id": normalized_branch_id}, graph_runtime)
        ),
        "graphRuntime": graph_runtime,
    }
