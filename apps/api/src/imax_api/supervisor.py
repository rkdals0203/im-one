from __future__ import annotations

import os
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from im_one_agent.web import run_agent

from .config import Settings
from .database import AppDatabase
from .expenses import ExpenseService
from .knowledge import KnowledgeService
from .llm import LLMClient, LLMUnavailable


Workspace = Literal["knowledge", "data", "expense", "clarification"]


class SupervisorState(TypedDict, total=False):
    session_id: str
    message: str
    role: str
    branch_id: int
    user_id: str
    auth_mode: str
    workspace_hint: str | None
    route: Workspace
    route_confidence: float
    route_reason: str
    payload: dict[str, Any]
    answer: str


KNOWLEDGE_WORDS = {"매뉴얼", "업무지식", "채권", "회의실", "예약", "사용법", "화면번호", "tr"}
DATA_WORDS = {"데이터", "조회", "지점", "계좌", "상품", "voc", "추이", "건수", "sql", "els", "민원", "실적"}
EXPENSE_WORDS = {"품의", "결의", "법인카드", "승인", "반려", "예산", "영수증", "지출", "결제"}


def _keyword_route(message: str) -> tuple[Workspace, float, str]:
    lowered = message.lower()
    scores = {
        "knowledge": sum(word in lowered for word in KNOWLEDGE_WORDS),
        "data": sum(word in lowered for word in DATA_WORDS),
        "expense": sum(word in lowered for word in EXPENSE_WORDS),
    }
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if ranked[0][1] == 0 or (ranked[1][1] > 0 and ranked[0][1] == ranked[1][1]):
        return "clarification", 0.4, "업무 영역을 하나로 확정하기 어렵습니다."
    return ranked[0][0], min(0.65 + ranked[0][1] * 0.1, 0.95), "업무 용어와 요청 대상을 기준으로 분류했습니다."


def _llm_route(llm: LLMClient, message: str) -> tuple[Workspace, float, str]:
    payload, _ = llm.complete_json(
        [
            {
                "role": "system",
                "content": (
                    "당신은 iMAX 업무 라우터입니다. 사용자의 요청을 knowledge, data, expense, clarification 중 "
                    "하나로 분류하세요. knowledge는 사내 매뉴얼/업무 절차, data는 통계/NL2SQL/실적 조회, "
                    "expense는 법인카드/품의/승인/예산입니다. 두 업무가 섞였거나 불명확하면 clarification입니다. "
                    "JSON으로 route, confidence(0~1), reason만 반환하세요."
                ),
            },
            {"role": "user", "content": message},
        ]
    )
    route = str(payload.get("route", "clarification")).strip().lower()
    if route not in {"knowledge", "data", "expense", "clarification"}:
        route = "clarification"
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < 0.62:
        route = "clarification"
    return route, max(0.0, min(confidence, 1.0)), str(payload.get("reason", "LLM 업무 분류"))[:240]


def build_supervisor(
    settings: Settings,
    database: AppDatabase,
    knowledge: KnowledgeService,
    expenses: ExpenseService,
    llm: LLMClient,
    checkpointer: Any | None = None,
):
    def normalize_node(state: SupervisorState) -> SupervisorState:
        return {
            "message": str(state.get("message", "")).strip(),
            "role": str(state.get("role", "branch_manager")).strip() or "branch_manager",
            "branch_id": max(int(state.get("branch_id", 1)), 1),
        }

    def classify_node(state: SupervisorState) -> SupervisorState:
        hint = state.get("workspace_hint")
        if hint in {"knowledge", "data", "expense"}:
            route: Workspace = hint  # type: ignore[assignment]
            return {"route": route, "route_confidence": 1.0, "route_reason": "현재 업무 화면을 유지했습니다."}
        disable_llm = os.getenv("IMAX_DISABLE_LLM_ROUTER", "").strip().lower() in {"1", "true", "yes"}
        if not disable_llm and llm.configured():
            try:
                route, confidence, reason = _llm_route(llm, state["message"])
                return {"route": route, "route_confidence": confidence, "route_reason": reason}
            except LLMUnavailable:
                pass
        route, confidence, reason = _keyword_route(state["message"])
        return {"route": route, "route_confidence": confidence, "route_reason": reason}

    def knowledge_agent(state: SupervisorState) -> SupervisorState:
        payload = knowledge.query(state["message"])
        return {"payload": payload, "answer": payload["answer"]}

    def data_agent(state: SupervisorState) -> SupervisorState:
        previous = database.latest_payload(state["session_id"], "data") or {}
        context = previous.get("conversationContext", {}) if isinstance(previous, dict) else {}
        result = run_agent(
            state["message"],
            state["role"],
            state["branch_id"],
            context,
            str(settings.data_db_path),
            str(settings.audit_path),
            user_id=state.get("user_id", "local-demo"),
            request_auth_mode=state.get("auth_mode", "none"),
        )
        payload = {"kind": "data", **result}
        return {"payload": payload, "answer": result.get("answer", "데이터 조회를 완료했습니다.")}

    def expense_agent(state: SupervisorState) -> SupervisorState:
        payload = expenses.handle_message(state["session_id"], state["message"])
        return {"payload": payload, "answer": payload["message"]}

    def clarification_node(state: SupervisorState) -> SupervisorState:
        payload = {
            "kind": "clarification",
            "message": "어떤 업무를 도와드릴까요?",
            "options": [
                {"workspace": "knowledge", "label": "업무 매뉴얼 찾기"},
                {"workspace": "data", "label": "데이터 조회하기"},
                {"workspace": "expense", "label": "지출품의 처리하기"},
            ],
        }
        return {"payload": payload, "answer": payload["message"]}

    def route_after_classification(state: SupervisorState) -> Workspace:
        return state.get("route", "clarification")

    def specialist_subgraph(handler):
        subgraph = StateGraph(SupervisorState)
        subgraph.add_node("execute", handler)
        subgraph.add_edge(START, "execute")
        subgraph.add_edge("execute", END)
        return subgraph.compile()

    graph = StateGraph(SupervisorState)
    graph.add_node("normalize", normalize_node)
    graph.add_node("classify", classify_node)
    graph.add_node("knowledge_agent", specialist_subgraph(knowledge_agent))
    graph.add_node("data_agent", specialist_subgraph(data_agent))
    graph.add_node("expense_agent", specialist_subgraph(expense_agent))
    graph.add_node("clarification", clarification_node)
    graph.add_edge(START, "normalize")
    graph.add_edge("normalize", "classify")
    graph.add_conditional_edges(
        "classify",
        route_after_classification,
        {
            "knowledge": "knowledge_agent",
            "data": "data_agent",
            "expense": "expense_agent",
            "clarification": "clarification",
        },
    )
    for node in ("knowledge_agent", "data_agent", "expense_agent", "clarification"):
        graph.add_edge(node, END)
    return graph.compile(checkpointer=checkpointer)
