from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from im_one_agent.web import build_catalog_payload, build_csv_document, build_report_draft, run_agent

from ..auth import require_session_identity, resolve_identity
from ..schemas import DataQueryRequest, ExportRequest


router = APIRouter(tags=["data"])


@router.post("/api/v1/data/query")
@router.post("/api/query", include_in_schema=False)
def data_query(payload: DataQueryRequest, request: Request) -> dict:
    identity = resolve_identity(request, payload.role, payload.branch_id)
    database = request.app.state.database
    session_id = database.ensure_session(payload.session_id, identity.user_id, identity.role, identity.branch_id, "data")
    previous = database.latest_payload(session_id, "data") or {}
    context = payload.conversation_context or previous.get("conversationContext", {})
    database.add_message(session_id, "user", payload.question, "data")
    result = run_agent(
        payload.question,
        identity.role,
        identity.branch_id,
        context,
        str(request.app.state.settings.data_db_path),
        str(request.app.state.settings.audit_path),
        user_id=identity.user_id,
        request_auth_mode=identity.auth_mode,
    )
    wrapped = {"kind": "data", **result, "sessionId": session_id}
    database.add_message(session_id, "assistant", result.get("answer", "데이터 조회를 완료했습니다."), "data", wrapped)
    return wrapped


@router.post("/api/v1/data/export")
@router.post("/api/export", include_in_schema=False)
def data_export(payload: ExportRequest, request: Request) -> Response:
    require_session_identity(request, request.app.state.database, payload.session_id)
    result = request.app.state.database.latest_payload(payload.session_id, "data")
    if result is None:
        raise HTTPException(status_code=404, detail="내보낼 데이터 결과가 없습니다.")
    if payload.export_type == "report":
        content = build_report_draft(result)
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="imax-report.md"'},
        )
    content = build_csv_document(result.get("columns", []), result.get("rows", []))
    return Response(
        content="\ufeff" + content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="imax-results.csv"'},
    )


@router.get("/api/v1/data/catalog")
@router.get("/api/catalog", include_in_schema=False)
def data_catalog(request: Request, role: str = "branch_manager") -> dict:
    resolve_identity(request, role, 1)
    return build_catalog_payload(role=role)
