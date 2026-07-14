from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import resolve_identity
from ..schemas import AssistantRequest, AssistantResult


router = APIRouter(tags=["assistant"])


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post(
    "/api/v1/assistant/messages",
    responses={
        200: {
            "model": AssistantResult,
            "description": "stage, route, clarification, result, error 이벤트를 전달하는 SSE 스트림",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def assistant_message(payload: AssistantRequest, request: Request) -> StreamingResponse:
    identity = resolve_identity(request, payload.role, payload.branch_id)
    database = request.app.state.database
    session_id = database.ensure_session(
        payload.session_id,
        identity.user_id,
        identity.role,
        identity.branch_id,
        payload.workspace_hint,
    )
    user_message_id = database.add_message(session_id, "user", payload.message, payload.workspace_hint)

    async def stream() -> AsyncIterator[str]:
        state: dict[str, Any] = {
            "session_id": session_id,
            "message": payload.message,
            "role": identity.role,
            "branch_id": identity.branch_id,
            "user_id": identity.user_id,
            "auth_mode": identity.auth_mode,
            "workspace_hint": payload.workspace_hint,
        }
        yield _sse(
            "stage",
            {"node": "intake", "label": "질문을 확인하고 있습니다", "status": "completed", "sessionId": session_id},
        )
        try:
            async for update in request.app.state.supervisor.astream(
                state,
                config={"configurable": {"thread_id": session_id}},
                stream_mode="updates",
            ):
                if not isinstance(update, dict):
                    continue
                for node, delta in update.items():
                    if not isinstance(delta, dict):
                        continue
                    state.update(delta)
                    if node == "classify":
                        yield _sse(
                            "route",
                            {
                                "workspace": state.get("route"),
                                "confidence": state.get("route_confidence"),
                                "reason": state.get("route_reason"),
                            },
                        )
                    yield _sse(
                        "stage",
                        {"node": node, "label": _stage_label(node), "status": "completed"},
                    )

            result_payload = state.get("payload")
            if not isinstance(result_payload, dict):
                raise RuntimeError("에이전트 결과가 비어 있습니다.")
            workspace = str(state.get("route", result_payload.get("kind", "clarification")))
            answer = str(state.get("answer", result_payload.get("message", "요청을 처리했습니다.")))
            if result_payload.get("kind") == "clarification":
                yield _sse(
                    "clarification",
                    {"sessionId": session_id, "message": answer, "payload": result_payload},
                )
            assistant_message_id = database.add_message(
                session_id,
                "assistant",
                answer,
                workspace,
                result_payload,
            )
            yield _sse(
                "result",
                {
                    "sessionId": session_id,
                    "userMessageId": user_message_id,
                    "messageId": assistant_message_id,
                    "workspace": workspace,
                    "answer": answer,
                    "payload": result_payload,
                },
            )
        except Exception as exc:
            yield _sse(
                "error",
                {"code": "agent_execution_failed", "message": str(exc), "retryable": True, "sessionId": session_id},
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _stage_label(node: str) -> str:
    return {
        "normalize": "질문을 정리했습니다",
        "classify": "담당 업무를 선택했습니다",
        "knowledge_agent": "매뉴얼 근거를 확인했습니다",
        "data_agent": "데이터를 검증하고 조회했습니다",
        "expense_agent": "지출업무 규칙을 적용했습니다",
        "clarification": "추가 선택이 필요합니다",
    }.get(node, "처리 단계를 완료했습니다")
