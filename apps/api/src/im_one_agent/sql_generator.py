from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from im_one_agent.conversation import sanitize_conversation_context
from im_one_agent.domain import AS_OF_DATE, ROLE_TABLE_POLICY, normalize_user_role
from im_one_agent.env import load_project_env
from im_one_agent.sample_data import REQUIRED_DATASET_METADATA
from im_one_agent.schema_retrieval import SchemaContext

load_project_env()

PROMPT_VERSION = "im-one-nl2sql-v1"
DEFAULT_LLM_MODEL = "gpt-5.6-luna"
DEFAULT_LLM_TIMEOUT_SECONDS = 10.0
LLM_TEMPERATURE = 0
LLM_TOP_P = 1
OPERATIONAL_ONLY_TABLES = ("query_audit_log",)
LOCAL_NO_AUTH_VALUES = {"1", "true", "yes", "on"}
LOCAL_LLM_HOSTS = {"localhost", "127.0.0.1", "::1"}
CANONICAL_RESULT_COLUMNS: dict[str, tuple[str, ...]] = {
    "new_accounts": ("branch_name", "opened_month", "new_account_count"),
    "high_risk_product_sales": ("branch_name", "high_risk_sale_count", "total_amount"),
    "voc_status": ("case_type", "status", "case_count"),
    "els_sales_vs_voc": ("branch_name", "els_amount", "els_count", "voc_count"),
    "investment_review_status": ("branch_name", "status", "review_count"),
    "new_accounts_vs_target": ("branch_name", "target_month", "target_value", "actual_value", "gap"),
}
METRIC_SQL_GUIDANCE: dict[str, tuple[str, ...]] = {
    "new_accounts": (
        "For recent three-month trends, group by branches.branch_name and STRFTIME('%Y-%m', accounts.opened_at) AS opened_month.",
        "Order account trends by opened_month, then branch_name.",
    ),
    "high_risk_product_sales": (
        "For high-risk product sales, select COUNT(product_sales.sale_id) AS high_risk_sale_count and SUM(product_sales.amount) AS total_amount.",
        "Use product_sales.risk_grade >= 4 unless the user explicitly asks for a single risk grade.",
        "For 이번 달, use product_sales.sold_at >= DATE(as_of_date, 'start of month').",
        "Order by high_risk_sale_count DESC, total_amount DESC.",
    ),
    "voc_status": (
        "For VOC status summaries, group by voc_cases.case_type and voc_cases.status unless the user explicitly narrows one dimension.",
        "For 최근 30일, use voc_cases.received_at >= DATE(as_of_date, '-30 days').",
        "Order VOC status summaries by case_count DESC, then case_type.",
    ),
    "els_sales_vs_voc": (
        "For ELS versus VOC comparison, aggregate ELS sales and VOC cases in separate CTEs by branch_id, then join through branches.",
        "Select SUM(product_sales.amount) AS els_amount, COUNT(product_sales.sale_id) AS els_count, and COUNT(voc_cases.case_id) AS voc_count.",
        "Use product_sales.product_type = 'ELS' and the same recent three-month period for both sales and VOC counts.",
        "Order by els_amount DESC, voc_count DESC.",
    ),
    "investment_review_status": (
        "For incomplete investment review questions, use investment_reviews.status != 'completed'.",
        "Select investment_reviews.status AS status and COUNT(investment_reviews.review_id) AS review_count.",
        "Group by branches.branch_name and investment_reviews.status.",
        "Order by review_count DESC, then branch_name.",
    ),
    "new_accounts_vs_target": (
        "Compare actual account counts with branch_targets where branch_targets.metric_name = 'new_accounts'.",
        "Select target_value, actual_value, and actual_value - target_value AS gap.",
    ),
}
LLM_RESPONSE_CONTRACT: dict[str, object] = {
    "type": "object",
    "required": ["sql", "reason", "assumptions"],
    "additionalProperties": False,
    "properties": {
        "sql": {
            "type": "string",
            "description": "SQLite SELECT or WITH query without semicolons.",
        },
        "reason": {
            "type": "string",
            "description": "Short explanation of how the SQL answers the business question.",
        },
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Explicit assumptions applied while interpreting ambiguous business terms.",
        },
    },
}


@dataclass(frozen=True)
class GeneratedSQL:
    sql: str
    reason: str
    engine: str = "llm"
    assumptions: tuple[str, ...] = ()
    error: str | None = None
    model: str = ""
    prompt_version: str = PROMPT_VERSION


class LLMGenerationError(RuntimeError):
    """Raised when the configured LLM cannot produce a valid SQL payload."""


def generate_sql(
    question: str,
    context: SchemaContext,
    user_role: str = "branch_manager",
    branch_id: int | None = 1,
    conversation_context: dict[str, object] | None = None,
) -> GeneratedSQL:
    """Generate SQL with the configured LLM endpoint."""
    return generate_sql_with_llm(
        question,
        context,
        user_role=user_role,
        branch_id=branch_id,
        conversation_context=conversation_context,
    )


def generate_sql_with_llm(
    question: str,
    context: SchemaContext,
    user_role: str = "branch_manager",
    branch_id: int | None = 1,
    conversation_context: dict[str, object] | None = None,
) -> GeneratedSQL:
    api_key = os.getenv("OPENAI_API_KEY")
    endpoint = configured_llm_base_url()
    if not api_key and not local_llm_no_auth_enabled(endpoint):
        raise LLMGenerationError("LLM endpoint credentials are not configured.")

    model = configured_llm_model()
    payload = build_llm_payload(
        question,
        context,
        model=model,
        user_role=user_role,
        branch_id=branch_id,
        conversation_context=conversation_context,
    )
    endpoint = endpoint.rstrip("/")
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(
        f"{endpoint}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=llm_timeout_seconds()) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise LLMGenerationError(f"LLM SQL generation failed: HTTP Error {exc.code}: {http_error_message(exc)}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LLMGenerationError(f"LLM SQL generation failed: {exc}") from exc

    try:
        content = raw_response["choices"][0]["message"]["content"]
        generated = json.loads(content)
        if not isinstance(generated, dict):
            raise TypeError("LLM response must be a JSON object.")
        required_keys = {"sql", "reason", "assumptions"}
        missing_keys = required_keys - set(generated)
        if missing_keys:
            raise KeyError(f"missing required LLM response keys: {', '.join(sorted(missing_keys))}")
        extra_keys = set(generated) - required_keys
        if extra_keys:
            raise KeyError(f"unexpected LLM response keys: {', '.join(sorted(extra_keys))}")
        if not isinstance(generated["sql"], str):
            raise TypeError("LLM response sql must be a string.")
        sql = generated["sql"].strip()
        if not isinstance(generated["reason"], str):
            raise TypeError("LLM response reason must be a string.")
        reason = generated["reason"].strip()
        raw_assumptions = generated["assumptions"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMGenerationError("LLM response did not match the expected JSON shape.") from exc

    if not sql:
        raise LLMGenerationError("LLM returned empty SQL.")
    if not reason:
        raise LLMGenerationError("LLM returned empty reason.")

    if not isinstance(raw_assumptions, list):
        raise LLMGenerationError("LLM response assumptions must be an array.")
    if not all(isinstance(item, str) for item in raw_assumptions):
        raise LLMGenerationError("LLM response assumptions array must contain only strings.")
    assumptions = tuple(item.strip() for item in raw_assumptions if item.strip())

    return GeneratedSQL(
        sql=sql,
        reason=reason,
        engine="llm",
        assumptions=assumptions,
        model=model,
        prompt_version=PROMPT_VERSION,
    )


def build_llm_payload(
    question: str,
    context: SchemaContext,
    model: str | None = None,
    user_role: str = "branch_manager",
    branch_id: int | None = 1,
    conversation_context: dict[str, object] | None = None,
) -> dict[str, object]:
    normalized_role = normalize_user_role(user_role)
    role_allowed_tables = sorted(ROLE_TABLE_POLICY[normalized_role])
    selected_tables = [
        {
            "name": table.name,
            "description": table.description,
            "columns": list(table.columns),
        }
        for table in context.tables
    ]
    safe_conversation_context = sanitize_llm_conversation_context(conversation_context or {})
    payload = {
        "model": model or configured_llm_model(),
        "messages": [
            {
                "role": "system",
                "content": "\n".join(
                    [
                        "You are an NL2SQL engine for a securities-company internal analytics tool.",
                        f"Prompt version: {PROMPT_VERSION}.",
                        "Return only JSON with keys sql, reason, and assumptions.",
                        "Generate SQLite-compatible read-only SELECT SQL.",
                        "Use only the allowed tables and columns in the provided context.",
                        "Treat selected_schema.allowed_tables as the executable table subset; role_policy.allowed_tables is only the broader role boundary.",
                        "Prefer the provided semantic metric definitions, filters, date columns, and join paths when a business term is ambiguous.",
                        "When canonical_result_columns are provided, use those exact output aliases in the SELECT list.",
                        "Follow metric_sql_guidance exactly when it is provided for a matched metric.",
                        "For aggregate ranking questions, order by the primary aggregate metric descending before tie-breaker dimensions.",
                        "Return aggregate analytics by business dimensions such as branch, month, product type, status, or case type.",
                        "Do not select customer-level, transaction-level, or event-level raw detail columns unless they are grouped as an explicit aggregate dimension.",
                        "Never put row-level identifier columns such as account_id, sale_id, case_id, review_id, target_id, or audit_id in SELECT, GROUP BY, or ORDER BY; use them only inside aggregate functions such as COUNT().",
                        "Treat dataset_metadata as the only data-level context; do not infer facts from unseen row values.",
                        "Always include a LIMIT of 100 or less.",
                        "Do not include semicolons, comments, DML, DDL, PRAGMA, or multiple statements.",
                        "Do not use UNION, INTERSECT, or EXCEPT set operations.",
                        "Never query operational-only audit/control tables.",
                        "If user_role is branch_manager, include the required branch_id filter in the SQL.",
                        "For follow-up questions, use the conversation_context to resolve references such as previous result, top rows, or 'that subset'.",
                        f"Use {AS_OF_DATE} as the current reference date when relative dates are requested.",
                    ]
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": question,
                        "prompt_version": PROMPT_VERSION,
                        "response_contract": LLM_RESPONSE_CONTRACT,
                        "retrieval_confidence": context.retrieval_confidence,
                        "clarification_options": list(context.clarification_options),
                        "user_role": normalized_role,
                        "role_policy": {
                            "role": normalized_role,
                            "allowed_tables": role_allowed_tables,
                            "selected_tables_only": True,
                            "branch_scope_required": normalized_role == "branch_manager",
                            "branch_id": branch_id if normalized_role == "branch_manager" else None,
                        },
                        "sql_rules": build_sql_rules(normalized_role),
                        "branch_scope": {"branch_id": branch_id} if normalized_role == "branch_manager" else None,
                        "dataset_metadata": build_llm_dataset_metadata(),
                        "conversation_context": safe_conversation_context,
                        "canonical_result_columns": canonical_result_columns_for(context),
                        "metric_sql_guidance": metric_sql_guidance_for(context),
                        "allowed_tables": selected_tables,
                        "selected_schema": {
                            "dialect": "sqlite",
                            "allowed_tables": selected_tables,
                        },
                        "matched_metrics": [
                            {
                                "name": metric.name,
                                "description": metric.description,
                                "definition": metric.definition,
                                "related_columns": list(metric.related_columns),
                                "date_column": metric.date_column,
                                "default_period": metric.default_period,
                                "filters": list(metric.filters),
                                "join_paths": list(metric.join_paths),
                                "default_grouping": metric.default_grouping,
                                "sample_question": metric.sample_question,
                            }
                            for metric in context.matched_metrics
                        ],
                        "business_rules": list(context.business_rules),
                        "example_queries": list(context.example_queries),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {"type": "json_object"},
    }
    payload.update(sampling_parameters_for_model(str(payload["model"])))
    return payload


def sanitize_llm_conversation_context(value: object) -> dict[str, object]:
    context = sanitize_conversation_context(value)
    context.pop("previous_rows_sample", None)
    return context


def build_llm_dataset_metadata() -> dict[str, str]:
    allowed_keys = (
        "dataset_classification",
        "source",
        "as_of_date",
        "contains_real_customer_data",
        "contains_real_account_numbers",
        "contains_real_employee_data",
        "contains_real_branch_performance",
        "notice_ko",
    )
    return {key: REQUIRED_DATASET_METADATA[key] for key in allowed_keys}


def build_sql_rules(user_role: str) -> list[str]:
    rules = [
        "dialect=sqlite",
        "read_only_select_or_with_only",
        "use_only_selected_schema_allowed_tables",
        "use_exact_canonical_result_column_aliases_when_provided",
        "do_not_query_operational_only_tables: " + ", ".join(OPERATIONAL_ONLY_TABLES),
        "do_not_use_select_star",
        "limit_required_and_max_100",
        "aggregate_event_tables_by_business_dimension",
        "no_customer_or_transaction_row_level_detail",
        "row_level_identifier_columns_only_inside_aggregate_functions",
        "no_semicolon_comments_or_multi_statement",
        "no_dml_ddl_pragma_attach_detach_vacuum_copy",
        "no_nested_subquery_use_with_cte_instead",
        "no_union_intersect_except_set_operations",
        "no_cross_join_tautological_join_recursive_cte_random_order_or_large_offset",
    ]
    if user_role == "branch_manager":
        rules.append("branch_manager_requires_branch_id_scope")
    return rules


def sampling_parameters_for_model(model: str) -> dict[str, float]:
    if model.startswith("gpt-5.6"):
        return {}
    return {"temperature": LLM_TEMPERATURE, "top_p": LLM_TOP_P}


def canonical_result_columns_for(context: SchemaContext) -> list[str]:
    columns: list[str] = []
    for metric in context.matched_metrics:
        for column in CANONICAL_RESULT_COLUMNS.get(metric.name, ()):
            if column not in columns:
                columns.append(column)
    return columns


def metric_sql_guidance_for(context: SchemaContext) -> list[str]:
    guidance: list[str] = []
    for metric in context.matched_metrics:
        for item in METRIC_SQL_GUIDANCE.get(metric.name, ()):
            if item not in guidance:
                guidance.append(item)
    return guidance


def http_error_message(exc: HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return exc.reason or "request rejected"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:500]
    return exc.reason or "request rejected"


def configured_llm_model() -> str:
    return os.getenv("IM_ONE_LLM_MODEL", DEFAULT_LLM_MODEL)


def configured_llm_base_url() -> str:
    return os.getenv("IM_ONE_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


def local_llm_no_auth_enabled(endpoint: str | None = None) -> bool:
    raw_flag = os.getenv("IM_ONE_LLM_ALLOW_LOCAL_NO_AUTH", "").strip().lower()
    if raw_flag not in LOCAL_NO_AUTH_VALUES:
        return False

    parsed = urlparse(endpoint or configured_llm_base_url())
    hostname = parsed.hostname or ""
    return hostname in LOCAL_LLM_HOSTS


def llm_endpoint_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY")) or local_llm_no_auth_enabled()


def llm_timeout_seconds() -> float:
    raw_value = os.getenv("IM_ONE_LLM_TIMEOUT", str(DEFAULT_LLM_TIMEOUT_SECONDS)).strip()
    try:
        timeout = float(raw_value)
    except ValueError as exc:
        raise LLMGenerationError("IM_ONE_LLM_TIMEOUT must be a numeric number of seconds.") from exc
    if timeout <= 0:
        raise LLMGenerationError("IM_ONE_LLM_TIMEOUT must be greater than 0 seconds.")
    return timeout
