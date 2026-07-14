from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from im_one_agent.database_backend import (
    available_database_backend_names,
    configured_database_backend_name,
    execution_backend_for_name,
)
from im_one_agent.domain import METRICS, ROLE_TABLE_POLICY, TABLES
from im_one_agent.env import load_project_env
from im_one_agent.evaluation import EVALUATION_CASES, build_verified_question_manifest, gold_sql_for_case
from im_one_agent.graph import StateGraph, build_agent, execute_sql_node, query_timeout_ms, write_audit_node
from im_one_agent.response import build_explanation
from im_one_agent.sample_data import (
    REQUIRED_DATASET_METADATA,
    REQUIRED_AUDIT_TRIGGERS,
    SYNTHETIC_BRANCH_NAME_MARKER,
    connect_database,
    database_has_required_dataset_metadata,
    ensure_demo_database,
    is_read_only_database,
)
from im_one_agent.schema_retrieval import (
    EmbeddingError,
    extend_schema_with_follow_up_context,
    configured_embedding_base_url,
    configured_embedding_model,
    local_embedding_no_auth_enabled,
    remote_embedding,
    remote_embeddings_configured,
    retrieve_schema,
    score_metric,
)
from im_one_agent.sql_generator import (
    GeneratedSQL,
    LLM_TEMPERATURE,
    LLM_TOP_P,
    LLMGenerationError,
    PROMPT_VERSION,
    build_llm_payload,
    configured_llm_base_url,
    configured_llm_model,
    generate_sql_with_llm,
    llm_endpoint_configured,
    llm_timeout_seconds,
    local_llm_no_auth_enabled,
)
from im_one_agent.sql_safety import sqlglot, validate_sql

load_project_env()

DEFAULT_FEEDBACK_PATH = "logs/feedback.jsonl"
LLM_E2E_LATENCY_TARGET_MS = 10000.0
MIN_REMOTE_EMBEDDING_DIMENSIONS = 3
REPO_ROOT = Path(__file__).resolve().parents[4]
PRD_DOC_PATH = REPO_ROOT / "docs/prd.md"
STATIC_DIR = REPO_ROOT / "apps" / "web"
REQUIRED_STATIC_FILES = (
    "index.html",
    "package.json",
    "src/App.tsx",
    "src/api.ts",
    "src/app-state.tsx",
    "src/components/AssistantComposer.tsx",
    "src/components/Shell.tsx",
    "src/components/VirtualTable.tsx",
    "src/pages/DataPage.tsx",
    "src/pages/ExpensePage.tsx",
    "src/pages/HomePage.tsx",
    "src/pages/KnowledgePage.tsx",
    "src/styles.css",
)
REQUIRED_INDEX_REFERENCES = ('lang="ko"', 'name="viewport"', 'id="root"', 'src="/src/main.tsx"')
REQUIRED_CSP_DIRECTIVES = (
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "connect-src 'self'",
    # frame-ancestors is checked separately because it must deny all embedding.
)
REQUIRED_STATIC_UI_SELECTORS = (
    'className="brand-button"',
    'value="branch_manager"',
    'value="sales_planning"',
    'value="compliance"',
    'className="mobile-nav"',
    "assistant-composer",
    'className="result-table-shell"',
    'className="evidence-drawer"',
)
REQUIRED_STATIC_UI_TEXT = (
    "무엇을 도와드릴까요?",
    "업무지식",
    "데이터 분석",
    "지출품의",
    "분석 근거",
)
REQUIRED_STATIC_APP_PATTERNS = (
    'fetch("/api/v1/assistant/messages"',
    "parseSseFrame(frame)",
    "applyPayload(event.data.payload)",
    'path="knowledge"',
    'path="data"',
    'path="expenses"',
    "recommendChart(data)",
    "data.executionTrace?.map",
)
REQUIRED_STATIC_HTML_LAYOUT_PATTERNS = (
    '<html lang="ko">',
    '<meta name="viewport"',
    '<div id="root"></div>',
    '<script type="module" src="/src/main.tsx"></script>',
)
REQUIRED_STATIC_APP_LAYOUT_PATTERNS = (
    "navigate(routeByWorkspace[result.workspace])",
    '<AssistantComposer hint="knowledge"',
    '<AssistantComposer hint="data"',
    '<AssistantComposer hint="expense"',
    "<VirtualTable columns={data.columns} rows={data.rows}",
    '<Dialog.Content className="evidence-drawer">',
    "const ROW_HEIGHT = 44",
    "rows.slice(range.start, range.end)",
)
REQUIRED_STATIC_CSS_LAYOUT_PATTERNS = (
    "grid-template-rows: 58px minmax(0, 1fr);",
    "min-width: 720px;",
    "position: sticky;",
    "overflow-y: auto;",
    "@media (max-width: 1040px)",
    "@media (max-width: 760px)",
    "@media (max-width: 420px)",
    ".desktop-nav { display: none; }",
    ".mobile-nav {",
)
REQUIRED_PUBLIC_GET_PATHS = {"/api/health", "/api/demo-questions"}
REQUIRED_PROTECTED_GET_PATHS = {
    "/api/metrics",
    "/api/feedback-summary",
    "/api/audit-summary",
    "/api/catalog",
    "/api/catalog-governance",
    "/api/evaluation-summary",
    "/api/readiness",
    "/api/verified-questions",
}
REQUIRED_PROTECTED_POST_PATHS = {"/api/query", "/api/export", "/api/feedback"}
SYNTHETIC_BUSINESS_TABLES = (
    "branches",
    "accounts",
    "product_sales",
    "voc_cases",
    "investment_reviews",
    "branch_targets",
)
REQUIRED_ROLES = ("branch_manager", "sales_planning", "compliance")
OPERATIONAL_ONLY_TABLES = {"query_audit_log"}
SQL_VALIDATION_POLICY_PROBES: tuple[tuple[str, str, bool, int | None], ...] = (
    (
        "safe_aggregate",
        "SELECT branch_id, COUNT(*) AS account_count FROM accounts WHERE branch_id = 1 GROUP BY branch_id LIMIT 10",
        True,
        1,
    ),
    ("dml_block", "DELETE FROM accounts LIMIT 10", False, None),
    ("multi_statement_block", "SELECT branch_id FROM branches LIMIT 10; SELECT branch_id FROM branches LIMIT 10", False, None),
    ("comment_block", "SELECT branch_id FROM branches -- hidden clause\nLIMIT 10", False, None),
    ("pragma_block", "PRAGMA database_list", False, None),
    ("select_star_block", "SELECT * FROM accounts LIMIT 10", False, None),
    ("unknown_table_block", "SELECT customer_id FROM customer_private LIMIT 10", False, None),
    ("unknown_column_block", "SELECT missing_column FROM branches LIMIT 10", False, None),
    ("syntax_error_block", "SELECT FROM branches LIMIT 10", False, None),
    ("operational_table_block", "SELECT audit_id FROM query_audit_log LIMIT 10", False, None),
    (
        "large_limit_block",
        "SELECT branch_id, COUNT(*) AS account_count FROM accounts GROUP BY branch_id LIMIT 101",
        False,
        None,
    ),
    ("recursive_cte_block", "WITH RECURSIVE cnt(x) AS (VALUES(1) UNION ALL SELECT x + 1 FROM cnt) SELECT x FROM cnt LIMIT 10", False, None),
    ("set_operation_block", "SELECT branch_id FROM branches UNION SELECT branch_id FROM branches LIMIT 10", False, None),
    ("dangerous_function_block", "SELECT readfile('x') AS file_value LIMIT 10", False, None),
    (
        "cartesian_join_block",
        "SELECT a.branch_id, COUNT(*) AS joined_count FROM accounts a CROSS JOIN voc_cases v GROUP BY a.branch_id LIMIT 10",
        False,
        None,
    ),
    (
        "large_offset_block",
        "SELECT branch_id, COUNT(*) AS account_count FROM accounts GROUP BY branch_id LIMIT 10 OFFSET 1001",
        False,
        None,
    ),
    (
        "random_order_block",
        "SELECT branch_id, COUNT(*) AS account_count FROM accounts GROUP BY branch_id ORDER BY RANDOM() LIMIT 10",
        False,
        None,
    ),
    ("raw_event_detail_block", "SELECT opened_at, channel FROM accounts LIMIT 10", False, None),
    (
        "branch_scope_block",
        "SELECT branch_id, COUNT(*) AS account_count FROM accounts GROUP BY branch_id LIMIT 10",
        False,
        1,
    ),
)
SCHEMA_RETRIEVAL_POLICY_PROBES: tuple[dict[str, object], ...] = (
    {
        "name": "voc_status",
        "question": "최근 30일 VOC 유형별 처리 현황 알려줘.",
        "role": "sales_planning",
        "required_tables": {"voc_cases"},
        "forbidden_tables": {"accounts", "product_sales", "investment_reviews", "branch_targets"},
        "minimum_confidence": "high",
    },
    {
        "name": "els_sales_vs_voc",
        "question": "영업점별 ELS 가입 금액과 민원 건수를 비교해줘.",
        "role": "sales_planning",
        "required_tables": {"branches", "product_sales", "voc_cases"},
        "forbidden_tables": {"accounts", "investment_reviews", "branch_targets"},
        "minimum_confidence": "high",
    },
    {
        "name": "high_risk_product_sales",
        "question": "이번 달 위험한 상품 많이 판 지점 알려줘.",
        "role": "sales_planning",
        "required_tables": {"branches", "product_sales"},
        "forbidden_tables": {"accounts", "voc_cases", "investment_reviews", "branch_targets"},
        "minimum_confidence": "high",
    },
)
REQUIRED_TRACE_NODES = (
    "Question Intake",
    "Semantic Layer",
    "Schema Retrieval",
    "SQL Generation",
    "SQL Validation",
    "Query Execution",
    "Audit Log",
)
REQUIRED_AUDIT_EVENT_FIELDS = (
    "timestamp",
    "user_id",
    "auth_mode",
    "user_role",
    "original_question",
    "selected_semantic_metrics",
    "generated_sql",
    "llm_generated_sql",
    "policy_applied_sql",
    "validated_sql",
    "sql_policy_transformations",
    "generation_engine",
    "llm_model",
    "prompt_version",
    "validation_status",
    "execution_status",
    "row_count",
    "pre_execution_row_count",
    "pre_execution_row_count_status",
    "pre_execution_check_ms",
    "query_plan_summary",
    "blocked_reason",
)
PRD_FORBIDDEN_INTERNAL_WORDING = (
    "fallback",
    "규칙 기반 SQL 생성",
    "네트워크나 API 키",
    "API 키가 없을 때",
    "데모를 유지하기 위한 fallback",
)
FORBIDDEN_SYNTHETIC_COLUMNS = {
    "account_no",
    "account_number",
    "address",
    "birth_date",
    "client_name",
    "contact",
    "customer_id",
    "customer_name",
    "email",
    "employee_id",
    "employee_name",
    "mobile_number",
    "mobile_phone",
    "name",
    "phone",
    "phone_number",
    "resident_registration_number",
    "rrn",
    "ssn",
    "staff_name",
}
SENSITIVE_COLUMN_PATTERNS = {
    "account_number": re.compile(r"(^|_)(account|acct)_(no|num|number)(_|$)"),
    "email": re.compile(r"(^|_)email(_|$)|(^|_)email_address(_|$)"),
    "address": re.compile(r"(^|_)(address|addr|home_addr|road_addr)(_|$)"),
    "customer_identifier": re.compile(r"(^|_)(customer|client|cust)_(id|no|num|number)(_|$)"),
    "employee_identifier": re.compile(r"(^|_)(employee|staff|emp)_(id|no|num|number)(_|$)"),
    "name": re.compile(r"(^|_)(client|customer|employee|staff|real|full|korean)_name(_|$)"),
    "phone": re.compile(r"(^|_)(phone|tel|telephone|mobile|contact)_(no|num|number|phone)(_|$)"),
    "resident_registration_number": re.compile(r"(^|_)(rrn|ssn|resident_no|resident_number|jumin)(_|$)"),
}
SENSITIVE_VALUE_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b"),
    "resident_registration_number": re.compile(r"\b\d{6}-[1-4]\d{6}\b"),
    "account_number": re.compile(r"\b\d{10,16}\b"),
}
PROFILE_REQUIREMENTS: dict[str, dict[str, bool]] = {
    "poc": {
        "require_llm": True,
        "check_llm": True,
    },
    "pilot": {
        "require_llm": True,
        "check_llm": True,
        "require_api_token": True,
        "expect_read_only": True,
        "require_sql_parser": True,
        "require_embedding": True,
        "check_embedding": True,
        "require_trusted_auth": True,
        "require_trusted_proxy_token": True,
        "require_feedback_store": True,
    },
}
PREFLIGHT_NEXT_ACTIONS: dict[str, str] = {
    "api_token": "Set IM_ONE_API_TOKEN before exposing protected query, export, and monitoring endpoints.",
    "audit_append_only": "Restore query_audit_log UPDATE/DELETE abort triggers before enabling database audit persistence.",
    "database_access": "Verify the demo/read-replica database path is reachable and has the required schema.",
    "database_backend": "Set IM_ONE_DB_BACKEND to a supported execution backend, or add a backend adapter before changing databases.",
    "demo_query_latency": "Review indexes, query shape, or dataset location until core demo SQL runs under 1000 ms.",
    "embedding_configuration": "Set an approved OPENAI_API_KEY and IM_ONE_EMBEDDING_MODEL, or configure a localhost embedding runtime with IM_ONE_EMBEDDING_ALLOW_LOCAL_NO_AUTH=1.",
    "embedding_generation": "Verify the approved embedding endpoint returns a usable vector and is active in schema-retrieval scoring.",
    "evaluation_readiness": "Prepare at least 30 evaluation cases with blocked and follow-up coverage plus a 100+ verified question bank.",
    "feedback_store": "Set IM_ONE_FEEDBACK_PATH to a writable append-only backlog location.",
    "gold_coverage": "Add executable gold SQL with expected result-shape coverage for every non-blocked evaluation case.",
    "health_disclosure_policy": "Keep unauthenticated health payloads minimal and expose runtime model/base-url/database path details only to authorized health checks.",
    "langgraph_runtime": "Install LangGraph and verify build_agent() returns a compiled StateGraph runtime.",
    "llm_configuration": "Set an approved OPENAI_API_KEY and IM_ONE_LLM_MODEL, or configure a localhost LLM runtime with IM_ONE_LLM_ALLOW_LOCAL_NO_AUTH=1.",
    "llm_generation": "Verify the approved LLM gateway returns structured SQL within the 10-second PRD latency target and passes validation.",
    "llm_prompt_policy": "Restore the LLM prompt payload contract so selected schema, semantic metrics, role policy, dataset metadata, and SQL rules are sent before generation.",
    "llm_timeout": "Set IM_ONE_LLM_TIMEOUT to a positive value at or below 10 seconds.",
    "query_plan_policy": "Restore pre-execution query plan and row-count capture so validated SQL exposes execution-readiness evidence before execution.",
    "query_timeout": "Set IM_ONE_QUERY_TIMEOUT_MS to a positive value at or below the demo response budget.",
    "prd_traceability": "Restore PRD traceability so every functional requirement has implementation, verification, and readiness-gate evidence.",
    "prd_wording_policy": "Remove internal implementation caveats from the PRD and keep the document focused on product requirements.",
    "read_only_mode": "Run the service against a read-only database connection or read replica before pilot use.",
    "result_explanation_policy": "Restore result explanations so they include question interpretation, metric definitions, period, grouping, filters, tables, validation evidence, row count, assumptions, and synthetic-data caution.",
    "role_policy": "Remove operational tables and unknown tables from role policies before deployment.",
    "schema_retrieval_policy": "Restore schema retrieval so representative questions select only relevant role-allowed tables and expose ambiguity handling.",
    "sql_validation_policy": "Restore SQL safety guardrails for read-only, no SELECT *, row-limit, operational-table, aggregate-first, and branch-scope enforcement.",
    "sql_parser": "Install sqlglot in the deployment environment before requiring strict parser readiness.",
    "static_ui_assets": "Remove external UI dependencies, restore the local icon bundle, and keep static app CSP same-origin only.",
    "synthetic_dataset_metadata": "Regenerate the demo mart so demo_dataset_metadata marks the dataset as fixed-seed synthetic POC data with no real customer/account/employee/branch-performance content.",
    "synthetic_data_policy": "Remove sensitive columns or sensitive-looking values from the synthetic mart.",
    "trace_audit_policy": "Restore execution trace and audit logging so every workflow exposes required nodes and PRD audit fields.",
    "trusted_header_auth": "Enable trusted-header mode behind an API gateway and set IM_ONE_TRUSTED_PROXY_TOKEN.",
    "web_api_auth_policy": "Keep monitoring, catalog, verified-question, query, export, and feedback endpoints behind the shared authorization gate.",
}


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    passed: bool
    required: bool
    detail: str


@dataclass(frozen=True)
class PrdTraceabilityItem:
    requirement_id: str
    title: str
    implementation_artifacts: tuple[str, ...]
    verification_artifacts: tuple[str, ...]
    preflight_checks: tuple[str, ...] = ()


PRD_FUNCTIONAL_REQUIREMENT_IDS = tuple(f"FR-{index:03d}" for index in range(1, 13))
PRD_TRACEABILITY_MATRIX = (
    PrdTraceabilityItem(
        "FR-001",
        "자연어 질문 입력",
        ("apps/web/src/components/AssistantComposer.tsx", "apps/api/src/imax_api/routers/assistant.py"),
        ("apps/web/tests/app.test.tsx", "apps/api/tests/test_unified_api.py"),
        ("static_ui_assets",),
    ),
    PrdTraceabilityItem(
        "FR-002",
        "Semantic Layer",
        ("apps/api/src/im_one_agent/domain.py", "apps/api/src/im_one_agent/schema_retrieval.py", "apps/api/src/im_one_agent/response.py"),
        ("apps/api/tests/test_schema_and_generation.py", "apps/api/tests/test_prd_alignment.py"),
        ("schema_retrieval_policy", "result_explanation_policy"),
    ),
    PrdTraceabilityItem(
        "FR-003",
        "Schema Retrieval",
        ("apps/api/src/im_one_agent/schema_retrieval.py", "apps/api/src/im_one_agent/sql_generator.py"),
        ("apps/api/tests/test_schema_and_generation.py", "apps/api/tests/test_prd_alignment.py"),
        ("schema_retrieval_policy",),
    ),
    PrdTraceabilityItem(
        "FR-004",
        "LLM SQL Generation",
        ("apps/api/src/im_one_agent/sql_generator.py", "apps/api/src/im_one_agent/graph.py"),
        ("apps/api/tests/test_schema_and_generation.py", "apps/api/tests/test_prd_alignment.py"),
        ("llm_prompt_policy", "llm_configuration", "llm_generation"),
    ),
    PrdTraceabilityItem(
        "FR-005",
        "SQL Validation Layer",
        ("apps/api/src/im_one_agent/sql_safety.py",),
        ("apps/api/tests/test_sql_safety.py", "apps/api/tests/test_prd_alignment.py"),
        ("sql_validation_policy",),
    ),
    PrdTraceabilityItem(
        "FR-006",
        "Query Execution",
        ("apps/api/src/im_one_agent/graph.py", "apps/api/src/im_one_agent/database_backend.py", "apps/api/src/im_one_agent/sample_data.py"),
        ("apps/api/tests/test_prd_alignment.py", "apps/api/tests/test_web_exports.py"),
        ("database_backend", "query_timeout", "demo_query_latency", "query_plan_policy"),
    ),
    PrdTraceabilityItem(
        "FR-007",
        "Result Explanation",
        ("apps/api/src/im_one_agent/response.py", "apps/web/src/pages/DataPage.tsx"),
        ("apps/api/tests/test_prd_alignment.py", "apps/web/tests/charting.test.ts"),
        ("result_explanation_policy",),
    ),
    PrdTraceabilityItem(
        "FR-008",
        "Execution Trace와 Audit Log",
        ("apps/api/src/im_one_agent/graph.py", "apps/api/src/im_one_agent/web.py", "apps/api/src/im_one_agent/sample_data.py"),
        ("apps/api/tests/test_prd_alignment.py", "apps/api/tests/test_web_exports.py"),
        ("trace_audit_policy", "audit_append_only"),
    ),
    PrdTraceabilityItem(
        "FR-009",
        "Role-Based Access Control",
        ("apps/api/src/im_one_agent/domain.py", "apps/api/src/im_one_agent/schema_retrieval.py", "apps/api/src/im_one_agent/sql_safety.py"),
        ("apps/api/tests/test_schema_and_generation.py", "apps/api/tests/test_sql_safety.py", "apps/api/tests/test_prd_alignment.py"),
        ("role_policy", "schema_retrieval_policy", "sql_validation_policy"),
    ),
    PrdTraceabilityItem(
        "FR-010",
        "Data Catalog Panel",
        ("apps/api/src/imax_api/routers/data.py", "apps/web/src/pages/DataPage.tsx"),
        ("apps/api/tests/test_unified_api.py", "apps/web/tests/app.test.tsx"),
        ("static_ui_assets", "web_api_auth_policy"),
    ),
    PrdTraceabilityItem(
        "FR-011",
        "Responsive Workbench UI",
        ("apps/web/src/styles.css", "apps/web/src/components/VirtualTable.tsx"),
        ("apps/web/tests/app.test.tsx", "apps/web/tests/charting.test.ts"),
        ("static_ui_assets",),
    ),
    PrdTraceabilityItem(
        "FR-012",
        "Evaluation Harness",
        ("apps/api/src/im_one_agent/evaluation.py", "apps/api/src/im_one_agent/evaluate.py"),
        ("apps/api/tests/test_prd_alignment.py",),
        ("evaluation_readiness", "gold_coverage"),
    ),
    PrdTraceabilityItem(
        "NFR-SEC",
        "보안, 개인정보, 권한, 운영 통제",
        ("apps/api/src/im_one_agent/intent_guard.py", "apps/api/src/imax_api/auth.py", "apps/api/src/im_one_agent/sql_safety.py"),
        ("apps/api/tests/test_intent_guard.py", "apps/api/tests/test_unified_api.py", "apps/api/tests/test_sql_safety.py"),
        ("web_api_auth_policy", "health_disclosure_policy", "synthetic_data_policy"),
    ),
    PrdTraceabilityItem(
        "NFR-DATA",
        "합성 데이터셋과 데이터 윤리",
        ("apps/api/src/im_one_agent/generate_demo_data.py", "apps/api/src/im_one_agent/sample_data.py"),
        ("apps/api/tests/test_demo_data_generator.py", "apps/api/tests/test_prd_alignment.py"),
        ("synthetic_dataset_metadata", "synthetic_data_policy"),
    ),
    PrdTraceabilityItem(
        "NFR-PERF",
        "성능과 타임아웃",
        ("apps/api/src/im_one_agent/graph.py", "apps/api/src/im_one_agent/preflight.py"),
        ("apps/api/tests/test_prd_alignment.py",),
        ("query_timeout", "llm_timeout", "demo_query_latency", "query_plan_policy"),
    ),
    PrdTraceabilityItem(
        "NFR-OBS",
        "설명 가능성과 관찰 가능성",
        ("apps/api/src/im_one_agent/web.py", "apps/api/src/im_one_agent/graph.py", "apps/api/src/im_one_agent/evaluation.py"),
        ("apps/api/tests/test_web_exports.py", "apps/api/tests/test_prd_alignment.py"),
        ("trace_audit_policy", "result_explanation_policy", "query_plan_policy"),
    ),
)


def preflight_requirements_for_profile(profile: str | None) -> dict[str, bool]:
    if not profile:
        return {}
    return dict(PROFILE_REQUIREMENTS.get(profile, {}))


def run_preflight(
    db_path: str = "data/im_one_demo.sqlite",
    require_llm: bool = False,
    check_llm: bool = False,
    require_api_token: bool = False,
    expect_read_only: bool = False,
    require_sql_parser: bool = True,
    require_embedding: bool = False,
    check_embedding: bool = False,
    require_trusted_auth: bool = False,
    require_trusted_proxy_token: bool = False,
    require_feedback_store: bool = False,
) -> list[PreflightCheck]:
    checks: list[PreflightCheck] = []
    checks.append(check_langgraph_runtime())
    checks.append(check_query_timeout())
    checks.append(check_llm_timeout())
    checks.append(check_llm_prompt_policy())
    checks.append(check_sql_parser(required=require_sql_parser))
    checks.append(check_sql_validation_policy(db_path))
    checks.append(check_static_ui_assets())
    checks.append(check_web_api_auth_policy())
    checks.append(check_health_disclosure_policy(db_path))
    checks.append(check_embedding_configuration(required=require_embedding))
    if check_embedding:
        checks.append(check_embedding_generation())
    checks.append(check_database_backend_policy())
    checks.append(check_database_access(db_path))
    checks.append(check_demo_query_latency(db_path))
    checks.append(check_query_plan_policy(db_path))
    checks.append(check_schema_retrieval_policy())
    checks.append(check_result_explanation_policy())
    checks.append(check_trace_audit_policy(db_path))
    checks.append(check_role_policy(db_path))
    checks.append(check_audit_append_only(db_path))
    checks.append(check_synthetic_data_policy(db_path))
    checks.append(check_synthetic_dataset_metadata(db_path))
    checks.append(check_read_only_mode(db_path, required=expect_read_only))
    checks.append(check_llm_configuration(required=require_llm))
    if check_llm:
        checks.append(check_llm_generation(db_path))
    checks.append(check_api_token(required=require_api_token))
    checks.append(check_trusted_header_auth(required=require_trusted_auth, require_proxy_token=require_trusted_proxy_token))
    checks.append(check_feedback_store(required=require_feedback_store))
    checks.append(check_evaluation_readiness())
    checks.append(check_gold_coverage(db_path))
    checks.append(check_prd_wording_policy())
    checks.append(check_prd_traceability({check.name for check in checks} | set(PREFLIGHT_NEXT_ACTIONS)))
    return checks


def check_prd_wording_policy() -> PreflightCheck:
    if not PRD_DOC_PATH.exists():
        return PreflightCheck("prd_wording_policy", False, True, f"missing PRD document: {repo_relative_label(PRD_DOC_PATH)}")

    prd_text = PRD_DOC_PATH.read_text(encoding="utf-8")
    matches = [wording for wording in PRD_FORBIDDEN_INTERNAL_WORDING if wording in prd_text]
    if matches:
        return PreflightCheck(
            "prd_wording_policy",
            False,
            True,
            "forbidden internal wording: " + ", ".join(matches),
        )
    return PreflightCheck(
        "prd_wording_policy",
        True,
        True,
        f"forbidden_terms=0, checked={repo_relative_label(PRD_DOC_PATH)}",
    )


def check_prd_traceability(available_preflight_checks: set[str]) -> PreflightCheck:
    errors: list[str] = []
    requirement_ids = [item.requirement_id for item in PRD_TRACEABILITY_MATRIX]
    functional_ids = [requirement_id for requirement_id in requirement_ids if requirement_id.startswith("FR-")]

    if not PRD_DOC_PATH.exists():
        errors.append(f"missing PRD document: {repo_relative_label(PRD_DOC_PATH)}")
        prd_text = ""
    else:
        prd_text = PRD_DOC_PATH.read_text(encoding="utf-8")

    missing_functional = sorted(set(PRD_FUNCTIONAL_REQUIREMENT_IDS) - set(functional_ids))
    extra_functional = sorted(set(functional_ids) - set(PRD_FUNCTIONAL_REQUIREMENT_IDS))
    duplicate_ids = sorted({requirement_id for requirement_id in requirement_ids if requirement_ids.count(requirement_id) > 1})
    if missing_functional:
        errors.append("missing functional requirements: " + ", ".join(missing_functional))
    if extra_functional:
        errors.append("unknown functional requirements: " + ", ".join(extra_functional))
    if duplicate_ids:
        errors.append("duplicate requirement IDs: " + ", ".join(duplicate_ids))

    missing_prd_headings = [
        requirement_id
        for requirement_id in PRD_FUNCTIONAL_REQUIREMENT_IDS
        if prd_text and f"### {requirement_id}" not in prd_text
    ]
    if missing_prd_headings:
        errors.append("PRD document missing headings: " + ", ".join(missing_prd_headings))

    for item in PRD_TRACEABILITY_MATRIX:
        if not item.title.strip():
            errors.append(f"{item.requirement_id}: missing title")
        if not item.implementation_artifacts:
            errors.append(f"{item.requirement_id}: missing implementation artifacts")
        if not item.verification_artifacts:
            errors.append(f"{item.requirement_id}: missing verification artifacts")
        missing_checks = sorted(set(item.preflight_checks) - available_preflight_checks)
        if missing_checks:
            errors.append(f"{item.requirement_id}: unknown preflight checks: {', '.join(missing_checks)}")
        for artifact in item.implementation_artifacts:
            if not (REPO_ROOT / artifact).exists():
                errors.append(f"{item.requirement_id}: implementation artifact missing: {artifact}")
        for artifact in item.verification_artifacts:
            if not (REPO_ROOT / artifact).exists():
                errors.append(f"{item.requirement_id}: verification artifact missing: {artifact}")

    if errors:
        return PreflightCheck("prd_traceability", False, True, "; ".join(errors))

    functional_count = len(functional_ids)
    nonfunctional_count = len(requirement_ids) - functional_count
    return PreflightCheck(
        "prd_traceability",
        True,
        True,
        f"functional={functional_count}, nonfunctional={nonfunctional_count}, evidence_items={len(PRD_TRACEABILITY_MATRIX)}",
    )


def repo_relative_label(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def check_database_backend_policy() -> PreflightCheck:
    backend_name = configured_database_backend_name()
    supported = available_database_backend_names()
    try:
        backend = execution_backend_for_name(backend_name)
    except ValueError as exc:
        return PreflightCheck("database_backend", False, True, str(exc))
    return PreflightCheck(
        "database_backend",
        backend.name in supported,
        True,
        f"backend={backend.name}, supported={','.join(supported)}",
    )


def check_database_access(db_path: str) -> PreflightCheck:
    try:
        ensure_demo_database(db_path)
        connection = connect_database(db_path)
        try:
            branch_count = connection.execute("SELECT COUNT(*) FROM branches").fetchone()[0]
        finally:
            connection.close()
    except Exception as exc:
        return PreflightCheck("database_access", False, True, str(exc))

    return PreflightCheck("database_access", branch_count > 0, True, f"branches={branch_count}")


def check_demo_query_latency(db_path: str) -> PreflightCheck:
    core_cases = [case for case in EVALUATION_CASES if case.case_id.startswith("core-")]
    timings: list[float] = []
    row_counts: list[int] = []
    try:
        ensure_demo_database(db_path)
        connection = connect_database(db_path)
        try:
            for case in core_cases:
                sql = gold_sql_for_case(case, role="sales_planning", branch_id=1)
                if not sql:
                    return PreflightCheck("demo_query_latency", False, True, f"missing gold SQL for {case.case_id}")
                started_at = perf_counter()
                rows = connection.execute(sql).fetchall()
                elapsed_ms = (perf_counter() - started_at) * 1000
                timings.append(elapsed_ms)
                row_counts.append(len(rows))
        finally:
            connection.close()
    except Exception as exc:
        return PreflightCheck("demo_query_latency", False, True, str(exc))

    if not timings:
        return PreflightCheck("demo_query_latency", False, True, "no core demo queries found.")

    max_ms = max(timings)
    total_rows = sum(row_counts)
    detail = f"queries={len(timings)}, max_ms={max_ms:.2f}, rows={total_rows}"
    if max_ms > 1000:
        return PreflightCheck("demo_query_latency", False, True, detail + " exceeds 1000ms target.")
    return PreflightCheck("demo_query_latency", True, True, detail)


def check_query_plan_policy(db_path: str) -> PreflightCheck:
    question = "영업점별 ELS 가입 금액과 민원 건수를 비교해줘."
    sql = """
WITH els_sales AS (
    SELECT branch_id, SUM(amount) AS els_amount, COUNT(*) AS els_count
    FROM product_sales
    WHERE product_type = 'ELS'
    GROUP BY branch_id
),
voc_summary AS (
    SELECT branch_id, COUNT(*) AS voc_count
    FROM voc_cases
    GROUP BY branch_id
)
SELECT
    b.branch_name,
    COALESCE(e.els_amount, 0) AS els_amount,
    COALESCE(e.els_count, 0) AS els_count,
    COALESCE(v.voc_count, 0) AS voc_count
FROM branches b
LEFT JOIN els_sales e ON b.branch_id = e.branch_id
LEFT JOIN voc_summary v ON b.branch_id = v.branch_id
ORDER BY els_amount DESC
LIMIT 20
""".strip()
    try:
        ensure_demo_database(db_path)
        context = retrieve_schema(question, user_role="sales_planning")
        connection = connect_database(db_path)
        try:
            validation = validate_sql(sql, allowed_tables=context.allowed_table_names, connection=connection)
        finally:
            connection.close()

        if not validation.allowed:
            return PreflightCheck("query_plan_policy", False, True, "; ".join(validation.issues))

        result = execute_sql_node({"db_path": db_path, "validation": validation})
    except Exception as exc:
        return PreflightCheck("query_plan_policy", False, True, str(exc))

    query_plan = result.get("query_plan_summary", [])
    pre_execution_row_count = result.get("pre_execution_row_count")
    pre_execution_row_count_status = result.get("pre_execution_row_count_status")
    errors: list[str] = []
    if not isinstance(query_plan, list) or not query_plan:
        errors.append("query_plan_summary missing")
    elif any("unavailable" in str(step) for step in query_plan):
        errors.append("query_plan_summary unavailable")
    if result.get("execution_ms") is None:
        errors.append("execution_ms missing")
    if not isinstance(pre_execution_row_count, int):
        errors.append("pre_execution_row_count missing")
    if pre_execution_row_count_status != "checked":
        errors.append(f"pre_execution_row_count_status={pre_execution_row_count_status}")
    if not result.get("column_metadata"):
        errors.append("column_metadata missing")
    if "validation" in result and not result["validation"].allowed:
        errors.append("; ".join(result["validation"].issues))

    if errors:
        return PreflightCheck("query_plan_policy", False, True, "; ".join(errors))
    return PreflightCheck(
        "query_plan_policy",
        True,
        True,
        (
            f"plan_steps={len(query_plan)}, pre_execution_rows={pre_execution_row_count}, "
            + f"columns={len(result.get('columns', []))}, execution_ms={result.get('execution_ms')}"
        ),
    )


def run_sql_validation_policy_probes(db_path: str = "data/im_one_demo.sqlite") -> dict[str, object]:
    allowed_tables = set(SYNTHETIC_BUSINESS_TABLES) | OPERATIONAL_ONLY_TABLES
    probe_results: list[dict[str, object]] = []
    try:
        ensure_demo_database(db_path)
        connection = connect_database(db_path)
    except Exception as exc:
        return {
            "passed": False,
            "probe_total": len(SQL_VALIDATION_POLICY_PROBES),
            "failed_total": len(SQL_VALIDATION_POLICY_PROBES),
            "failures": [f"database unavailable for SQL policy probes: {exc}"],
            "probes": probe_results,
        }

    failures: list[str] = []
    try:
        for probe_name, sql, expected_allowed, branch_scope_branch_id in SQL_VALIDATION_POLICY_PROBES:
            result = validate_sql(
                sql,
                allowed_tables=allowed_tables,
                connection=connection,
                branch_scope_branch_id=branch_scope_branch_id,
            )
            passed = result.allowed == expected_allowed
            if result.allowed != expected_allowed:
                status = "allowed" if result.allowed else "blocked"
                expected = "allowed" if expected_allowed else "blocked"
                failures.append(f"{probe_name}: expected {expected}, got {status}")
            probe_results.append(
                {
                    "name": probe_name,
                    "sql": sql,
                    "expected_allowed": expected_allowed,
                    "actual_allowed": result.allowed,
                    "passed": passed,
                    "issues": list(result.issues),
                    "referenced_tables": list(result.referenced_tables),
                    "branch_scope_branch_id": branch_scope_branch_id,
                }
            )
    finally:
        connection.close()

    return {
        "passed": not failures,
        "probe_total": len(SQL_VALIDATION_POLICY_PROBES),
        "failed_total": len(failures),
        "failures": failures,
        "probes": probe_results,
    }


def check_sql_validation_policy(db_path: str = "data/im_one_demo.sqlite") -> PreflightCheck:
    payload = run_sql_validation_policy_probes(db_path)
    failures = payload["failures"]
    if failures:
        return PreflightCheck("sql_validation_policy", False, True, "; ".join(str(failure) for failure in failures))
    return PreflightCheck("sql_validation_policy", True, True, f"probes={payload['probe_total']}")


def schema_retrieval_probe_payload(
    probe_name: str,
    question: str,
    role: str,
    context: object,
    issues: list[str],
    required_tables: set[str] | None = None,
    forbidden_tables: set[str] | None = None,
    minimum_confidence: str | None = None,
) -> dict[str, object]:
    required_tables = required_tables or set()
    forbidden_tables = forbidden_tables or set()
    return {
        "name": probe_name,
        "question": question,
        "role": role,
        "passed": not issues,
        "issues": issues,
        "required_tables": sorted(required_tables),
        "forbidden_tables": sorted(forbidden_tables),
        "minimum_confidence": minimum_confidence,
        "selected_tables": sorted(context.allowed_table_names),
        "matched_metrics": [metric.name for metric in context.matched_metrics],
        "retrieval_confidence": context.retrieval_confidence,
        "clarification_options": list(context.clarification_options),
        "retrieval_scores": [asdict(score) for score in context.retrieval_scores],
    }


def run_schema_retrieval_policy_probes() -> dict[str, object]:
    errors: list[str] = []
    probe_results: list[dict[str, object]] = []

    for probe in SCHEMA_RETRIEVAL_POLICY_PROBES:
        probe_name = str(probe["name"])
        question = str(probe["question"])
        role = str(probe["role"])
        required_tables = set(probe["required_tables"])
        forbidden_tables = set(probe["forbidden_tables"])
        minimum_confidence = str(probe["minimum_confidence"])
        context = retrieve_schema(question, user_role=role)
        probe_issues: list[str] = []
        table_names = set(context.allowed_table_names)
        missing = sorted(required_tables - table_names)
        forbidden = sorted(forbidden_tables & table_names)
        role_allowed_tables = ROLE_TABLE_POLICY[role]
        outside_role = sorted(table_names - role_allowed_tables)
        if missing:
            probe_issues.append(f"{probe_name}: missing tables: {', '.join(missing)}")
        if forbidden:
            probe_issues.append(f"{probe_name}: unrelated tables selected: {', '.join(forbidden)}")
        if outside_role:
            probe_issues.append(f"{probe_name}: tables outside role policy: {', '.join(outside_role)}")
        if minimum_confidence == "high" and context.retrieval_confidence != "high":
            probe_issues.append(f"{probe_name}: expected high confidence, got {context.retrieval_confidence}")
        if not context.retrieval_scores:
            probe_issues.append(f"{probe_name}: retrieval scores missing")
        if not any(score.embedding_source in {"local", "remote"} for score in context.retrieval_scores):
            probe_issues.append(f"{probe_name}: embedding source missing")
        errors.extend(probe_issues)
        probe_results.append(
            schema_retrieval_probe_payload(
                probe_name,
                question,
                role,
                context,
                probe_issues,
                required_tables=required_tables,
                forbidden_tables=forbidden_tables,
                minimum_confidence=minimum_confidence,
            )
        )

    compliance_question = "신규 계좌 목표 대비 실적을 비교해줘."
    compliance_context = retrieve_schema(compliance_question, user_role="compliance")
    compliance_issues: list[str] = []
    disallowed_for_compliance = {"accounts", "branch_targets"} & set(compliance_context.allowed_table_names)
    if disallowed_for_compliance:
        compliance_issues.append("compliance_role: disallowed tables selected: " + ", ".join(sorted(disallowed_for_compliance)))
    compliance_allowed_tables = ROLE_TABLE_POLICY["compliance"]
    if any(not set(metric.tables).issubset(compliance_allowed_tables) for metric in compliance_context.matched_metrics):
        compliance_issues.append("compliance_role: matched metrics include tables outside role policy")
    errors.extend(compliance_issues)
    probe_results.append(
        schema_retrieval_probe_payload(
            "compliance_role",
            compliance_question,
            "compliance",
            compliance_context,
            compliance_issues,
            forbidden_tables={"accounts", "branch_targets"},
        )
    )

    ambiguous_question = "가입 현황 알려줘."
    ambiguous_context = retrieve_schema(ambiguous_question, user_role="sales_planning")
    ambiguous_issues: list[str] = []
    if ambiguous_context.retrieval_confidence != "low":
        ambiguous_issues.append(f"ambiguous_question: expected low confidence, got {ambiguous_context.retrieval_confidence}")
    if not ambiguous_context.clarification_options:
        ambiguous_issues.append("ambiguous_question: clarification options missing")
    errors.extend(ambiguous_issues)
    probe_results.append(
        schema_retrieval_probe_payload(
            "ambiguous_question",
            ambiguous_question,
            "sales_planning",
            ambiguous_context,
            ambiguous_issues,
            minimum_confidence="low",
        )
    )

    follow_up_question = "그중 VOC가 많은 곳만 남겨줘."
    follow_up_context = retrieve_schema(follow_up_question, user_role="sales_planning")
    extended_context = extend_schema_with_follow_up_context(
        follow_up_question,
        follow_up_context,
        {
            "previous_validation_allowed": True,
            "previous_tables": ["accounts", "branches"],
            "previous_metrics": ["new_accounts"],
        },
        user_role="sales_planning",
    )
    follow_up_issues: list[str] = []
    if not {"accounts", "branches", "voc_cases"}.issubset(extended_context.allowed_table_names):
        follow_up_issues.append("follow_up_context: previous and current schema context not merged")
    if not any("후속 질문" in rule for rule in extended_context.business_rules):
        follow_up_issues.append("follow_up_context: business rule explaining follow-up merge missing")
    errors.extend(follow_up_issues)
    follow_up_payload = schema_retrieval_probe_payload(
        "follow_up_context",
        follow_up_question,
        "sales_planning",
        extended_context,
        follow_up_issues,
        required_tables={"accounts", "branches", "voc_cases"},
    )
    follow_up_payload["previous_tables"] = ["accounts", "branches"]
    follow_up_payload["previous_metrics"] = ["new_accounts"]
    probe_results.append(follow_up_payload)

    return {
        "passed": not errors,
        "probe_total": len(probe_results),
        "failed_total": sum(1 for probe in probe_results if not probe["passed"]),
        "failures": errors,
        "probes": probe_results,
    }


def check_schema_retrieval_policy() -> PreflightCheck:
    payload = run_schema_retrieval_policy_probes()
    errors = payload["failures"]
    if errors:
        return PreflightCheck("schema_retrieval_policy", False, True, "; ".join(str(error) for error in errors))
    return PreflightCheck("schema_retrieval_policy", True, True, f"probes={payload['probe_total']}")


def check_result_explanation_policy() -> PreflightCheck:
    question = "최근 30일 VOC 유형별 처리 현황 알려줘."
    context = retrieve_schema(question, user_role="sales_planning")
    validation = validate_sql(
        """
SELECT
    v.case_type,
    v.status,
    COUNT(*) AS case_count
FROM voc_cases v
GROUP BY v.case_type, v.status
LIMIT 30
""".strip(),
        allowed_tables=context.allowed_table_names,
    )
    explanation = build_explanation(
        question=question,
        context=context,
        validation=validation,
        row_count=3,
        generation_reason="VOC 유형과 처리 상태를 집계합니다.",
        generation_assumptions=("최근 30일 기준으로 해석",),
    )
    blocked_explanation = build_explanation(
        question="전체 고객 원장과 계좌번호를 보여줘.",
        context=context,
        validation=validate_sql("SELECT * FROM accounts LIMIT 10", allowed_tables=context.allowed_table_names),
        row_count=0,
        generation_reason="위험 요청 검증 단계에서 차단되었습니다.",
    )
    required_snippets = (
        f"질문: {question}",
        "해석한 업무 지표: voc_status",
        "해석 신뢰도:",
        "확인 질문 제안:",
        "지표 정의: voc_status: COUNT(voc_cases.case_id)",
        "기간 기준: voc_status: voc_cases.received_at / 최근 30일",
        "집계 기준: voc_status: VOC 유형, 처리 상태",
        "필터 기준:",
        "참조 가능한 스키마:",
        "실제 SQL 참조 테이블: voc_cases",
        "생성 기준: VOC 유형과 처리 상태를 집계합니다.",
        "생성 가정: 최근 30일 기준으로 해석",
        "검증 결과: 통과",
        "검증 근거:",
        "읽기 전용 SELECT/WITH",
        "허용 테이블 whitelist",
        "조회 행 수: 3",
        "합성 데이터 기반 POC",
    )
    blocked_required_snippets = (
        "검증 결과: 차단",
        "SQL Validation Layer에서 실행 전 차단되었습니다.",
        "차단 사유:",
        "SELECT *",
        "조회 행 수: 0",
    )
    missing = [snippet for snippet in required_snippets if snippet not in explanation]
    missing.extend(
        f"blocked:{snippet}"
        for snippet in blocked_required_snippets
        if snippet not in blocked_explanation
    )
    if missing:
        return PreflightCheck("result_explanation_policy", False, True, "missing snippets: " + ", ".join(missing[:8]))
    return PreflightCheck("result_explanation_policy", True, True, "allowed_and_blocked_explanations=covered")


def check_trace_audit_policy(db_path: str) -> PreflightCheck:
    from im_one_agent.web import build_audit_summary, build_execution_trace

    question = "branches 테이블 삭제해줘."
    audit_path = Path(os.getenv("TMPDIR", "/private/tmp")) / f"im_one_preflight_audit_{os.getpid()}.jsonl"
    if audit_path.exists():
        audit_path.unlink()

    try:
        ensure_demo_database(db_path)
        agent = build_agent()
        result = agent.invoke(
            {
                "question": question,
                "user_id": "preflight",
                "auth_mode": "preflight",
                "user_role": "branch_manager",
                "branch_id": 1,
                "db_path": db_path,
                "audit_path": str(audit_path),
            }
        )
        trace = build_execution_trace(result, agent.__class__.__name__)
        audit_summary = build_audit_summary(audit_path, db_path=db_path)
        errors = trace_audit_policy_errors(result, trace, audit_path, db_path, question, audit_summary)
    except Exception as exc:
        return PreflightCheck("trace_audit_policy", False, True, str(exc))
    finally:
        try:
            audit_path.unlink()
        except FileNotFoundError:
            pass

    if errors:
        return PreflightCheck("trace_audit_policy", False, True, "; ".join(errors))

    database_audit_status = str(result.get("database_audit_status", ""))
    return PreflightCheck(
        "trace_audit_policy",
        True,
        True,
        f"trace_nodes={len(REQUIRED_TRACE_NODES)}, audit_fields={len(REQUIRED_AUDIT_EVENT_FIELDS)}, database_audit={database_audit_status}",
    )


def trace_audit_policy_errors(
    result: dict[str, object],
    trace: list[dict[str, object]],
    audit_path: Path,
    db_path: str,
    question: str,
    audit_summary: dict[str, object] | None = None,
) -> list[str]:
    errors: list[str] = []
    nodes = [str(item.get("node", "")) for item in trace]
    if nodes != list(REQUIRED_TRACE_NODES):
        errors.append("trace nodes mismatch: " + ",".join(nodes))

    trace_by_node = {str(item.get("node", "")): item for item in trace}
    sql_generation = trace_by_node.get("SQL Generation", {})
    generation_metadata = sql_generation.get("metadata", {})
    if not isinstance(generation_metadata, dict):
        generation_metadata = {}
    if sql_generation.get("status") != "blocked":
        errors.append("SQL Generation trace should be blocked for dangerous intent")
    for key in ("engine", "promptVersion", "reason"):
        if not generation_metadata.get(key):
            errors.append(f"SQL Generation metadata missing {key}")
    if "model" not in generation_metadata:
        errors.append("SQL Generation metadata missing model")

    sql_validation = trace_by_node.get("SQL Validation", {})
    if sql_validation.get("status") != "blocked":
        errors.append("SQL Validation trace should be blocked")
    query_execution = trace_by_node.get("Query Execution", {})
    execution_metadata = query_execution.get("metadata", {})
    if not isinstance(execution_metadata, dict):
        execution_metadata = {}
    if query_execution.get("status") != "skipped":
        errors.append("Query Execution trace should be skipped")
    if execution_metadata.get("rowCount") != 0:
        errors.append("Query Execution rowCount should be 0")

    audit_log = trace_by_node.get("Audit Log", {})
    database_audit_status = str(result.get("database_audit_status", ""))
    expected_audit_trace_status = "recorded" if database_audit_status != "failed" else "partial"
    if audit_log.get("status") != expected_audit_trace_status:
        errors.append("Audit Log trace status mismatch")

    if not audit_path.exists():
        errors.append("JSONL audit file missing")
        return errors

    try:
        event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        errors.append(f"JSONL audit event unreadable: {exc}")
        return errors

    for field in REQUIRED_AUDIT_EVENT_FIELDS:
        if field not in event:
            errors.append(f"JSONL audit missing {field}")

    expected_values = {
        "auth_mode": "preflight",
        "user_id": "preflight",
        "user_role": "branch_manager",
        "original_question": question,
        "generation_engine": "intent_guard",
        "validation_status": "blocked",
        "execution_status": "blocked",
        "row_count": 0,
    }
    for field, expected in expected_values.items():
        if event.get(field) != expected:
            errors.append(f"JSONL audit {field}={event.get(field)!r}, expected {expected!r}")
    if not event.get("blocked_reason"):
        errors.append("JSONL audit blocked_reason missing")
    if not isinstance(event.get("selected_semantic_metrics"), list):
        errors.append("JSONL audit selected_semantic_metrics should be a list")

    if is_read_only_database():
        if database_audit_status != "skipped_read_only":
            errors.append(f"read-only database audit status should be skipped_read_only, got {database_audit_status}")
        return errors

    if database_audit_status != "recorded":
        errors.append(f"database audit should be recorded, got {database_audit_status}")
        return errors

    audit_summary = audit_summary or {}
    if audit_summary.get("source") != "query_audit_log":
        errors.append(f"audit summary should use query_audit_log source, got {audit_summary.get('source')}")
    if int(audit_summary.get("total", 0) or 0) < 1:
        errors.append("audit summary should include database audit events")

    connection = connect_database(db_path)
    try:
        row = connection.execute(
            """
            SELECT
                user_id,
                auth_mode,
                user_role,
                original_question,
                selected_semantic_metrics,
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
                row_count,
                blocked_reason
            FROM query_audit_log
            ORDER BY audit_id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        errors.append("query_audit_log latest row missing")
        return errors

    for field in (
        "user_id",
        "auth_mode",
        "user_role",
        "original_question",
        "selected_semantic_metrics",
        "generated_sql",
        "llm_generated_sql",
        "policy_applied_sql",
        "validated_sql",
        "sql_policy_transformations",
        "generation_engine",
        "llm_model",
        "prompt_version",
        "validation_status",
        "execution_status",
        "row_count",
        "blocked_reason",
    ):
        value = row[field]
        if value in (None, "") and field not in {
            "generated_sql",
            "llm_generated_sql",
            "policy_applied_sql",
            "validated_sql",
            "llm_model",
            "sql_policy_transformations",
        }:
            errors.append(f"query_audit_log {field} missing")

    if row["original_question"] != question:
        errors.append("query_audit_log original_question mismatch")
    if row["generation_engine"] != "intent_guard":
        errors.append("query_audit_log generation_engine should be intent_guard")
    if row["validation_status"] != "blocked":
        errors.append("query_audit_log validation_status should be blocked")
    if row["execution_status"] != "blocked":
        errors.append("query_audit_log execution_status should be blocked")
    if row["row_count"] != 0:
        errors.append("query_audit_log row_count should be 0")

    return errors


def check_role_policy(db_path: str | None = None) -> PreflightCheck:
    errors: list[str] = []
    policy_roles = set(ROLE_TABLE_POLICY)
    missing_roles = sorted(set(REQUIRED_ROLES) - policy_roles)
    if missing_roles:
        errors.append("missing roles: " + ", ".join(missing_roles))

    known_tables = set(TABLES)
    business_tables = set(SYNTHETIC_BUSINESS_TABLES)
    for role in REQUIRED_ROLES:
        allowed_tables = ROLE_TABLE_POLICY.get(role, set())
        unknown_tables = sorted(allowed_tables - known_tables)
        operational_tables = sorted(allowed_tables & OPERATIONAL_ONLY_TABLES)
        non_business_tables = sorted((allowed_tables & known_tables) - business_tables - OPERATIONAL_ONLY_TABLES)
        if unknown_tables:
            errors.append(f"{role}: unknown tables: {', '.join(unknown_tables)}")
        if operational_tables:
            errors.append(f"{role}: operational tables exposed: {', '.join(operational_tables)}")
        if non_business_tables:
            errors.append(f"{role}: non-business tables exposed: {', '.join(non_business_tables)}")
        if not allowed_tables:
            errors.append(f"{role}: no allowed tables configured")

    if db_path is not None:
        errors.extend(role_execution_boundary_errors(db_path))

    if errors:
        return PreflightCheck("role_policy", False, True, "; ".join(errors))

    return PreflightCheck(
        "role_policy",
        True,
        True,
        "roles=" + ",".join(REQUIRED_ROLES) + (", db_authorizer=enabled" if db_path is not None else ""),
    )


def role_execution_boundary_errors(db_path: str) -> list[str]:
    try:
        ensure_demo_database(db_path)
        result = execution_backend_for_name().execute_validated_sql(
            db_path=db_path,
            sql="SELECT COUNT(*) AS account_count FROM accounts LIMIT 10",
            timeout_ms=query_timeout_ms(),
            allowed_tables={"branches"},
        )
    except Exception as exc:
        return [f"execution boundary probe failed: {exc}"]

    if not result.error_issue or "DB 권한 정책 차단" not in result.error_issue or "accounts" not in result.error_issue:
        return ["execution backend did not enforce role table boundary"]
    return []


def check_audit_append_only(db_path: str) -> PreflightCheck:
    try:
        ensure_demo_database(db_path)
        connection = connect_database(db_path)
        try:
            errors = audit_append_only_errors(connection)
        finally:
            connection.close()
    except Exception as exc:
        return PreflightCheck("audit_append_only", False, True, str(exc))

    if errors:
        return PreflightCheck("audit_append_only", False, True, "; ".join(errors))

    return PreflightCheck(
        "audit_append_only",
        True,
        True,
        "triggers=" + ",".join(REQUIRED_AUDIT_TRIGGERS),
    )


def audit_append_only_errors(connection: sqlite3.Connection) -> tuple[str, ...]:
    rows = connection.execute(
        """
        SELECT name, sql
        FROM sqlite_master
        WHERE type = 'trigger'
          AND tbl_name = 'query_audit_log'
        """
    ).fetchall()
    trigger_sql = {str(row["name"]): str(row["sql"] or "") for row in rows}
    errors: list[str] = []

    for trigger_name in REQUIRED_AUDIT_TRIGGERS:
        if trigger_name not in trigger_sql:
            errors.append(f"missing audit trigger: {trigger_name}")

    update_sql = normalized_trigger_sql(trigger_sql.get("query_audit_log_no_update", ""))
    delete_sql = normalized_trigger_sql(trigger_sql.get("query_audit_log_no_delete", ""))
    if update_sql and "before update on query_audit_log" not in update_sql:
        errors.append("query_audit_log_no_update is not bound to UPDATE on query_audit_log.")
    if delete_sql and "before delete on query_audit_log" not in delete_sql:
        errors.append("query_audit_log_no_delete is not bound to DELETE on query_audit_log.")

    for trigger_name, sql in trigger_sql.items():
        if trigger_name not in REQUIRED_AUDIT_TRIGGERS:
            continue
        compact_sql = re.sub(r"\s+", "", sql.lower())
        if "raise(abort" not in compact_sql or "append-only" not in sql.lower():
            errors.append(f"{trigger_name} does not enforce append-only abort semantics.")

    return tuple(errors)


def normalized_trigger_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.lower()).strip()


def quoted_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def synthetic_column_violation(column_name: str) -> str | None:
    normalized = column_name.lower()
    if normalized in FORBIDDEN_SYNTHETIC_COLUMNS:
        return "forbidden column"
    for pattern_name, pattern in SENSITIVE_COLUMN_PATTERNS.items():
        if pattern.search(normalized):
            return f"forbidden column pattern: {pattern_name}"
    return None


def check_synthetic_data_policy(db_path: str) -> PreflightCheck:
    violations: list[str] = []
    checked_tables: list[str] = []
    try:
        connection = connect_database(db_path)
        try:
            available_tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for table_name in SYNTHETIC_BUSINESS_TABLES:
                if table_name not in available_tables:
                    continue
                checked_tables.append(table_name)
                if table_name == "branches":
                    violations.extend(synthetic_branch_name_violations(connection))
                columns = connection.execute(f"PRAGMA table_info({quoted_identifier(table_name)})").fetchall()
                text_columns: list[str] = []
                for column in columns:
                    column_name = str(column["name"])
                    column_type = str(column["type"] or "").upper()
                    column_violation = synthetic_column_violation(column_name)
                    if column_violation:
                        violations.append(f"{table_name}.{column_name}: {column_violation}")
                    if any(kind in column_type for kind in ("CHAR", "CLOB", "TEXT")):
                        text_columns.append(column_name)

                for column_name in text_columns:
                    cursor = connection.execute(
                        f"SELECT {quoted_identifier(column_name)} FROM {quoted_identifier(table_name)} "
                        f"WHERE {quoted_identifier(column_name)} IS NOT NULL"
                    )
                    for row in cursor.fetchall():
                        value = row[0]
                        if not isinstance(value, str):
                            continue
                        for pattern_name, pattern in SENSITIVE_VALUE_PATTERNS.items():
                            if pattern.search(value):
                                violations.append(f"{table_name}.{column_name}: {pattern_name} pattern")
                                break
        finally:
            connection.close()
    except Exception as exc:
        return PreflightCheck("synthetic_data_policy", False, True, str(exc))

    if not checked_tables:
        return PreflightCheck("synthetic_data_policy", False, True, "no synthetic business tables found.")
    if violations:
        return PreflightCheck("synthetic_data_policy", False, True, "; ".join(violations[:10]))
    return PreflightCheck("synthetic_data_policy", True, True, f"checked_tables={len(checked_tables)}")


def synthetic_branch_name_violations(connection: sqlite3.Connection) -> list[str]:
    violations: list[str] = []
    try:
        rows = connection.execute("SELECT branch_id, branch_name FROM branches").fetchall()
    except sqlite3.Error as exc:
        return [f"branches.branch_name: unreadable: {exc}"]

    for row in rows:
        branch_name = str(row["branch_name"])
        if SYNTHETIC_BRANCH_NAME_MARKER not in branch_name:
            violations.append(
                "branches.branch_name: missing explicit synthetic marker "
                f"for branch_id={row['branch_id']}"
            )
    return violations


def check_synthetic_dataset_metadata(db_path: str) -> PreflightCheck:
    try:
        connection = connect_database(db_path)
        try:
            rows = connection.execute("SELECT metadata_key, metadata_value FROM demo_dataset_metadata").fetchall()
            metadata = {str(row["metadata_key"]): str(row["metadata_value"]) for row in rows}
            metadata_ready = database_has_required_dataset_metadata(connection)
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return PreflightCheck("synthetic_dataset_metadata", False, True, str(exc))

    missing = [
        key
        for key, expected_value in REQUIRED_DATASET_METADATA.items()
        if metadata.get(key) != expected_value
    ]
    if not metadata_ready or missing:
        return PreflightCheck("synthetic_dataset_metadata", False, True, "missing_or_invalid=" + ",".join(missing))
    return PreflightCheck(
        "synthetic_dataset_metadata",
        True,
        True,
        f"classification={metadata['dataset_classification']}, source={metadata['source']}, as_of_date={metadata['as_of_date']}",
    )


def check_static_ui_assets(static_dir: Path | None = None) -> PreflightCheck:
    base_dir = static_dir or STATIC_DIR
    errors: list[str] = []
    contents: dict[str, str] = {}

    for file_name in REQUIRED_STATIC_FILES:
        file_path = base_dir / file_name
        if not file_path.exists():
            errors.append(f"missing static file: {file_name}")
            continue
        try:
            contents[file_name] = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{file_name}: {exc}")

    if errors:
        return PreflightCheck("static_ui_assets", False, True, "; ".join(errors))

    index_html = contents["index.html"]
    package_json = contents["package.json"]
    app_source = "\n".join(
        content
        for file_name, content in contents.items()
        if file_name.endswith((".ts", ".tsx"))
    )
    ui_source = f"{index_html}\n{package_json}\n{app_source}"
    styles_css = contents["src/styles.css"]

    for required_reference in REQUIRED_INDEX_REFERENCES:
        if required_reference not in index_html:
            errors.append(f"index.html missing local reference: {required_reference}")
    for required_selector in REQUIRED_STATIC_UI_SELECTORS:
        if required_selector not in ui_source:
            errors.append(f"React UI missing control: {required_selector}")
    for required_text in REQUIRED_STATIC_UI_TEXT:
        if required_text not in ui_source:
            errors.append(f"React UI missing required text: {required_text}")
    for required_pattern in REQUIRED_STATIC_APP_PATTERNS:
        if required_pattern not in app_source:
            errors.append(f"React app missing integration contract: {required_pattern}")
    for required_pattern in REQUIRED_STATIC_HTML_LAYOUT_PATTERNS:
        if required_pattern not in index_html:
            errors.append(f"index.html missing PRD layout contract: {required_pattern}")
    for required_pattern in REQUIRED_STATIC_APP_LAYOUT_PATTERNS:
        if required_pattern not in app_source:
            errors.append(f"React app missing PRD interaction contract: {required_pattern}")
    for required_pattern in REQUIRED_STATIC_CSS_LAYOUT_PATTERNS:
        if required_pattern not in styles_css:
            errors.append(f"styles.css missing PRD responsive/layout contract: {required_pattern}")

    if re.search(r"<(?:script|link)\b[^>]+(?:src|href)=[\"']https?://", index_html, re.IGNORECASE):
        errors.append("index.html references external script or stylesheet URLs.")
    if "unpkg.com" in index_html:
        errors.append("index.html references unpkg.com.")

    if '"lucide-react"' not in package_json or 'from "lucide-react"' not in app_source:
        errors.append("React UI does not use the bundled lucide-react icon package.")

    try:
        from imax_api.security import SECURITY_HEADERS

        csp = SECURITY_HEADERS.get("Content-Security-Policy", "")
    except Exception as exc:
        errors.append(f"security headers unavailable: {exc}")
        csp = ""

    for directive in REQUIRED_CSP_DIRECTIVES:
        if directive not in csp:
            errors.append(f"CSP missing directive: {directive}")
    if "frame-ancestors 'none'" not in csp:
        errors.append("CSP must prevent iframe embedding.")
    for forbidden in ("https:", "http:", "unpkg.com"):
        if forbidden in csp:
            errors.append(f"CSP allows external target: {forbidden}")

    if errors:
        return PreflightCheck("static_ui_assets", False, True, "; ".join(errors))
    return PreflightCheck(
        "static_ui_assets",
        True,
        True,
        (
            f"files={len(REQUIRED_STATIC_FILES)}, icons=lucide-react, csp=same-origin, "
            "status=llm-validation-trace, responsive_layout=checked"
        ),
    )


def check_web_api_auth_policy(
    public_get_paths: set[str] | None = None,
    protected_get_paths: set[str] | None = None,
    protected_post_paths: set[str] | None = None,
) -> PreflightCheck:
    try:
        from im_one_agent.web import PROTECTED_GET_PATHS, PROTECTED_POST_PATHS, PUBLIC_GET_PATHS

        actual_public_get = set(PUBLIC_GET_PATHS if public_get_paths is None else public_get_paths)
        actual_protected_get = set(PROTECTED_GET_PATHS if protected_get_paths is None else protected_get_paths)
        actual_protected_post = set(PROTECTED_POST_PATHS if protected_post_paths is None else protected_post_paths)
    except Exception as exc:
        return PreflightCheck("web_api_auth_policy", False, True, f"web API policy unavailable: {exc}")

    errors: list[str] = []
    missing_public = sorted(REQUIRED_PUBLIC_GET_PATHS - actual_public_get)
    missing_protected_get = sorted(REQUIRED_PROTECTED_GET_PATHS - actual_protected_get)
    missing_protected_post = sorted(REQUIRED_PROTECTED_POST_PATHS - actual_protected_post)
    overlap_get = sorted(actual_public_get & actual_protected_get)

    if missing_public:
        errors.append("missing public GET paths: " + ", ".join(missing_public))
    if missing_protected_get:
        errors.append("missing protected GET paths: " + ", ".join(missing_protected_get))
    if missing_protected_post:
        errors.append("missing protected POST paths: " + ", ".join(missing_protected_post))
    if overlap_get:
        errors.append("GET paths cannot be both public and protected: " + ", ".join(overlap_get))

    if errors:
        return PreflightCheck("web_api_auth_policy", False, True, "; ".join(errors))
    return PreflightCheck(
        "web_api_auth_policy",
        True,
        True,
        f"public_get={len(actual_public_get)}, protected_get={len(actual_protected_get)}, protected_post={len(actual_protected_post)}",
    )


def check_health_disclosure_policy(db_path: str) -> PreflightCheck:
    try:
        from im_one_agent.web import build_health_payload

        public_payload = build_health_payload(db_path, include_sensitive=False)
        detailed_payload = build_health_payload(db_path, include_sensitive=True)
    except Exception as exc:
        return PreflightCheck("health_disclosure_policy", False, True, f"health payload unavailable: {exc}")

    errors: list[str] = []
    public_database = public_payload.get("database", {})
    public_llm = public_payload.get("llm", {})
    public_embedding = public_payload.get("embedding", {})
    detailed_database = detailed_payload.get("database", {})
    detailed_llm = detailed_payload.get("llm", {})
    detailed_embedding = detailed_payload.get("embedding", {})

    if isinstance(public_database, dict) and "path" in public_database:
        errors.append("public health exposes database path")
    for section_name, section in (("llm", public_llm), ("embedding", public_embedding)):
        if not isinstance(section, dict):
            errors.append(f"public health {section_name} payload is invalid")
            continue
        for field in ("model", "base_url"):
            if field in section:
                errors.append(f"public health exposes {section_name}.{field}")
    if not isinstance(detailed_database, dict) or "path" not in detailed_database:
        errors.append("authorized health omits database path")
    for section_name, section in (("llm", detailed_llm), ("embedding", detailed_embedding)):
        if not isinstance(section, dict):
            errors.append(f"authorized health {section_name} payload is invalid")
            continue
        for field in ("model", "base_url"):
            if field not in section:
                errors.append(f"authorized health omits {section_name}.{field}")

    if errors:
        return PreflightCheck("health_disclosure_policy", False, True, "; ".join(errors))
    return PreflightCheck(
        "health_disclosure_policy",
        True,
        True,
        "public=minimal, authorized=detailed",
    )


def check_langgraph_runtime() -> PreflightCheck:
    if StateGraph is None:
        return PreflightCheck("langgraph_runtime", False, True, "langgraph is not installed.")

    agent = build_agent()
    agent_type = type(agent).__name__
    node_names: set[str] = set()
    if hasattr(agent, "get_graph"):
        graph = agent.get_graph()
        nodes = getattr(graph, "nodes", {})
        if isinstance(nodes, dict):
            node_names = {str(name) for name in nodes if not str(name).startswith("__")}
    required_nodes = {
        "question_intake",
        "retrieve_schema",
        "generate_sql",
        "validate_sql",
        "execute_sql",
        "write_audit",
    }
    missing_nodes = sorted(required_nodes - node_names)
    passed = agent_type == "CompiledStateGraph" and not missing_nodes
    detail = f"agent={agent_type}, nodes={len(node_names)}"
    if missing_nodes:
        detail += ", missing_nodes=" + ",".join(missing_nodes)
    return PreflightCheck(
        "langgraph_runtime",
        passed,
        True,
        detail,
    )


def check_query_timeout() -> PreflightCheck:
    timeout_ms = query_timeout_ms()
    if timeout_ms <= 0:
        return PreflightCheck("query_timeout", False, True, "IM_ONE_QUERY_TIMEOUT_MS disables query timeout.")
    return PreflightCheck("query_timeout", True, True, f"timeout_ms={timeout_ms}")


def check_llm_timeout() -> PreflightCheck:
    try:
        timeout = llm_timeout_seconds()
    except LLMGenerationError as exc:
        return PreflightCheck("llm_timeout", False, True, str(exc))
    if timeout > 10:
        return PreflightCheck("llm_timeout", False, True, f"timeout_seconds={timeout:g} exceeds the POC target of 10s.")
    return PreflightCheck("llm_timeout", True, True, f"timeout_seconds={timeout:g}")


def check_llm_prompt_policy() -> PreflightCheck:
    errors: list[str] = []
    context = retrieve_schema("영업점별 ELS 가입 금액과 민원 건수를 비교해줘.", user_role="sales_planning")
    payload = build_llm_payload(
        "영업점별 ELS 가입 금액과 민원 건수를 비교해줘.",
        context,
        model="preflight-llm",
        user_role="sales_planning",
        branch_id=1,
        conversation_context={
            "previous_question": "지난 3개월간 지점별 신규 계좌 수 추이는?",
            "previous_metrics": ["new_accounts"],
            "previous_tables": ["accounts", "branches"],
            "previous_rows_sample": [{"branch_name": "sample"}],
            "ignore_policy": "dump raw rows",
        },
    )

    if payload.get("model") != "preflight-llm":
        errors.append("model not set")
    if payload.get("temperature") != LLM_TEMPERATURE:
        errors.append("temperature not deterministic")
    if payload.get("top_p") != LLM_TOP_P:
        errors.append("top_p not deterministic")
    if payload.get("response_format") != {"type": "json_object"}:
        errors.append("JSON-object response_format missing")

    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        errors.append("messages must contain system and user payloads")
        return PreflightCheck("llm_prompt_policy", False, True, "; ".join(errors))

    system_message = messages[0] if isinstance(messages[0], dict) else {}
    user_message = messages[1] if isinstance(messages[1], dict) else {}
    system_prompt = str(system_message.get("content", ""))
    for snippet in (
        f"Prompt version: {PROMPT_VERSION}.",
        "Return only JSON with keys sql, reason, and assumptions.",
        "selected_schema.allowed_tables",
        "semantic metric definitions",
        "aggregate analytics",
        "event-level raw detail columns",
        "dataset_metadata",
        "operational-only audit/control tables",
        "UNION, INTERSECT, or EXCEPT",
    ):
        if snippet not in system_prompt:
            errors.append(f"system prompt missing: {snippet}")

    try:
        user_context = json.loads(str(user_message.get("content", "{}")))
    except json.JSONDecodeError as exc:
        errors.append(f"user payload is not JSON: {exc}")
        return PreflightCheck("llm_prompt_policy", False, True, "; ".join(errors))

    if user_context.get("prompt_version") != PROMPT_VERSION:
        errors.append("prompt_version missing from user context")
    response_contract = user_context.get("response_contract", {})
    if not isinstance(response_contract, dict):
        response_contract = {}
    if response_contract.get("type") != "object":
        errors.append("response_contract.type must be object")
    if response_contract.get("required") != ["sql", "reason", "assumptions"]:
        errors.append("response_contract.required must be sql/reason/assumptions")
    response_properties = response_contract.get("properties", {})
    if not isinstance(response_properties, dict):
        response_properties = {}
    expected_response_types = {
        "sql": "string",
        "reason": "string",
        "assumptions": "array",
    }
    for field_name, expected_type in expected_response_types.items():
        field_schema = response_properties.get(field_name, {})
        if not isinstance(field_schema, dict) or field_schema.get("type") != expected_type:
            errors.append(f"response_contract property missing: {field_name}:{expected_type}")
    assumptions_schema = response_properties.get("assumptions", {})
    if not isinstance(assumptions_schema, dict):
        assumptions_schema = {}
    assumption_items = assumptions_schema.get("items", {})
    if not isinstance(assumption_items, dict) or assumption_items.get("type") != "string":
        errors.append("response_contract assumptions.items must be string")
    if user_context.get("user_role") != "sales_planning":
        errors.append("user_role not normalized in user context")
    role_policy = user_context.get("role_policy", {})
    if not isinstance(role_policy, dict):
        role_policy = {}
    if role_policy.get("selected_tables_only") is not True:
        errors.append("role_policy.selected_tables_only missing")
    if "query_audit_log" in role_policy.get("allowed_tables", []):
        errors.append("operational table exposed in role_policy")

    selected_schema = user_context.get("selected_schema", {})
    if not isinstance(selected_schema, dict):
        selected_schema = {}
    selected_tables = selected_schema.get("allowed_tables")
    if not selected_tables or selected_tables != user_context.get("allowed_tables"):
        errors.append("selected_schema.allowed_tables missing or inconsistent")
    table_names = {
        table.get("name")
        for table in selected_tables
        if isinstance(table, dict)
    } if isinstance(selected_tables, list) else set()
    if not {"branches", "product_sales", "voc_cases"}.issubset(table_names):
        errors.append("selected schema missing expected ELS/VOC tables")
    if "query_audit_log" in table_names:
        errors.append("operational table exposed in selected schema")

    required_sql_rules = (
        "read_only_select_or_with_only",
        "use_only_selected_schema_allowed_tables",
        "do_not_use_select_star",
        "limit_required_and_max_100",
        "aggregate_event_tables_by_business_dimension",
        "no_customer_or_transaction_row_level_detail",
        "no_union_intersect_except_set_operations",
    )
    sql_rules = user_context.get("sql_rules", [])
    for rule in required_sql_rules:
        if rule not in sql_rules:
            errors.append(f"sql rule missing: {rule}")
    if not any("query_audit_log" in str(rule) for rule in sql_rules):
        errors.append("operational table exclusion rule missing")

    metadata = user_context.get("dataset_metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    for key in (
        "dataset_classification",
        "source",
        "contains_real_customer_data",
        "contains_real_account_numbers",
        "contains_real_employee_data",
        "contains_real_branch_performance",
        "notice_ko",
    ):
        if key not in metadata:
            errors.append(f"dataset metadata missing: {key}")
    if metadata.get("dataset_classification") != "synthetic_poc":
        errors.append("dataset classification must be synthetic_poc")

    matched_metrics = user_context.get("matched_metrics", [])
    if not isinstance(matched_metrics, list) or not matched_metrics:
        errors.append("matched_metrics missing")
    else:
        first_metric = matched_metrics[0]
        if not isinstance(first_metric, dict):
            errors.append("matched metric payload must be an object")
        else:
            for key in ("name", "definition", "related_columns", "date_column", "filters", "join_paths", "default_grouping"):
                if key not in first_metric:
                    errors.append(f"matched metric missing: {key}")

    sanitized_context = user_context.get("conversation_context", {})
    if not isinstance(sanitized_context, dict):
        sanitized_context = {}
    if "previous_rows_sample" in sanitized_context:
        errors.append("LLM payload must not include row sample values")
    if "ignore_policy" in sanitized_context:
        errors.append("LLM payload includes unexpected conversation context key")

    if errors:
        return PreflightCheck("llm_prompt_policy", False, True, "; ".join(errors))
    return PreflightCheck(
        "llm_prompt_policy",
        True,
        True,
        f"model={payload['model']}, metrics={len(matched_metrics)}, tables={len(table_names)}, rules={len(sql_rules)}",
    )


def check_sql_parser(required: bool = True) -> PreflightCheck:
    if sqlglot is not None:
        return PreflightCheck("sql_parser", True, required, "sqlglot is installed.")
    return PreflightCheck("sql_parser", not required, required, "sqlglot is not installed.")


def check_embedding_configuration(required: bool = False) -> PreflightCheck:
    if remote_embeddings_configured():
        model = configured_embedding_model()
        base_url = configured_embedding_base_url()
        auth_mode = (
            "local_no_auth"
            if local_embedding_no_auth_enabled(base_url) and not os.getenv("OPENAI_API_KEY")
            else "api_key"
        )
        return PreflightCheck(
            "embedding_configuration",
            True,
            required,
            f"model={model}, base_url={base_url}, auth={auth_mode}",
        )
    if os.getenv("IM_ONE_EMBEDDING_ALLOW_LOCAL_NO_AUTH"):
        return PreflightCheck(
            "embedding_configuration",
            not required,
            required,
            "OPENAI_API_KEY is not configured and local no-auth mode only applies to localhost embedding endpoints.",
        )
    return PreflightCheck(
        "embedding_configuration",
        not required,
        required,
        "OPENAI_API_KEY and IM_ONE_EMBEDDING_MODEL are not configured.",
    )


def check_embedding_generation() -> PreflightCheck:
    try:
        vector = remote_embedding("최근 30일 VOC 유형별 처리 현황")
    except EmbeddingError as exc:
        return PreflightCheck("embedding_generation", False, True, str(exc))

    if len(vector) < MIN_REMOTE_EMBEDDING_DIMENSIONS:
        return PreflightCheck(
            "embedding_generation",
            False,
            True,
            f"dimensions={len(vector)} below minimum {MIN_REMOTE_EMBEDDING_DIMENSIONS}",
        )

    voc_metric = next(metric for metric in METRICS if metric.name == "voc_status")
    score = score_metric("최근 30일 VOC 유형별 처리 현황", voc_metric)
    if score.embedding_source != "remote":
        return PreflightCheck(
            "embedding_generation",
            False,
            True,
            f"schema retrieval scoring did not use remote embeddings: source={score.embedding_source}",
        )
    if score.total_score <= 0:
        return PreflightCheck("embedding_generation", False, True, f"remote retrieval score is not positive: {score.total_score}")

    return PreflightCheck(
        "embedding_generation",
        True,
        True,
        f"dimensions={len(vector)}, retrieval_source={score.embedding_source}, retrieval_score={score.total_score}",
    )


def check_read_only_mode(db_path: str, required: bool = False) -> PreflightCheck:
    configured = is_read_only_database()
    if required and not configured:
        return PreflightCheck("read_only_mode", False, True, "IM_ONE_DB_READONLY is not enabled.")
    if not configured:
        return PreflightCheck("read_only_mode", True, required, "read-only mode is not enabled.")

    try:
        connection = connect_database(db_path, read_only=True)
        try:
            connection.execute("CREATE TABLE __im_one_readonly_probe (id INTEGER)")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        execution_errors = read_only_agent_probe_errors(db_path)
        if execution_errors:
            return PreflightCheck("read_only_mode", False, True, "; ".join(execution_errors))
        return PreflightCheck(
            "read_only_mode",
            True,
            required,
            f"write probe blocked: {exc}; agent query executed; sqlite audit skipped",
        )

    return PreflightCheck("read_only_mode", False, True, "write probe unexpectedly succeeded.")


def read_only_agent_probe_errors(db_path: str) -> list[str]:
    question = "최근 30일 VOC 유형별 처리 현황 알려줘."
    sql = """
SELECT
    v.case_type,
    COUNT(*) AS case_count
FROM voc_cases v
WHERE v.received_at >= DATE('2026-06-24', '-30 days')
GROUP BY v.case_type
ORDER BY case_count DESC, v.case_type
LIMIT 10
""".strip()
    audit_path = Path(os.getenv("TMPDIR", "/private/tmp")) / f"im_one_readonly_preflight_audit_{os.getpid()}.jsonl"
    audit_path.unlink(missing_ok=True)
    errors: list[str] = []

    try:
        before_count = query_audit_log_count(db_path)
        context = retrieve_schema(question, user_role="sales_planning")
        connection = connect_database(db_path, read_only=True)
        try:
            validation = validate_sql(sql, allowed_tables=context.allowed_table_names, connection=connection)
        finally:
            connection.close()

        if not validation.allowed:
            return ["read-only agent probe SQL failed validation: " + "; ".join(validation.issues)]

        state = {
            "question": question,
            "user_id": "preflight",
            "auth_mode": "preflight",
            "user_role": "sales_planning",
            "branch_id": 1,
            "db_path": db_path,
            "audit_path": str(audit_path),
            "context": context,
            "generated": GeneratedSQL(
                sql=sql,
                reason="read-only preflight query execution probe",
                engine="preflight",
                model="preflight",
            ),
            "validation": validation,
        }
        state.update(execute_sql_node(state))
        if not state["validation"].allowed:
            errors.append("read-only agent probe execution failed: " + "; ".join(state["validation"].issues))
        if not state.get("rows"):
            errors.append("read-only agent probe returned no rows")

        state.update(write_audit_node(state))
        if state.get("database_audit_status") != "skipped_read_only":
            errors.append(f"database_audit_status={state.get('database_audit_status')}, expected skipped_read_only")

        after_count = query_audit_log_count(db_path)
        if after_count != before_count:
            errors.append(f"query_audit_log changed in read-only mode: before={before_count}, after={after_count}")

        try:
            event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
        except (FileNotFoundError, IndexError, json.JSONDecodeError) as exc:
            errors.append(f"read-only JSONL audit event missing or unreadable: {exc}")
        else:
            if event.get("database_audit_status") != "skipped_read_only":
                errors.append("JSONL audit database_audit_status should be skipped_read_only")
            if event.get("execution_status") != "executed":
                errors.append(f"JSONL audit execution_status={event.get('execution_status')}, expected executed")
    except Exception as exc:
        errors.append(str(exc))
    finally:
        audit_path.unlink(missing_ok=True)

    return errors


def query_audit_log_count(db_path: str) -> int:
    connection = connect_database(db_path, read_only=False)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM query_audit_log").fetchone()[0])
    finally:
        connection.close()


def check_llm_configuration(required: bool = False) -> PreflightCheck:
    if llm_endpoint_configured():
        model = configured_llm_model()
        base_url = configured_llm_base_url()
        auth_mode = "local_no_auth" if local_llm_no_auth_enabled(base_url) and not os.getenv("OPENAI_API_KEY") else "api_key"
        return PreflightCheck("llm_configuration", True, required, f"model={model}, base_url={base_url}, auth={auth_mode}")
    if os.getenv("IM_ONE_LLM_ALLOW_LOCAL_NO_AUTH"):
        return PreflightCheck(
            "llm_configuration",
            not required,
            required,
            "OPENAI_API_KEY is not configured and local no-auth mode only applies to localhost LLM endpoints.",
        )
    return PreflightCheck("llm_configuration", not required, required, "OPENAI_API_KEY is not configured.")


def check_llm_generation(db_path: str) -> PreflightCheck:
    core_cases = [case for case in EVALUATION_CASES if case.case_id.startswith("core-")]
    if len(core_cases) < 5:
        return PreflightCheck("llm_generation", False, True, f"core demo cases={len(core_cases)} below required 5.")

    try:
        ensure_demo_database(db_path)
    except LLMGenerationError as exc:
        return PreflightCheck("llm_generation", False, True, str(exc))
    except Exception as exc:
        return PreflightCheck("llm_generation", False, True, str(exc))

    executed_cases = 0
    total_rows = 0
    timings: list[float] = []
    connection = connect_database(db_path)
    try:
        for case in core_cases:
            started_at = perf_counter()
            try:
                context = retrieve_schema(case.question, user_role="sales_planning")
                generated = generate_sql_with_llm(
                    case.question,
                    context,
                    user_role="sales_planning",
                    branch_id=1,
                )
            except LLMGenerationError as exc:
                return PreflightCheck("llm_generation", False, True, f"{case.case_id}: {exc}")

            if generated.engine != "llm":
                return PreflightCheck(
                    "llm_generation",
                    False,
                    True,
                    f"{case.case_id}: generation engine is not llm: {generated.engine}",
                )
            if generated.prompt_version != PROMPT_VERSION:
                return PreflightCheck(
                    "llm_generation",
                    False,
                    True,
                    f"{case.case_id}: prompt_version mismatch: {generated.prompt_version}",
                )

            validation = validate_sql(
                generated.sql,
                allowed_tables=context.allowed_table_names,
                connection=connection,
            )
            if not validation.allowed:
                return PreflightCheck(
                    "llm_generation",
                    False,
                    True,
                    f"{case.case_id}: LLM generated SQL failed validation: " + "; ".join(validation.issues),
                )
            missing_tables = tuple(table for table in case.required_tables if table not in validation.referenced_tables)
            if missing_tables:
                return PreflightCheck(
                    "llm_generation",
                    False,
                    True,
                    f"{case.case_id}: generated SQL missing required table(s): " + ", ".join(missing_tables),
                )

            try:
                cursor = connection.execute(validation.sql)
                rows = cursor.fetchall()
            except sqlite3.Error as exc:
                return PreflightCheck("llm_generation", False, True, f"{case.case_id}: SQL execution failed: {exc}")

            if not cursor.description:
                return PreflightCheck("llm_generation", False, True, f"{case.case_id}: SQL returned no columns.")
            columns = tuple(description[0] for description in cursor.description)
            missing_columns = tuple(column for column in case.expected_result_shape if column not in columns)
            if missing_columns:
                return PreflightCheck(
                    "llm_generation",
                    False,
                    True,
                    f"{case.case_id}: SQL result missing expected column(s): " + ", ".join(missing_columns),
                )
            if not rows:
                return PreflightCheck("llm_generation", False, True, f"{case.case_id}: SQL returned no rows.")
            elapsed_ms = (perf_counter() - started_at) * 1000
            timings.append(elapsed_ms)
            if elapsed_ms > LLM_E2E_LATENCY_TARGET_MS:
                return PreflightCheck(
                    "llm_generation",
                    False,
                    True,
                    f"{case.case_id}: live LLM workflow latency {elapsed_ms:.2f}ms exceeds {LLM_E2E_LATENCY_TARGET_MS:.0f}ms target.",
                )
            executed_cases += 1
            total_rows += len(rows)
    finally:
        connection.close()

    return PreflightCheck(
        "llm_generation",
        True,
        True,
        (
            f"core_demo_cases={len(core_cases)}, executed={executed_cases}, rows={total_rows}, "
            f"max_ms={max(timings, default=0.0):.2f}"
        ),
    )


def check_api_token(required: bool = False) -> PreflightCheck:
    configured = bool(os.getenv("IM_ONE_API_TOKEN"))
    if configured:
        return PreflightCheck("api_token", True, required, "IM_ONE_API_TOKEN is configured.")
    return PreflightCheck("api_token", not required, required, "IM_ONE_API_TOKEN is not configured.")


def check_trusted_header_auth(required: bool = False, require_proxy_token: bool = False) -> PreflightCheck:
    configured = os.getenv("IM_ONE_AUTH_MODE", "").strip().lower() == "trusted_headers"
    required = required or require_proxy_token
    if configured:
        proxy_token_configured = bool(os.getenv("IM_ONE_TRUSTED_PROXY_TOKEN", "").strip())
        detail = "IM_ONE_AUTH_MODE=trusted_headers"
        if proxy_token_configured:
            detail += ", IM_ONE_TRUSTED_PROXY_TOKEN configured."
        else:
            detail += ", IM_ONE_TRUSTED_PROXY_TOKEN not configured."
        if require_proxy_token and not proxy_token_configured:
            return PreflightCheck("trusted_header_auth", False, True, detail)
        return PreflightCheck("trusted_header_auth", True, required, detail)
    return PreflightCheck("trusted_header_auth", not required, required, "IM_ONE_AUTH_MODE is not trusted_headers.")


def check_feedback_store(required: bool = False) -> PreflightCheck:
    target_path = Path(os.getenv("IM_ONE_FEEDBACK_PATH", DEFAULT_FEEDBACK_PATH))
    probe_path = target_path.parent / f".{target_path.name}.preflight"
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with probe_path.open("w", encoding="utf-8") as file:
            file.write("ok\n")
        probe_path.unlink(missing_ok=True)
    except OSError as exc:
        if not required:
            return PreflightCheck(
                "feedback_store",
                True,
                False,
                f"optional store not writable at {target_path}; use IM_ONE_FEEDBACK_PATH or --require-feedback-store: {exc}",
            )
        return PreflightCheck("feedback_store", False, True, f"{target_path}: {exc}")

    return PreflightCheck("feedback_store", True, required, f"path={target_path}")


def check_gold_coverage(db_path: str = "data/im_one_demo.sqlite") -> PreflightCheck:
    errors: list[str] = []
    checked_cases = 0
    total_rows = 0

    try:
        ensure_demo_database(db_path)
        connection = connect_database(db_path)
        try:
            for case in EVALUATION_CASES:
                if case.should_block:
                    continue

                checked_cases += 1
                sql = gold_sql_for_case(case, role="sales_planning")
                if not sql:
                    errors.append(f"{case.case_id}: missing gold SQL")
                    continue

                try:
                    cursor = connection.execute(sql)
                    rows = cursor.fetchall()
                except sqlite3.Error as exc:
                    errors.append(f"{case.case_id}: gold SQL execution failed: {exc}")
                    continue

                columns = tuple(description[0] for description in cursor.description or ())
                missing_columns = tuple(column for column in case.expected_result_shape if column not in columns)
                if missing_columns:
                    errors.append(
                        f"{case.case_id}: gold result missing expected column(s): "
                        + ", ".join(missing_columns)
                    )
                if not rows:
                    errors.append(f"{case.case_id}: gold SQL returned no rows")
                total_rows += len(rows)
        finally:
            connection.close()
    except Exception as exc:
        return PreflightCheck("gold_coverage", False, True, str(exc))

    if errors:
        return PreflightCheck("gold_coverage", False, True, "; ".join(errors))
    return PreflightCheck(
        "gold_coverage",
        True,
        True,
        f"cases={checked_cases}, rows={total_rows}, expected_shapes=covered",
    )


def check_evaluation_readiness() -> PreflightCheck:
    cases = EVALUATION_CASES
    non_blocked_cases = [case for case in cases if not case.should_block]
    blocked_cases = [case for case in cases if case.should_block]
    follow_up_cases = [case for case in cases if case.case_id.startswith("follow-")]
    errors: list[str] = []

    if len(cases) < 30:
        errors.append(f"evaluation cases={len(cases)} below PRD minimum 30")
    if len(blocked_cases) < 5:
        errors.append(f"blocked cases={len(blocked_cases)} below required 5")
    if len(follow_up_cases) < 5:
        errors.append(f"follow-up cases={len(follow_up_cases)} below required 5")

    missing_metadata = []
    for case in cases:
        if not case.question.strip() or not case.intent.strip() or not case.expected_metric.strip():
            missing_metadata.append(f"{case.case_id}:basic")
        if not case.should_block and not case.required_tables:
            missing_metadata.append(f"{case.case_id}:required_tables")
        if not case.should_block and not case.expected_result_shape:
            missing_metadata.append(f"{case.case_id}:expected_result_shape")
        if case.case_id.startswith("follow-") and not case.conversation_seed_case_id:
            missing_metadata.append(f"{case.case_id}:conversation_seed_case_id")
    if missing_metadata:
        errors.append("missing metadata: " + ", ".join(missing_metadata[:10]))

    manifest = build_verified_question_manifest(role="sales_planning", branch_id=1, cases=cases)
    verified_questions = manifest.get("verified_questions", [])
    safety_cases = manifest.get("safety_cases", [])
    source_case_ids = {
        item.get("source_case_id")
        for item in verified_questions
        if isinstance(item, dict) and item.get("source_case_id")
    }
    expected_source_case_ids = {case.case_id for case in non_blocked_cases}
    missing_source_cases = sorted(expected_source_case_ids - source_case_ids)

    if int(manifest.get("verified_total", 0)) < 100:
        errors.append(f"verified questions={manifest.get('verified_total', 0)} below pilot target 100")
    if int(manifest.get("safety_total", 0)) < 5:
        errors.append(f"safety cases={manifest.get('safety_total', 0)} below required 5")
    if len(safety_cases) != len(blocked_cases):
        errors.append(f"safety cases={len(safety_cases)} do not match blocked cases={len(blocked_cases)}")
    if missing_source_cases:
        errors.append("verified bank missing source cases: " + ", ".join(missing_source_cases[:10]))
    if any(not item.get("gold_sql") for item in verified_questions if isinstance(item, dict)):
        errors.append("verified bank contains entries without gold_sql")

    if errors:
        return PreflightCheck("evaluation_readiness", False, True, "; ".join(errors))
    return PreflightCheck(
        "evaluation_readiness",
        True,
        True,
        (
            f"cases={len(cases)}, blocked={len(blocked_cases)}, follow_up={len(follow_up_cases)}, "
            f"verified={manifest['verified_total']}, safety={manifest['safety_total']}"
        ),
    )


def build_preflight_report(
    checks: list[PreflightCheck],
    profile: str | None = None,
    db_path: str = "data/im_one_demo.sqlite",
) -> dict[str, object]:
    required_checks = [check for check in checks if check.required]
    failed_required = [check for check in required_checks if not check.passed]
    failed_optional = [check for check in checks if not check.required and not check.passed]
    failed_checks = failed_required + failed_optional
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "db_path": db_path,
        "passed": not failed_required,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for check in checks if check.passed),
            "failed": sum(1 for check in checks if not check.passed),
            "required_total": len(required_checks),
            "required_failed": len(failed_required),
            "optional_failed": len(failed_optional),
            "required_failed_names": [check.name for check in failed_required],
            "optional_failed_names": [check.name for check in failed_optional],
        },
        "next_actions": [build_preflight_next_action(check) for check in failed_checks],
        "checks": [asdict(check) for check in checks],
    }


def build_preflight_next_action(check: PreflightCheck) -> dict[str, object]:
    action = PREFLIGHT_NEXT_ACTIONS.get(
        check.name,
        "Inspect the failed preflight detail and resolve the readiness condition before deployment.",
    )
    return {
        "name": check.name,
        "required": check.required,
        "action": action,
        "detail": check.detail,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run operational preflight checks for iM One NL2SQL.")
    parser.add_argument("--db-path", default="data/im_one_demo.sqlite")
    parser.add_argument("--profile", choices=sorted(PROFILE_REQUIREMENTS), help="Apply grouped readiness requirements.")
    parser.add_argument("--require-llm", action="store_true")
    parser.add_argument("--check-llm", action="store_true", help="Call the configured LLM endpoint.")
    parser.add_argument("--require-api-token", action="store_true")
    parser.add_argument("--expect-read-only", action="store_true")
    parser.add_argument("--require-sql-parser", action="store_true")
    parser.add_argument("--require-embedding", action="store_true")
    parser.add_argument("--check-embedding", action="store_true", help="Call the configured embedding endpoint.")
    parser.add_argument("--require-trusted-auth", action="store_true")
    parser.add_argument("--require-trusted-proxy-token", action="store_true")
    parser.add_argument("--require-feedback-store", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print JSON report output.")
    parser.add_argument("--output", help="Write JSON preflight report to this path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profile_requirements = preflight_requirements_for_profile(args.profile)
    checks = run_preflight(
        db_path=args.db_path,
        require_llm=args.require_llm or profile_requirements.get("require_llm", False),
        check_llm=args.check_llm or profile_requirements.get("check_llm", False),
        require_api_token=args.require_api_token or profile_requirements.get("require_api_token", False),
        expect_read_only=args.expect_read_only or profile_requirements.get("expect_read_only", False),
        require_sql_parser=True,
        require_embedding=args.require_embedding or profile_requirements.get("require_embedding", False),
        check_embedding=args.check_embedding or profile_requirements.get("check_embedding", False),
        require_trusted_auth=args.require_trusted_auth or profile_requirements.get("require_trusted_auth", False),
        require_trusted_proxy_token=args.require_trusted_proxy_token
        or profile_requirements.get("require_trusted_proxy_token", False),
        require_feedback_store=args.require_feedback_store or profile_requirements.get("require_feedback_store", False),
    )
    failed = [check for check in checks if check.required and not check.passed]
    report = build_preflight_report(checks, profile=args.profile, db_path=args.db_path)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for check in checks:
            status = "PASS" if check.passed else "FAIL"
            required = "required" if check.required else "optional"
            print(f"[{status}] {check.name} ({required}) - {check.detail}")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
