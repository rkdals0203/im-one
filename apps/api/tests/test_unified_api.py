from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from im_one_agent.sql_generator import GeneratedSQL
from imax_api.config import get_settings
from imax_api.main import create_app


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for frame in body.strip().split("\n\n"):
        event = "message"
        data: list[str] = []
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data.append(line.removeprefix("data:").strip())
        if data:
            events.append((event, json.loads("\n".join(data))))
    return events


@pytest.fixture
def configured_environment(tmp_path, monkeypatch) -> Iterator[dict[str, str]]:
    paths = {
        "IMAX_APP_DB": str(tmp_path / "app.sqlite"),
        "IMAX_CHECKPOINT_DB": str(tmp_path / "checkpoints.sqlite"),
        "IM_ONE_DB_PATH": str(tmp_path / "target.sqlite"),
        "IM_ONE_AUDIT_PATH": str(tmp_path / "audit.jsonl"),
        "IM_ONE_FEEDBACK_PATH": str(tmp_path / "feedback.jsonl"),
        "IMAX_UPLOADS_DIR": str(tmp_path / "uploads"),
    }
    for name, value in paths.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("IMAX_DISABLE_LLM_ROUTER", "1")
    monkeypatch.delenv("IM_ONE_API_TOKEN", raising=False)
    monkeypatch.delenv("IM_ONE_LLM_ALLOW_LOCAL_NO_AUTH", raising=False)
    get_settings.cache_clear()
    yield paths
    get_settings.cache_clear()


def post_assistant(client: TestClient, message: str, **extra) -> list[tuple[str, dict]]:
    response = client.post(
        "/api/v1/assistant/messages",
        json={"message": message, "role": "branch_manager", "branchId": 1, **extra},
    )
    assert response.status_code == 200
    return parse_sse(response.text)


def result_event(events: list[tuple[str, dict]]) -> dict:
    return next(payload for name, payload in events if name == "result")


def test_supervisor_routes_grounded_knowledge_and_clarifies(configured_environment) -> None:
    with TestClient(create_app()) as client:
        knowledge_events = post_assistant(client, "회의실 예약 절차와 화면번호를 알려줘")
        route = next(payload for name, payload in knowledge_events if name == "route")
        result = result_event(knowledge_events)

        assert route["workspace"] == "knowledge"
        assert result["payload"]["kind"] == "knowledge"
        assert result["payload"]["citations"]
        assert result["payload"]["generationEngine"] == "grounded_search"

        clarification = result_event(post_assistant(client, "안녕하세요"))
        clarification_events = post_assistant(client, "무슨 일을 할 수 있어?")
        assert any(name == "clarification" for name, _ in clarification_events)
        assert clarification["workspace"] == "clarification"
        assert {item["workspace"] for item in clarification["payload"]["options"]} == {
            "knowledge",
            "data",
            "expense",
        }


def test_nl2sql_agent_keeps_validation_and_role_scope(configured_environment, monkeypatch) -> None:
    def generated_sql(*args, **kwargs):
        return GeneratedSQL(
            sql=(
                "SELECT b.branch_name, STRFTIME('%Y-%m', a.opened_at) AS opened_month, "
                "COUNT(*) AS new_account_count FROM accounts a "
                "JOIN branches b ON a.branch_id = b.branch_id "
                "WHERE a.branch_id = 1 AND a.opened_at >= DATE('2026-06-24', '-3 months') "
                "GROUP BY b.branch_name, opened_month ORDER BY opened_month, b.branch_name LIMIT 50"
            ),
            reason="지점 권한 범위에서 최근 3개월 신규 계좌를 월별 집계합니다.",
            engine="llm",
            model="test-llm",
        )

    monkeypatch.setattr("im_one_agent.graph.generate_sql", generated_sql)
    with TestClient(create_app()) as client:
        result = result_event(post_assistant(client, "지난 3개월간 지점별 신규 계좌 수 추이는?"))

    payload = result["payload"]
    assert result["workspace"] == "data"
    assert payload["kind"] == "data"
    assert payload["validation"]["allowed"] is True
    assert payload["rows"]
    assert payload["columns"]
    assert "branch_id = 1" in payload["sql"].lower()
    assert payload["executionTrace"]


def test_expense_actions_require_confirmation_and_are_idempotent(configured_environment) -> None:
    with TestClient(create_app()) as client:
        draft_result = result_event(post_assistant(client, "스타벅스 88,000원 법인카드 품의해줘"))
        pending = draft_result["payload"]["overview"]["pendingAction"]
        session_id = draft_result["sessionId"]
        action_body = {
            "sessionId": session_id,
            "action": "confirm",
            "confirmationToken": pending["token"],
            "idempotencyKey": "expense-action-0001",
            "role": "branch_manager",
            "branchId": 1,
        }

        rejected = client.post(
            "/api/v1/expenses/actions",
            json={**action_body, "action": "reject", "confirmationToken": "wrong-token"},
        )
        assert rejected.status_code == 400

        first = client.post("/api/v1/expenses/actions", json=action_body)
        second = client.post("/api/v1/expenses/actions", json=action_body)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()
        assert len(first.json()["created"]) == 1
        overview = client.get(
            "/api/v1/expenses/overview",
            params={"sessionId": session_id, "role": "branch_manager", "branchId": 1},
        ).json()["overview"]
        assert sum(item["title"].startswith("스타벅스") for item in overview["items"]) == 1


def test_session_survives_server_restart_and_security_headers(configured_environment) -> None:
    with TestClient(create_app()) as first_client:
        assert {name for name, _ in first_client.app.state.supervisor.get_subgraphs()} == {
            "knowledge_agent",
            "data_agent",
            "expense_agent",
        }
        result = result_event(post_assistant(first_client, "회의실 예약 절차를 알려줘"))
        session_id = result["sessionId"]
        health = first_client.get("/api/v1/health")
        assert health.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in health.headers["content-security-policy"]

    with sqlite3.connect(configured_environment["IMAX_CHECKPOINT_DB"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] > 0

    get_settings.cache_clear()
    with TestClient(create_app()) as restarted_client:
        session = restarted_client.get(f"/api/v1/sessions/{session_id}")

    assert session.status_code == 200
    assert [message["role"] for message in session.json()["messages"]] == ["user", "assistant"]


def test_api_token_mode_protects_agent_routes(configured_environment, monkeypatch) -> None:
    monkeypatch.setenv("IM_ONE_API_TOKEN", "test-api-token")

    with TestClient(create_app()) as client:
        unauthorized = client.post(
            "/api/v1/assistant/messages",
            json={"message": "안녕하세요", "role": "branch_manager", "branchId": 1},
        )
        authorized = client.post(
            "/api/v1/assistant/messages",
            json={"message": "안녕하세요", "role": "branch_manager", "branchId": 1},
            headers={"Authorization": "Bearer test-api-token"},
        )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert result_event(parse_sse(authorized.text))["workspace"] == "clarification"


def test_trusted_identity_cannot_take_over_an_existing_session(configured_environment, monkeypatch) -> None:
    monkeypatch.setenv("IM_ONE_AUTH_MODE", "trusted_headers")
    monkeypatch.setenv("IM_ONE_TRUSTED_PROXY_TOKEN", "proxy-secret")
    owner_headers = {
        "X-IM-One-Proxy-Token": "proxy-secret",
        "X-IM-One-User": "owner.user",
        "X-IM-One-Role": "branch_manager",
        "X-IM-One-Branch": "1",
    }
    other_headers = {**owner_headers, "X-IM-One-User": "other.user"}

    with TestClient(create_app()) as client:
        created = client.post(
            "/api/v1/assistant/messages",
            json={"message": "안녕하세요", "role": "branch_manager", "branchId": 1},
            headers=owner_headers,
        )
        session_id = result_event(parse_sse(created.text))["sessionId"]
        takeover = client.post(
            "/api/v1/assistant/messages",
            json={"sessionId": session_id, "message": "안녕하세요", "role": "branch_manager", "branchId": 1},
            headers=other_headers,
        )

        expense = client.post(
            "/api/v1/assistant/messages",
            json={"message": "스타벅스 88,000원 법인카드 품의해줘", "role": "branch_manager", "branchId": 1},
            headers=owner_headers,
        )
        expense_result = result_event(parse_sse(expense.text))
        pending = expense_result["payload"]["overview"]["pendingAction"]
        expense_takeover = client.post(
            "/api/v1/expenses/actions",
            json={
                "sessionId": expense_result["sessionId"],
                "action": "confirm",
                "confirmationToken": pending["token"],
                "idempotencyKey": "takeover-attempt-001",
                "role": "branch_manager",
                "branchId": 1,
            },
            headers=other_headers,
        )

    assert takeover.status_code == 403
    assert expense_takeover.status_code == 403
