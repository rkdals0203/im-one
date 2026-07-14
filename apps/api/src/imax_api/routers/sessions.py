from __future__ import annotations

from fastapi import APIRouter, Request

from ..auth import require_session_identity


router = APIRouter(tags=["sessions"])


@router.get("/api/v1/sessions/{session_id}")
def get_session(session_id: str, request: Request) -> dict:
    _, session = require_session_identity(request, request.app.state.database, session_id)
    return session
