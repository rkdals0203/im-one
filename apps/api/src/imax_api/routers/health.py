from __future__ import annotations

import os

from fastapi import APIRouter, Request


router = APIRouter(tags=["health"])


@router.get("/api/v1/health")
@router.get("/api/health", include_in_schema=False)
def health(request: Request) -> dict:
    return {
        "status": "ok",
        "runtime": "FastAPI + LangGraph",
        "model": os.getenv("IM_ONE_LLM_MODEL", "not-configured"),
    }
