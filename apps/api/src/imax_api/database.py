from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionOwnershipError(PermissionError):
    pass


class AppDatabase:
    def __init__(self, path: Path, expense_seed_path: Path) -> None:
        self.path = path
        self.expense_seed_path = expense_seed_path
        self._lock = threading.RLock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._lock, self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    user_role TEXT NOT NULL,
                    branch_id INTEGER NOT NULL,
                    workspace TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS app_messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES app_sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    workspace TEXT,
                    payload_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_app_messages_session
                    ON app_messages(session_id, created_at);

                CREATE TABLE IF NOT EXISTS expense_items (
                    id INTEGER PRIMARY KEY,
                    dept TEXT NOT NULL,
                    document_date TEXT NOT NULL,
                    title TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    account TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_usage_keys_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS expense_pending_actions (
                    session_id TEXT PRIMARY KEY REFERENCES app_sessions(id) ON DELETE CASCADE,
                    token TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS expense_action_results (
                    idempotency_key TEXT PRIMARY KEY,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            count = connection.execute("SELECT COUNT(*) FROM expense_items").fetchone()[0]
            if count == 0:
                self._seed_expenses(connection)

    def _seed_expenses(self, connection: sqlite3.Connection) -> None:
        if not self.expense_seed_path.exists():
            return
        payload = json.loads(self.expense_seed_path.read_text(encoding="utf-8"))
        for item in payload.get("dataset", []):
            connection.execute(
                """
                INSERT OR IGNORE INTO expense_items
                    (id, dept, document_date, title, amount, account, status, source_usage_keys_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(item["id"]),
                    str(item["dept"]),
                    str(item["date"]),
                    str(item["title"]),
                    int(item["amount"]),
                    str(item["account"]),
                    str(item["status"]),
                    json.dumps(item.get("sourceUsageKeys", []), ensure_ascii=False),
                ),
            )

    def ensure_session(
        self,
        session_id: str | None,
        user_id: str,
        role: str,
        branch_id: int,
        workspace: str | None = None,
    ) -> str:
        normalized = session_id.strip()[:80] if session_id and session_id.strip() else uuid.uuid4().hex
        now = utc_now()
        with self._lock, self.connect() as connection:
            owner = connection.execute(
                "SELECT user_id FROM app_sessions WHERE id = ?",
                (normalized,),
            ).fetchone()
            if owner is not None and owner["user_id"] != user_id:
                raise SessionOwnershipError("이 세션에 접근할 수 없습니다.")
            connection.execute(
                """
                INSERT INTO app_sessions
                    (id, user_id, user_role, branch_id, workspace, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    user_role = excluded.user_role,
                    branch_id = excluded.branch_id,
                    workspace = COALESCE(excluded.workspace, app_sessions.workspace),
                    updated_at = excluded.updated_at
                """,
                (normalized, user_id, role, branch_id, workspace, now, now),
            )
        return normalized

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        workspace: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        message_id = uuid.uuid4().hex
        now = utc_now()
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_messages
                    (id, session_id, role, content, workspace, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    session_id,
                    role,
                    content,
                    workspace,
                    json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                    now,
                ),
            )
            connection.execute(
                "UPDATE app_sessions SET workspace = COALESCE(?, workspace), updated_at = ? WHERE id = ?",
                (workspace, now, session_id),
            )
        return message_id

    def session(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM app_sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return None
            messages = connection.execute(
                "SELECT * FROM app_messages WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
        return {
            "id": row["id"],
            "userId": row["user_id"],
            "role": row["user_role"],
            "branchId": row["branch_id"],
            "workspace": row["workspace"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "messages": [self._message_dict(item) for item in messages],
        }

    @staticmethod
    def _message_dict(row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload_json"]) if row["payload_json"] else None
        return {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "workspace": row["workspace"],
            "payload": payload,
            "createdAt": row["created_at"],
        }

    def latest_payload(self, session_id: str, kind: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM app_messages
                WHERE session_id = ? AND payload_json IS NOT NULL
                ORDER BY created_at DESC
                """,
                (session_id,),
            ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("kind") == kind:
                return payload
        return None

    def set_pending_expense(self, session_id: str, token: str, action: dict[str, Any]) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO expense_pending_actions (session_id, token, action_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    token = excluded.token,
                    action_json = excluded.action_json,
                    updated_at = excluded.updated_at
                """,
                (session_id, token, json.dumps(action, ensure_ascii=False), utc_now()),
            )

    def pending_expense(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT token, action_json FROM expense_pending_actions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {"token": row["token"], **json.loads(row["action_json"])}

    def clear_pending_expense(self, session_id: str) -> None:
        with self._lock, self.connect() as connection:
            connection.execute("DELETE FROM expense_pending_actions WHERE session_id = ?", (session_id,))

    def action_result(self, idempotency_key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT response_json FROM expense_action_results WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return json.loads(row["response_json"]) if row else None

    def store_action_result(self, idempotency_key: str, response: dict[str, Any]) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO expense_action_results
                    (idempotency_key, response_json, created_at)
                VALUES (?, ?, ?)
                """,
                (idempotency_key, json.dumps(response, ensure_ascii=False), utc_now()),
            )
