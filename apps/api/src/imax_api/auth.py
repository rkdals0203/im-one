from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status


@dataclass(frozen=True)
class Identity:
    user_id: str
    role: str
    branch_id: int
    auth_mode: str


def resolve_identity(request: Request, role: str, branch_id: int) -> Identity:
    mode = os.getenv("IM_ONE_AUTH_MODE", "none").strip().lower() or "none"
    expected_api_token = os.getenv("IM_ONE_API_TOKEN", "").strip()

    if mode == "trusted_headers":
        expected_proxy = os.getenv("IM_ONE_TRUSTED_PROXY_TOKEN", "").strip()
        supplied_proxy = request.headers.get("X-IM-One-Proxy-Token", "")
        if not expected_proxy or not secrets.compare_digest(supplied_proxy, expected_proxy):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다.")
        user_id = request.headers.get("X-IM-One-User", "internal-user").strip()[:120] or "internal-user"
        trusted_role = request.headers.get("X-IM-One-Role", role).strip() or role
        try:
            trusted_branch = int(request.headers.get("X-IM-One-Branch", str(branch_id)))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="지점 권한 헤더가 올바르지 않습니다.") from exc
        return Identity(user_id, trusted_role, trusted_branch, mode)

    if expected_api_token:
        bearer = request.headers.get("Authorization", "")
        supplied = bearer.removeprefix("Bearer ").strip() or request.headers.get("X-IM-One-Token", "")
        if not secrets.compare_digest(supplied, expected_api_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다.")
        return Identity("api-user", role, branch_id, "api_token")

    return Identity("local-demo", role, branch_id, "none")


def require_session_identity(request: Request, database: Any, session_id: str) -> tuple[Identity, dict[str, Any]]:
    session = database.session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="세션을 찾지 못했습니다.")
    identity = resolve_identity(request, session["role"], session["branchId"])
    if (
        identity.user_id != session["userId"]
        or identity.role != session["role"]
        or identity.branch_id != session["branchId"]
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="이 세션에 접근할 수 없습니다.")
    return identity, session
