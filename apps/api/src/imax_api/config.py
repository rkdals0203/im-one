from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from im_one_agent.env import load_project_env


REPO_ROOT = Path(__file__).resolve().parents[4]
API_ROOT = REPO_ROOT / "apps" / "api"


def _path_from_env(name: str, default: str) -> Path:
    raw = os.getenv(name, default)
    path = Path(raw).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    app_db_path: Path
    checkpoint_db_path: Path
    data_db_path: Path
    audit_path: Path
    feedback_path: Path
    uploads_dir: Path
    frontend_dist: Path
    expense_seed_path: Path
    manual_dir: Path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_project_env()
    return Settings(
        repo_root=REPO_ROOT,
        app_db_path=_path_from_env("IMAX_APP_DB", "data/imax_app.sqlite"),
        checkpoint_db_path=_path_from_env("IMAX_CHECKPOINT_DB", "data/langgraph_checkpoints.sqlite"),
        data_db_path=_path_from_env("IM_ONE_DB_PATH", "data/im_one_demo.sqlite"),
        audit_path=_path_from_env("IM_ONE_AUDIT_PATH", "logs/audit.jsonl"),
        feedback_path=_path_from_env("IM_ONE_FEEDBACK_PATH", "logs/feedback.jsonl"),
        uploads_dir=_path_from_env("IMAX_UPLOADS_DIR", "uploads"),
        frontend_dist=REPO_ROOT / "apps" / "web" / "dist",
        expense_seed_path=API_ROOT / "resources" / "expense_seed.json",
        manual_dir=API_ROOT / "resources" / "manual",
    )
