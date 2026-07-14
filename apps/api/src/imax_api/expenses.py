from __future__ import annotations

import json
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from .database import AppDatabase


BUDGET_CODES = (
    {"value": "08WA 본사 업무추진비", "code": "08WA", "name": "본사 업무추진비", "allocated": 2_000_000},
    {"value": "40AA 본사 회의비", "code": "40AA", "name": "본사 회의비", "allocated": 1_200_000},
    {"value": "45BC 본사 조직활성화비", "code": "45BC", "name": "본사 조직활성화비", "allocated": 800_000},
    {"value": "12CC (일반)전화료", "code": "12CC", "name": "(일반)전화료", "allocated": 500_000},
    {"value": "20DD 교통비", "code": "20DD", "name": "교통비", "allocated": 1_500_000},
)

WORK_PROMOTION = "08WA 본사 업무추진비"
MEETING = "40AA 본사 회의비"
ORG_ACTIVATION = "45BC 본사 조직활성화비"
PHONE = "12CC (일반)전화료"
TRANSPORT = "20DD 교통비"


class ExpenseError(ValueError):
    pass


class ExpenseService:
    def __init__(self, database: AppDatabase) -> None:
        self.database = database

    def overview(self, session_id: str | None = None) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM expense_items ORDER BY id DESC"
            ).fetchall()
        items = [
            {
                "id": row["id"],
                "dept": row["dept"],
                "date": row["document_date"],
                "title": row["title"],
                "amount": row["amount"],
                "account": row["account"],
                "status": row["status"],
                "sourceUsageKeys": json.loads(row["source_usage_keys_json"]),
            }
            for row in rows
        ]
        budgets = []
        for budget in BUDGET_CODES:
            used = sum(
                item["amount"]
                for item in items
                if item["status"] == "승인" and item["account"] == budget["value"]
            )
            budgets.append({**budget, "used": used, "remaining": budget["allocated"] - used})
        pending = self.database.pending_expense(session_id) if session_id else None
        return {
            "items": items,
            "budgets": budgets,
            "pendingCount": sum(item["status"] == "미승인" for item in items),
            "approvedCount": sum(item["status"] == "승인" for item in items),
            "pendingAction": self._public_pending(pending),
        }

    def handle_message(self, session_id: str, message: str) -> dict[str, Any]:
        text = message.strip()
        if not text:
            raise ExpenseError("메시지를 입력해주세요.")

        if any(keyword in text for keyword in ("예산", "잔액", "사용액")):
            overview = self.overview(session_id)
            remaining = sum(item["remaining"] for item in overview["budgets"])
            return self._payload(
                f"현재 전체 예산 잔액은 {remaining:,}원입니다. 계정별 사용액과 잔액을 함께 표시했습니다.",
                overview,
            )

        action_type = "approve" if "승인" in text else "cancel" if any(word in text for word in ("취소", "반려")) else None
        if action_type:
            return self._prepare_item_action(session_id, text, action_type)

        if any(keyword in text for keyword in ("품의", "결의", "법인카드", "영수증", "결제")):
            return self._prepare_draft(session_id, text)

        overview = self.overview(session_id)
        return self._payload(
            "법인카드 사용내역 품의, 미승인 문서 승인·반려, 계정별 예산 잔액을 요청할 수 있습니다.",
            overview,
        )

    def _prepare_item_action(self, session_id: str, text: str, action_type: str) -> dict[str, Any]:
        requested_ids = [int(value) for value in re.findall(r"\b(\d{4,})\b", text)]
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, dept, title, amount FROM expense_items WHERE status = '미승인' ORDER BY id DESC"
            ).fetchall()
        candidates = [dict(row) for row in rows]
        if requested_ids:
            candidates = [item for item in candidates if item["id"] in requested_ids]
        elif any(word in text for word in ("전체", "모두", "전부", "일괄")):
            pass
        else:
            candidates = candidates[:5]

        if not candidates:
            return self._payload("조건에 맞는 미승인 문서가 없습니다.", self.overview(session_id))

        token = uuid.uuid4().hex
        action = {
            "type": action_type,
            "itemIds": [item["id"] for item in candidates],
            "items": candidates,
        }
        self.database.set_pending_expense(session_id, token, action)
        verb = "승인" if action_type == "approve" else "반려"
        amount = sum(item["amount"] for item in candidates)
        overview = self.overview(session_id)
        return self._payload(
            f"{len(candidates)}건, 총 {amount:,}원을 {verb}할 예정입니다. 내용을 확인해주세요.",
            overview,
        )

    def _prepare_draft(self, session_id: str, text: str) -> dict[str, Any]:
        amount = self._parse_amount(text)
        if amount is None or amount <= 0:
            return self._payload(
                "품의할 금액을 원 단위로 함께 입력해주세요. 예: 스타벅스 88,000원 품의해줘",
                self.overview(session_id),
            )

        account = self._account_for(text)
        document_date = self._parse_date(text)
        store = self._parse_store(text)
        requires_minutes = account == MEETING and amount >= 100_000
        draft = {
            "dept": self._parse_department(text),
            "date": document_date,
            "title": f"{store} 법인카드 사용",
            "amount": amount,
            "account": account,
            "status": "미승인",
            "requiresMinutes": requires_minutes,
            "evidencePath": None,
            "store": store,
        }
        token = uuid.uuid4().hex
        self.database.set_pending_expense(session_id, token, {"type": "create", "draft": draft})
        evidence_note = " 회의록 PDF를 첨부한 뒤" if requires_minutes else ""
        return self._payload(
            f"{draft['dept']}의 {account} {amount:,}원 품의 초안을 만들었습니다.{evidence_note} 등록 여부를 확인해주세요.",
            self.overview(session_id),
        )

    def perform_action(
        self,
        session_id: str,
        action: str,
        confirmation_token: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        scoped_idempotency_key = f"{session_id}:{idempotency_key}"
        cached = self.database.action_result(scoped_idempotency_key)
        if cached is not None:
            return cached
        pending = self.database.pending_expense(session_id)
        if pending is None or not confirmation_token or pending["token"] != confirmation_token:
            raise ExpenseError("확인 토큰이 만료되었거나 일치하지 않습니다.")
        if action == "reject":
            self.database.clear_pending_expense(session_id)
            response = self._payload("요청을 취소했습니다.", self.overview(session_id))
            self.database.store_action_result(scoped_idempotency_key, response)
            return response
        if action != "confirm":
            raise ExpenseError("지원하지 않는 지출업무 명령입니다.")

        action_type = pending["type"]
        if action_type == "create":
            response = self._create_pending_draft(session_id, pending)
        elif action_type in {"approve", "cancel"}:
            response = self._apply_item_action(session_id, pending)
        else:
            raise ExpenseError("저장된 지출업무 명령을 해석할 수 없습니다.")
        self.database.store_action_result(scoped_idempotency_key, response)
        return response

    def _create_pending_draft(self, session_id: str, pending: dict[str, Any]) -> dict[str, Any]:
        draft = pending["draft"]
        if draft.get("requiresMinutes") and not draft.get("evidencePath"):
            raise ExpenseError("10만원 이상 회의비는 회의록 PDF 첨부가 필요합니다.")
        entries = self._ledger_entries(draft["account"], int(draft["amount"]))
        with self.database.connect() as connection:
            next_id = connection.execute("SELECT COALESCE(MAX(id), 1000) + 1 FROM expense_items").fetchone()[0]
            created = []
            for index, entry in enumerate(entries):
                title = draft["title"]
                if len(entries) > 1:
                    title = f"{title} ({entry['account'].split(' ', 1)[1]} 분할처리)"
                item_id = next_id + index
                connection.execute(
                    """
                    INSERT INTO expense_items
                        (id, dept, document_date, title, amount, account, status, source_usage_keys_json)
                    VALUES (?, ?, ?, ?, ?, ?, '미승인', '[]')
                    """,
                    (item_id, draft["dept"], draft["date"], title, entry["amount"], entry["account"]),
                )
                created.append({"id": item_id, **draft, **entry, "title": title, "status": "미승인"})
        self.database.clear_pending_expense(session_id)
        return self._payload(
            f"지출품의 {len(created)}건을 등록했습니다. 문서번호는 {', '.join(str(item['id']) for item in created)}입니다.",
            self.overview(session_id),
            created=created,
        )

    def _apply_item_action(self, session_id: str, pending: dict[str, Any]) -> dict[str, Any]:
        item_ids = [int(value) for value in pending.get("itemIds", [])]
        placeholders = ",".join("?" for _ in item_ids)
        if not placeholders:
            raise ExpenseError("처리할 문서가 없습니다.")
        with self.database.connect() as connection:
            if pending["type"] == "approve":
                cursor = connection.execute(
                    f"UPDATE expense_items SET status = '승인' WHERE status = '미승인' AND id IN ({placeholders})",
                    item_ids,
                )
                verb = "승인"
            else:
                cursor = connection.execute(
                    f"DELETE FROM expense_items WHERE status = '미승인' AND id IN ({placeholders})",
                    item_ids,
                )
                verb = "반려"
            changed = cursor.rowcount
        self.database.clear_pending_expense(session_id)
        return self._payload(f"{changed}건을 {verb} 처리했습니다.", self.overview(session_id))

    def attach_evidence(self, session_id: str, token: str, file_path: Path) -> dict[str, Any]:
        pending = self.database.pending_expense(session_id)
        if pending is None or pending["token"] != token or pending.get("type") != "create":
            raise ExpenseError("첨부할 품의 초안을 찾지 못했습니다.")
        pending["draft"]["evidencePath"] = str(file_path)
        self.database.set_pending_expense(
            session_id,
            token,
            {key: value for key, value in pending.items() if key != "token"},
        )
        return self._payload("회의록 PDF를 첨부했습니다.", self.overview(session_id))

    def _ledger_entries(self, account: str, amount: int) -> list[dict[str, Any]]:
        overview = self.overview()
        budget = next(item for item in overview["budgets"] if item["value"] == account)
        if amount <= budget["remaining"]:
            return [{"account": account, "amount": amount}]
        if account == WORK_PROMOTION:
            raise ExpenseError("본사 업무추진비 예산 잔액을 초과해 등록할 수 없습니다.")
        primary = max(int(budget["remaining"]), 0)
        shortfall = amount - primary
        promotion = next(item for item in overview["budgets"] if item["value"] == WORK_PROMOTION)
        if shortfall > promotion["remaining"]:
            raise ExpenseError("대체 가능한 업무추진비를 포함해도 예산이 부족합니다.")
        entries = []
        if primary:
            entries.append({"account": account, "amount": primary})
        entries.append({"account": WORK_PROMOTION, "amount": shortfall})
        return entries

    @staticmethod
    def _parse_amount(text: str) -> int | None:
        manwon = re.search(r"(\d+(?:\.\d+)?)\s*만\s*원", text)
        if manwon:
            return int(float(manwon.group(1)) * 10_000)
        won = re.search(r"([\d,]+)\s*원", text)
        return int(won.group(1).replace(",", "")) if won else None

    @staticmethod
    def _parse_date(text: str) -> str:
        iso = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", text)
        if iso:
            return f"{int(iso.group(1)):04d}-{int(iso.group(2)):02d}-{int(iso.group(3)):02d}"
        korean = re.search(r"(\d{1,2})월\s*(\d{1,2})일", text)
        if korean:
            return f"{date.today().year:04d}-{int(korean.group(1)):02d}-{int(korean.group(2)):02d}"
        return date.today().isoformat()

    @staticmethod
    def _parse_department(text: str) -> str:
        for department in ("IT정보팀", "IT기획", "IT금융상품부", "IT인프라부", "디지털전략부", "정보보안부"):
            if department in text:
                return department
        return "IT정보팀"

    @staticmethod
    def _parse_store(text: str) -> str:
        amount_match = re.search(r"[\d,.]+\s*(?:만\s*)?원", text)
        prefix = text[: amount_match.start()] if amount_match else text
        prefix = re.sub(r"20\d{2}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}월\s*\d{1,2}일|\d{1,2}:\d{2}", " ", prefix)
        words = [word for word in prefix.split() if word not in {"법인카드", "결제", "품의", "결의", "해줘", "처리"}]
        return " ".join(words[-3:]) or "법인카드 사용처"

    @staticmethod
    def _account_for(text: str) -> str:
        if any(word in text for word in ("전화", "통신")):
            return PHONE
        if any(word in text for word in ("택시", "교통")):
            return TRANSPORT
        if any(word in text for word in ("회의", "미팅")):
            return MEETING
        if any(word in text for word in ("조직", "회식", "활성화")):
            return ORG_ACTIVATION
        return WORK_PROMOTION

    @staticmethod
    def _public_pending(pending: dict[str, Any] | None) -> dict[str, Any] | None:
        if pending is None:
            return None
        return {key: value for key, value in pending.items() if key != "action_json"}

    @staticmethod
    def _payload(message: str, overview: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {"kind": "expense", "message": message, "overview": overview, **extra}
