from __future__ import annotations

from fastapi import APIRouter, Request

from ..auth import resolve_identity
from ..schemas import KnowledgeRequest


router = APIRouter(tags=["knowledge"])


@router.post("/api/v1/knowledge/query")
def knowledge_query(payload: KnowledgeRequest, request: Request) -> dict:
    identity = resolve_identity(request, payload.role, payload.branch_id)
    database = request.app.state.database
    session_id = database.ensure_session(payload.session_id, identity.user_id, identity.role, identity.branch_id, "knowledge")
    database.add_message(session_id, "user", payload.question, "knowledge")
    result = request.app.state.knowledge.query(payload.question)
    database.add_message(session_id, "assistant", result["answer"], "knowledge", result)
    return {"sessionId": session_id, **result}
