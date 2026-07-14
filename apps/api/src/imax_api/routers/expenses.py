from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..auth import require_session_identity, resolve_identity
from ..expenses import ExpenseError
from ..schemas import ExpenseActionRequest


router = APIRouter(tags=["expenses"])


@router.get("/api/v1/expenses/overview")
def expense_overview(
    request: Request,
    sessionId: str | None = None,
    role: str = "branch_manager",
    branchId: int = 1,
) -> dict:
    identity = resolve_identity(request, role, branchId)
    session_id = request.app.state.database.ensure_session(
        sessionId, identity.user_id, identity.role, identity.branch_id, "expense"
    )
    return {"sessionId": session_id, "kind": "expense", "overview": request.app.state.expenses.overview(session_id)}


@router.post("/api/v1/expenses/actions")
def expense_action(payload: ExpenseActionRequest, request: Request) -> dict:
    require_session_identity(request, request.app.state.database, payload.session_id)
    try:
        return request.app.state.expenses.perform_action(
            payload.session_id,
            payload.action,
            payload.confirmation_token,
            payload.idempotency_key,
        )
    except ExpenseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/expenses/evidence")
async def expense_evidence(
    request: Request,
    sessionId: str = Form(...),
    confirmationToken: str = Form(...),
    role: str = Form("branch_manager"),
    branchId: int = Form(1),
    file: UploadFile = File(...),
) -> dict:
    require_session_identity(request, request.app.state.database, sessionId)
    filename = file.filename or "evidence.pdf"
    if file.content_type != "application/pdf" and not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 첨부할 수 있습니다.")
    content = await file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="첨부 파일은 5MB 이하여야 합니다.")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="올바른 PDF 파일이 아닙니다.")
    safe_session = re.sub(r"[^A-Za-z0-9_-]", "_", sessionId)[:80]
    target_dir: Path = request.app.state.settings.uploads_dir / safe_session
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid.uuid4().hex}.pdf"
    target.write_bytes(content)
    try:
        return request.app.state.expenses.attach_evidence(sessionId, confirmationToken, target)
    except ExpenseError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
