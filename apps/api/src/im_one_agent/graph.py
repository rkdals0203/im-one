from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from typing_extensions import TypedDict

from im_one_agent.database_backend import execution_backend_for_name
from im_one_agent.domain import normalize_branch_id, normalize_question_text, normalize_user_role
from im_one_agent.intent_guard import guard_question_intent
from im_one_agent.response import build_blocked_answer, build_explanation, format_rows
from im_one_agent.sample_data import connect_database, is_read_only_database
from im_one_agent.schema_retrieval import SchemaContext, extend_schema_with_follow_up_context, retrieve_schema
from im_one_agent.sql_generator import GeneratedSQL, LLMGenerationError, configured_llm_model, generate_sql
from im_one_agent.sql_safety import ValidationResult, apply_branch_scope_filter, validate_sql

DEFAULT_QUERY_TIMEOUT_MS = 10000

try:
    from langgraph.graph import END, START, StateGraph
except ModuleNotFoundError:  # pragma: no cover
    END = START = StateGraph = None


class AgentState(TypedDict, total=False):
    question: str
    user_id: str
    auth_mode: str
    user_role: str
    branch_id: int
    conversation_context: dict[str, object]
    db_path: str
    audit_path: str
    context: SchemaContext
    generated: GeneratedSQL
    llm_generated_sql: str
    policy_applied_sql: str
    sql_policy_transformations: list[str]
    validation: ValidationResult
    columns: list[str]
    column_metadata: list[dict[str, object]]
    rows: list[dict[str, object]]
    query_plan_summary: list[str]
    pre_execution_row_count: int | None
    pre_execution_row_count_status: str
    pre_execution_check_ms: float
    execution_ms: float
    answer: str
    explanation: str
    database_audit_status: str
    database_audit_error: str | None


def query_timeout_ms() -> int:
    raw_value = os.getenv("IM_ONE_QUERY_TIMEOUT_MS", str(DEFAULT_QUERY_TIMEOUT_MS)).strip()
    try:
        timeout = int(raw_value)
    except ValueError:
        return DEFAULT_QUERY_TIMEOUT_MS
    return max(timeout, 0)


def question_intake_node(state: AgentState) -> AgentState:
    return {
        "question": normalize_question_text(state.get("question", "")),
        "user_role": normalize_user_role(state.get("user_role", "branch_manager")),
        "branch_id": normalize_branch_id(state.get("branch_id", 1)),
        "user_id": str(state.get("user_id", "local-demo")).strip() or "local-demo",
        "auth_mode": str(state.get("auth_mode", "none")).strip() or "none",
    }


def retrieve_schema_node(state: AgentState) -> AgentState:
    user_role = normalize_user_role(state.get("user_role", "branch_manager"))
    branch_id = normalize_branch_id(state.get("branch_id", 1))
    context = retrieve_schema(
        state["question"],
        user_role=user_role,
    )
    context = extend_schema_with_follow_up_context(
        state["question"],
        context,
        state.get("conversation_context", {}),
        user_role=user_role,
    )
    if user_role == "branch_manager":
        context = replace(
            context,
            business_rules=context.business_rules
            + (f"branch_manager 권한은 branch_id = {branch_id} 범위 조건을 반드시 적용합니다.",),
        )
    return {
        "context": context,
        "branch_id": branch_id,
        "user_role": user_role,
    }


def generate_sql_node(state: AgentState) -> AgentState:
    guard_result = guard_question_intent(state["question"])
    if not guard_result.allowed:
        return {
            "generated": GeneratedSQL(
                sql="",
                reason="질문 의도 검증 단계에서 차단되었습니다.",
                engine="intent_guard",
                error="; ".join(guard_result.issues),
            )
        }

    try:
        generated = generate_sql(
            state["question"],
            state["context"],
            user_role=state.get("user_role", "branch_manager"),
            branch_id=int(state.get("branch_id", 1)),
            conversation_context=state.get("conversation_context", {}),
        )
    except LLMGenerationError as exc:
        generated = GeneratedSQL(
            sql="",
            reason="LLM SQL 생성 단계에서 실패했습니다.",
            engine="llm",
            error=str(exc),
            model=configured_llm_model(),
        )
    if not generated.error:
        generated = append_retrieval_assumption(generated, state["context"])
    return {
        "generated": generated,
        "llm_generated_sql": generated.sql,
        "policy_applied_sql": generated.sql,
        "sql_policy_transformations": [],
    }


def append_retrieval_assumption(generated: GeneratedSQL, context: SchemaContext) -> GeneratedSQL:
    assumption = retrieval_assumption(context)
    if not assumption or assumption in generated.assumptions:
        return generated
    return replace(generated, assumptions=generated.assumptions + (assumption,))


def retrieval_assumption(context: SchemaContext) -> str:
    if context.retrieval_confidence != "low" or not context.clarification_options:
        return ""
    metric_names = ", ".join(metric.name for metric in context.matched_metrics[:2]) or "선택된 지표"
    return f"질문이 모호해 {metric_names} 기준으로 우선 해석했습니다. 필요하면 확인 질문을 선택하세요."


def validate_sql_node(state: AgentState) -> AgentState:
    if state["generated"].error:
        issue_prefix = "위험 요청 차단" if state["generated"].engine == "intent_guard" else "LLM SQL 생성 실패"
        return {
            "llm_generated_sql": state.get("llm_generated_sql", state["generated"].sql),
            "policy_applied_sql": state.get("policy_applied_sql", state["generated"].sql),
            "sql_policy_transformations": state.get("sql_policy_transformations", []),
            "validation": ValidationResult(
                allowed=False,
                sql="",
                issues=(f"{issue_prefix}: {state['generated'].error}",),
                referenced_tables=(),
            )
        }

    generated = state["generated"]
    llm_generated_sql = state.get("llm_generated_sql", generated.sql)
    sql_policy_transformations = list(state.get("sql_policy_transformations", []))
    branch_scope_branch_id = (
        int(state.get("branch_id", 1))
        if state.get("user_role", "branch_manager") == "branch_manager"
        else None
    )
    if branch_scope_branch_id is not None:
        scoped_sql = apply_branch_scope_filter(generated.sql, branch_scope_branch_id)
        if scoped_sql != generated.sql:
            generated = replace(
                generated,
                sql=scoped_sql,
                assumptions=generated.assumptions
                + ("branch_manager 권한에 맞춰 branch_id 범위 조건을 적용했습니다.",),
            )
            sql_policy_transformations.append("branch_scope_filter_applied")

    connection = connect_database(state["db_path"])
    try:
        validation = validate_sql(
            generated.sql,
            allowed_tables=state["context"].allowed_table_names,
            connection=connection,
            branch_scope_branch_id=branch_scope_branch_id,
        )
    finally:
        connection.close()
    return {
        "generated": generated,
        "llm_generated_sql": llm_generated_sql,
        "policy_applied_sql": generated.sql,
        "sql_policy_transformations": sql_policy_transformations,
        "validation": validation,
    }


def route_after_validation(state: AgentState) -> Literal["execute_sql", "explain_blocked"]:
    if state["validation"].allowed:
        return "execute_sql"
    return "explain_blocked"


def route_after_execution(state: AgentState) -> Literal["explain_result", "explain_blocked"]:
    if state["validation"].allowed:
        return "explain_result"
    return "explain_blocked"


def execute_sql_node(state: AgentState) -> AgentState:
    timeout_ms = query_timeout_ms()
    context = state.get("context")
    allowed_tables = context.allowed_table_names if context is not None else None
    try:
        execution_result = execution_backend_for_name().execute_validated_sql(
            db_path=state["db_path"],
            sql=state["validation"].sql,
            timeout_ms=timeout_ms,
            allowed_tables=allowed_tables,
        )
    except ValueError as exc:
        validation = ValidationResult(
            allowed=False,
            sql=state["validation"].sql,
            issues=(f"DB 실행 backend 오류: {exc}",),
            referenced_tables=state["validation"].referenced_tables,
        )
        return {
            "rows": [],
            "columns": [],
            "column_metadata": [],
            "query_plan_summary": [],
            "pre_execution_row_count": None,
            "pre_execution_row_count_status": "not_checked",
            "pre_execution_check_ms": 0.0,
            "validation": validation,
            "execution_ms": 0.0,
        }

    state_update: AgentState = {
        "rows": execution_result.rows,
        "columns": execution_result.columns,
        "column_metadata": execution_result.column_metadata,
        "query_plan_summary": execution_result.query_plan_summary,
        "pre_execution_row_count": execution_result.pre_execution_row_count,
        "pre_execution_row_count_status": execution_result.pre_execution_row_count_status,
        "pre_execution_check_ms": execution_result.pre_execution_check_ms,
        "execution_ms": execution_result.execution_ms,
    }
    if execution_result.error_issue:
        state_update["validation"] = ValidationResult(
            allowed=False,
            sql=state["validation"].sql,
            issues=(execution_result.error_issue,),
            referenced_tables=state["validation"].referenced_tables,
        )
    return state_update


def explain_result_node(state: AgentState) -> AgentState:
    explanation = build_explanation(
        question=state["question"],
        context=state["context"],
        validation=state["validation"],
        row_count=len(state.get("rows", [])),
        generation_reason=state["generated"].reason,
        generation_assumptions=state["generated"].assumptions,
    )
    answer = "\n\n".join(
        [
            "조회 결과",
            format_rows(state.get("columns", []), state.get("rows", [])),
            "설명",
            explanation,
            "SQL",
            state["validation"].sql,
        ]
    )
    return {"answer": answer, "explanation": explanation}


def explain_blocked_node(state: AgentState) -> AgentState:
    answer = build_blocked_answer(state["question"], state["validation"])
    return {"answer": answer, "explanation": answer, "rows": [], "columns": []}


def validation_status_for(state: AgentState) -> str:
    return "passed" if state["validation"].allowed else "blocked"


def has_execution_failure(state: AgentState) -> bool:
    if state.get("execution_ms") is None:
        return False
    return any(
        issue.startswith("SQL 실행 오류")
        or issue.startswith("SQL 실행 시간 제한 초과")
        or issue.startswith("DB 실행 backend 오류")
        for issue in state["validation"].issues
    )


def execution_status_for(state: AgentState) -> str:
    if state["validation"].allowed:
        return "executed"
    if has_execution_failure(state):
        return "failed"
    return "blocked"


def blocked_reason_for(state: AgentState) -> str | None:
    return "; ".join(state["validation"].issues) if state["validation"].issues else None


def write_audit_node(state: AgentState) -> AgentState:
    audit_path = Path(state.get("audit_path", "logs/audit.jsonl"))
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).isoformat()
    selected_semantic_metrics = [metric.name for metric in state["context"].matched_metrics]
    validation_status = validation_status_for(state)
    execution_status = execution_status_for(state)
    blocked_reason = blocked_reason_for(state)
    database_audit_status, database_audit_error = write_database_audit(state, created_at)
    event = {
        "timestamp": created_at,
        "created_at": created_at,
        "user_id": state.get("user_id", "local-demo"),
        "auth_mode": state.get("auth_mode", "none"),
        "original_question": state["question"],
        "question": state["question"],
        "user_role": state.get("user_role", "branch_manager"),
        "branch_id": int(state.get("branch_id", 1)),
        "allowed": state["validation"].allowed,
        "issues": list(state["validation"].issues),
        "generated_sql": state.get("llm_generated_sql", state["generated"].sql),
        "llm_generated_sql": state.get("llm_generated_sql", state["generated"].sql),
        "policy_applied_sql": state.get("policy_applied_sql", state["validation"].sql),
        "validated_sql": state["validation"].sql,
        "sql_policy_transformations": list(state.get("sql_policy_transformations", [])),
        "sql": state["validation"].sql,
        "selected_semantic_metrics": selected_semantic_metrics,
        "semantic_metrics": selected_semantic_metrics,
        "generation_engine": state["generated"].engine,
        "llm_model": state["generated"].model,
        "prompt_version": state["generated"].prompt_version,
        "validation_status": validation_status,
        "execution_status": execution_status,
        "referenced_tables": list(state["validation"].referenced_tables),
        "row_count": len(state.get("rows", [])),
        "pre_execution_row_count": state.get("pre_execution_row_count"),
        "pre_execution_row_count_status": state.get("pre_execution_row_count_status"),
        "pre_execution_check_ms": state.get("pre_execution_check_ms"),
        "query_plan_summary": state.get("query_plan_summary", []),
        "blocked_reason": blocked_reason,
        "execution_ms": state.get("execution_ms"),
        "database_audit_status": database_audit_status,
        "database_audit_error": database_audit_error,
    }
    with audit_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")

    return {
        "database_audit_status": database_audit_status,
        "database_audit_error": database_audit_error,
    }


def write_database_audit(state: AgentState, created_at: str) -> tuple[str, str | None]:
    if is_read_only_database():
        return "skipped_read_only", None

    connection = connect_database(state["db_path"])
    try:
        connection.execute(
            """
            INSERT INTO query_audit_log (
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
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                state.get("user_id", "local-demo"),
                state.get("auth_mode", "none"),
                state.get("user_role", "branch_manager"),
                state["question"],
                state["question"],
                ",".join(metric.name for metric in state["context"].matched_metrics),
                ",".join(metric.name for metric in state["context"].matched_metrics),
                state.get("llm_generated_sql", state["generated"].sql),
                state.get("llm_generated_sql", state["generated"].sql),
                state.get("policy_applied_sql", state["validation"].sql),
                state["validation"].sql,
                json.dumps(list(state.get("sql_policy_transformations", [])), ensure_ascii=False),
                state["generated"].engine,
                state["generated"].model,
                state["generated"].prompt_version,
                validation_status_for(state),
                execution_status_for(state),
                json.dumps(list(state["validation"].issues), ensure_ascii=False),
                json.dumps(list(state["validation"].referenced_tables), ensure_ascii=False),
                len(state.get("rows", [])),
                state.get("pre_execution_row_count"),
                state.get("pre_execution_row_count_status", ""),
                state.get("pre_execution_check_ms"),
                json.dumps(state.get("query_plan_summary", []), ensure_ascii=False),
                state.get("execution_ms"),
                blocked_reason_for(state),
            ),
        )
        connection.commit()
    except sqlite3.Error as exc:
        return "failed", str(exc)
    finally:
        connection.close()
    return "recorded", None


class SequentialAgent:
    """Sequential runner with the same node order as the LangGraph workflow."""

    def invoke(self, initial_state: AgentState) -> AgentState:
        state: AgentState = dict(initial_state)
        state.update(question_intake_node(state))
        state.update(retrieve_schema_node(state))
        state.update(generate_sql_node(state))
        state.update(validate_sql_node(state))

        if route_after_validation(state) == "execute_sql":
            state.update(execute_sql_node(state))
            if route_after_execution(state) == "explain_result":
                state.update(explain_result_node(state))
            else:
                state.update(explain_blocked_node(state))
        else:
            state.update(explain_blocked_node(state))

        state.update(write_audit_node(state))
        return state


def build_agent():
    if StateGraph is None:
        return SequentialAgent()

    graph = StateGraph(AgentState)
    graph.add_node("question_intake", question_intake_node)
    graph.add_node("retrieve_schema", retrieve_schema_node)
    graph.add_node("generate_sql", generate_sql_node)
    graph.add_node("validate_sql", validate_sql_node)
    graph.add_node("execute_sql", execute_sql_node)
    graph.add_node("explain_result", explain_result_node)
    graph.add_node("explain_blocked", explain_blocked_node)
    graph.add_node("write_audit", write_audit_node)

    graph.add_edge(START, "question_intake")
    graph.add_edge("question_intake", "retrieve_schema")
    graph.add_edge("retrieve_schema", "generate_sql")
    graph.add_edge("generate_sql", "validate_sql")
    graph.add_conditional_edges(
        "validate_sql",
        route_after_validation,
        {
            "execute_sql": "execute_sql",
            "explain_blocked": "explain_blocked",
        },
    )
    graph.add_conditional_edges(
        "execute_sql",
        route_after_execution,
        {
            "explain_result": "explain_result",
            "explain_blocked": "explain_blocked",
        },
    )
    graph.add_edge("explain_result", "write_audit")
    graph.add_edge("explain_blocked", "write_audit")
    graph.add_edge("write_audit", END)

    return graph.compile()
