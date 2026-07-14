from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from im_one_agent.sample_data import ensure_demo_database

from .config import get_settings
from .database import AppDatabase, SessionOwnershipError
from .expenses import ExpenseService
from .knowledge import KnowledgeService
from .llm import LLMClient
from .routers import assistant, data, expenses, health, knowledge, sessions
from .security import SECURITY_HEADERS
from .supervisor import build_supervisor


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    for path in (
        settings.app_db_path.parent,
        settings.checkpoint_db_path.parent,
        settings.data_db_path.parent,
        settings.audit_path.parent,
        settings.uploads_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    ensure_demo_database(settings.data_db_path)
    database = AppDatabase(settings.app_db_path, settings.expense_seed_path)
    database.initialize()
    llm = LLMClient()
    knowledge_service = KnowledgeService(settings.manual_dir, llm)
    expense_service = ExpenseService(database)

    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db_path)) as checkpointer:
        await checkpointer.setup()
        app.state.settings = settings
        app.state.database = database
        app.state.knowledge = knowledge_service
        app.state.expenses = expense_service
        app.state.supervisor = build_supervisor(
            settings,
            database,
            knowledge_service,
            expense_service,
            llm,
            checkpointer,
        )
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="iMAX Unified Agent API",
        version="0.2.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(SessionOwnershipError)
    async def session_ownership_error(request, exc: SessionOwnershipError):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @application.middleware("http")
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    application.include_router(health.router)
    application.include_router(assistant.router)
    application.include_router(sessions.router)
    application.include_router(knowledge.router)
    application.include_router(data.router)
    application.include_router(expenses.router)

    if settings.frontend_dist.exists():
        assets = settings.frontend_dist / "assets"
        if assets.exists():
            application.mount("/assets", StaticFiles(directory=assets), name="assets")

        @application.get("/{full_path:path}", include_in_schema=False)
        async def frontend(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not found")
            target = (settings.frontend_dist / full_path).resolve()
            if target.is_file() and settings.frontend_dist.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(settings.frontend_dist / "index.html")
    else:
        @application.get("/", include_in_schema=False)
        async def api_root():
            return JSONResponse({"name": "iMAX API", "docs": "/docs", "health": "/api/v1/health"})

    return application


app = create_app()


def run() -> None:
    uvicorn.run("imax_api.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
