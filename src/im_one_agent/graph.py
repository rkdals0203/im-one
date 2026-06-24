from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal

from typing_extensions import TypedDict

from im_one_agent.response import build_blocked_answer, build_explanation, format_rows
from im_one_agent.sample_data import connect_database
from im_one_agent.schema_retrieval import SchemaContext, retrieve_schema
from im_one_agent.sql_generator import GeneratedSQL, generate_sql
from im_one_agent.sql_safety import ValidationResult, validate_sql

try:
    from langgraph.graph import END, START, StateGraph
except ModuleNotFoundError:  # pragma: no cover - exercised only in offline local environments
    END = START = StateGraph = None


class AgentState(TypedDict, total=False):
    question: str
    user_role: str
    db_path: str
    audit_path: str
    context: SchemaContext
    generated: GeneratedSQL
    validation: ValidationResult
    columns: list[str]
    rows: list[dict[str, object]]
    answer: str
    explanation: str


def retrieve_schema_node(state: AgentState) -> AgentState:
    return {
        "context": retrieve_schema(
            state["question"],
            user_role=state.get("user_role", "branch_manager"),
        )
    }


def generate_sql_node(state: AgentState) -> AgentState:
    return {"generated": generate_sql(state["question"], state["context"])}


def validate_sql_node(state: AgentState) -> AgentState:
    connection = connect_database(state["db_path"])
    try:
        validation = validate_sql(
            state["generated"].sql,
            allowed_tables=state["context"].allowed_table_names,
            connection=connection,
        )
    finally:
        connection.close()
    return {"validation": validation}


def route_after_validation(state: AgentState) -> Literal["execute_sql", "explain_blocked"]:
    if state["validation"].allowed:
        return "execute_sql"
    return "explain_blocked"


def execute_sql_node(state: AgentState) -> AgentState:
    connection = connect_database(state["db_path"])
    try:
        cursor = connection.execute(state["validation"].sql)
        rows = [dict(row) for row in cursor.fetchall()]
        columns = [description[0] for description in cursor.description or []]
    except sqlite3.Error as exc:
        rows = []
        columns = []
        validation = ValidationResult(
            allowed=False,
            sql=state["validation"].sql,
            issues=(f"SQL 실행 오류: {exc}",),
            referenced_tables=state["validation"].referenced_tables,
        )
        return {"rows": rows, "columns": columns, "validation": validation}
    finally:
        connection.close()

    return {"rows": rows, "columns": columns}


def explain_result_node(state: AgentState) -> AgentState:
    explanation = build_explanation(
        question=state["question"],
        context=state["context"],
        validation=state["validation"],
        row_count=len(state.get("rows", [])),
        generation_reason=state["generated"].reason,
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


def write_audit_node(state: AgentState) -> AgentState:
    audit_path = Path(state.get("audit_path", "logs/audit.jsonl"))
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "question": state["question"],
        "user_role": state.get("user_role", "branch_manager"),
        "allowed": state["validation"].allowed,
        "issues": list(state["validation"].issues),
        "sql": state["validation"].sql,
        "referenced_tables": list(state["validation"].referenced_tables),
        "row_count": len(state.get("rows", [])),
    }
    with audit_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")
    return {}


class SequentialAgent:
    """Offline fallback that preserves the same node order when LangGraph is unavailable."""

    def invoke(self, initial_state: AgentState) -> AgentState:
        state: AgentState = dict(initial_state)
        state.update(retrieve_schema_node(state))
        state.update(generate_sql_node(state))
        state.update(validate_sql_node(state))

        if route_after_validation(state) == "execute_sql":
            state.update(execute_sql_node(state))
            state.update(explain_result_node(state))
        else:
            state.update(explain_blocked_node(state))

        state.update(write_audit_node(state))
        return state


def build_agent():
    if StateGraph is None:
        return SequentialAgent()

    graph = StateGraph(AgentState)
    graph.add_node("retrieve_schema", retrieve_schema_node)
    graph.add_node("generate_sql", generate_sql_node)
    graph.add_node("validate_sql", validate_sql_node)
    graph.add_node("execute_sql", execute_sql_node)
    graph.add_node("explain_result", explain_result_node)
    graph.add_node("explain_blocked", explain_blocked_node)
    graph.add_node("write_audit", write_audit_node)

    graph.add_edge(START, "retrieve_schema")
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
    graph.add_edge("execute_sql", "explain_result")
    graph.add_edge("explain_result", "write_audit")
    graph.add_edge("explain_blocked", "write_audit")
    graph.add_edge("write_audit", END)

    return graph.compile()
