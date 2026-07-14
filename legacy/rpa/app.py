"""
사내 지출결의/관리 시스템 (Streamlit 포팅판)

j.html(단일 HTML 프로토타입)을 Streamlit으로 변환한 버전. 비즈니스 규칙은 PRD.md를,
원본 구현 구조는 CLAUDE.md를 참고해 그대로 유지했다. Streamlit의 rerun 기반 모델에
맞추기 위해 아래 지점은 원본과 다르게(그러나 동등한 기능으로) 구현했다:

- 플로팅 챗봇 위젯 -> 화면 중앙(main, st.columns로 폭 제한) 고정 패널로 대체 (Streamlit은 자유
  위치의 오버레이를 지원하지 않음). ERP 탭(지출결의입력/승인/예산내역)은 반대로 사이드바로 옮겨,
  Streamlit 기본 사이드바 접기(<<) 버튼이 곧 "ERP 화면 보기/숨기기" 토글 역할을 하도록 했다.
- 브라우저 alert()/confirm() -> st.error/st.warning + 명시적 확인 버튼으로 대체
- 예산 부족 시 자동분할 확인(confirm)이 챗봇 흐름 중간에도 필요해, 새로운 대화 상태
  'SHORTFALL_CONFIRMATION'을 추가했다 (원본 PRD/CLAUDE.md에는 없던 중간 확인 단계).
"""

import json
import os
import random
import re
from datetime import date

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ---------------------------------------------------------------------------
# 상수 / 마스터 데이터 (j.html의 BUDGET_CODES / DEPT_CARDS / STORE_POOL 그대로 이식)
# ---------------------------------------------------------------------------

BUDGET_CODES = [
    {"value": "08WA 본사 업무추진비", "code": "08WA", "name": "본사 업무추진비", "allocated": 2000000},
    {"value": "40AA 본사 회의비", "code": "40AA", "name": "본사 회의비", "allocated": 1200000},
    {"value": "45BC 본사 조직활성화비", "code": "45BC", "name": "본사 조직활성화비", "allocated": 800000},
    {"value": "12CC (일반)전화료", "code": "12CC", "name": "(일반)전화료", "allocated": 500000},
    {"value": "20DD 교통비", "code": "20DD", "name": "교통비", "allocated": 1500000},
]

MEETING_VALUE = "40AA 본사 회의비"
ORG_ACTIVATION_VALUE = "45BC 본사 조직활성화비"
WORK_PROMOTION_VALUE = "08WA 본사 업무추진비"

DEPT_CARDS = {
    "C23 IT기획(예산)": [{"number": "5585-3176-0945-5522", "holder": "김윤석"}],
    "C30 IT정보팀": [
        {"number": "4521-8890-1123-7765", "holder": "김민준"},
        {"number": "4521-8890-1123-9981", "holder": "박서연"},
    ],
    "C26 IT금융상품부": [{"number": "9012-4456-7789-3345", "holder": "최민재"}],
    "C21 IT인프라부": [
        {"number": "3345-6678-9012-5567", "holder": "정다은"},
        {"number": "3345-6678-9012-8843", "holder": "한지훈"},
    ],
    "C25 디지털전략부": [{"number": "7788-2233-5566-9910", "holder": "오세훈"}],
    "C28 정보보안부": [{"number": "6654-3321-8877-4409", "holder": "윤아름"}],
}

STORE_POOL = [
    {"name": "동동국수 여의도점", "location": "여의도"},
    {"name": "쿠사 (일식)", "location": "기타"},
    {"name": "(주)공영식품 기소야 여의도점", "location": "여의도"},
    {"name": "스타벅스 여의도한국거래소점", "location": "여의도"},
    {"name": "교촌치킨 강남역점", "location": "기타"},
    {"name": "본죽&비빔밥 여의도점", "location": "여의도"},
    {"name": "김밥천국 마포공덕점", "location": "기타"},
    {"name": "CU 여의도IFC점", "location": "여의도"},
    {"name": "파리바게뜨 목동점", "location": "기타"},
    {"name": "놀부부대찌개 여의도점", "location": "여의도"},
]

# PRD §3.2: 테스트 프리셋 (실운영 전환 시 True로)
ENFORCE_DOC_DATE_RULE = False

DATA_FILE = "expense_demo_data.json"

POSITIVE_RE = r"(응|웅|넹|넵|네|확인|어|오케이|okay|ok|yes|ㅇㅇ|ㅇ케이)"

GREETING = (
    '안녕하세요! 법인카드 결제 내역이 감지되면 자동으로 품의 작성을 도와드려요. '
    '<b>"1월 5일 19:15 스타벅스 88000원 품의해줘"</b>처럼 날짜·시간·상호명·금액을 함께 채팅으로 '
    '요청해 보세요! (같은 가맹점 방문이 여러 건이면 조건을 더 구체적으로 입력해야 정확히 특정됩니다)'
)

DEPT_NAMES = [" ".join(v.split(" ")[1:]) for v in DEPT_CARDS.keys()]


# ---------------------------------------------------------------------------
# 영속성 (localStorage 대신 로컬 JSON 파일 - PRD §2.3/§6.1: 새로고침/재진입해도 유지)
# ---------------------------------------------------------------------------

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"dataset": [], "cardUsageCache": {}, "usedCardUsageKeys": []}


def save_data():
    payload = {
        "dataset": st.session_state.dataset,
        "cardUsageCache": st.session_state.card_usage_cache,
        "usedCardUsageKeys": list(st.session_state.used_card_usage_keys),
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def reset_test_data():
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)
    st.session_state.clear()
    st.rerun()


# ---------------------------------------------------------------------------
# 예산 / 품의일자 / 원장분할 규칙 (PRD §3.1~§3.3)
# ---------------------------------------------------------------------------

def get_budget_used(account_value):
    return sum(
        d["amount"] for d in st.session_state.dataset
        if d["status"] == "승인" and d["account"] == account_value
    )


def get_budget_remains(account_value):
    budget = next((b for b in BUDGET_CODES if b["value"] == account_value), None)
    if not budget:
        return 0
    return budget["allocated"] - get_budget_used(account_value)


def time_to_minutes(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m


def validate_doc_date(d):
    """PRD §3.2: 월/수/금만 가능, 23일 불가. (weekday(): Mon=0 ... Sun=6)"""
    is_allowed_weekday = d.weekday() in (0, 2, 4)
    is_blocked_23rd = d.day == 23
    valid = (not ENFORCE_DOC_DATE_RULE) or (is_allowed_weekday and not is_blocked_23rd)
    return valid, is_blocked_23rd


def build_ledger_entries(account_value, amount):
    """
    PRD §3.3 Shortfall Splitting 규칙.
    원본 JS는 window.confirm()으로 그 자리에서 분기 처리를 확인받지만, Streamlit은 블로킹
    다이얼로그가 없으므로 상태를 세 갈래로 반환해 호출부에서 확인 UI를 그리도록 한다.
      - ("ok", entries, None)          : 그대로 제출 가능
      - ("confirm", entries, message)  : 부족분 자동분할 - 사용자 재확인 필요
      - ("blocked", None, message)     : 예산 초과로 제출 불가
    """
    budget = next(b for b in BUDGET_CODES if b["value"] == account_value)
    remains = get_budget_remains(account_value)

    if amount <= remains:
        return "ok", [{"account": account_value, "amount": amount}], None

    if account_value == WORK_PROMOTION_VALUE:
        msg = (
            f'[5527] "{budget["name"]}" 예산 잔액을 초과합니다.\n'
            f'현재 잔액: {remains:,}원 / 요청 금액: {amount:,}원\n'
            f'본사업무추진비는 부족해도 다른 예산으로 대체할 수 없습니다.'
        )
        return "blocked", None, msg

    primary_portion = max(remains, 0)
    shortfall = amount - primary_portion
    promo_remains = get_budget_remains(WORK_PROMOTION_VALUE)

    if shortfall > promo_remains:
        total_gap = shortfall - promo_remains
        msg = (
            f'[5527] 예산이 부족합니다.\n'
            f'"{budget["name"]}" 잔액: {remains:,}원 + 본사업무추진비 잔액: {promo_remains:,}원으로도 '
            f'{total_gap:,}원이 부족합니다.'
        )
        return "blocked", None, msg

    entries = []
    if primary_portion > 0:
        entries.append({"account": account_value, "amount": primary_portion})
    entries.append({"account": WORK_PROMOTION_VALUE, "amount": shortfall})
    msg = (
        f'[5527] "{budget["name"]}" 예산이 부족하여 부족분이 본사업무추진비에서 자동 처리됩니다.\n'
        f'{budget["name"]}: {primary_portion:,}원 / 본사업무추진비: {shortfall:,}원\n'
        f'계속 진행하시겠습니까?'
    )
    return "confirm", entries, msg


# ---------------------------------------------------------------------------
# 카드 사용내역 생성/조회 (§2.3, §4.1)
# ---------------------------------------------------------------------------

def pad2(n):
    return f"{n:02d}"


def card_usage_key(card_number, item):
    return f'{card_number}|{item["date"]}|{item["time"]}|{item["store"]}|{item["amount"]}'


def random_usage_for_card(card_number):
    cache = st.session_state.card_usage_cache
    if card_number in cache:
        return cache[card_number]

    count = random.randint(3, 5)
    items = []
    for _ in range(count):
        store = random.choice(STORE_POOL)
        day = random.randint(2, 15)
        hour = random.randint(7, 21)
        minute = random.randint(0, 59)
        amount = random.randint(8, 150) * 1000
        items.append({
            "store": store["name"],
            "location": store["location"],
            "date": f"2026-01-{pad2(day)}",
            "time": f"{pad2(hour)}:{pad2(minute)}",
            "amount": amount,
        })
    items.sort(key=lambda it: it["date"] + it["time"], reverse=True)
    cache[card_number] = items
    save_data()
    return items


def mark_card_usage_used(usage_key):
    st.session_state.used_card_usage_keys.add(usage_key)
    save_data()


def collect_dept_card_usage(dept_value):
    """현재 부서가 보유한 모든 카드의 미사용 사용내역을 후보로 모은다 (수동 선택/챗봇 검색 공통)."""
    dept_name = " ".join(dept_value.split(" ")[1:])
    cards = DEPT_CARDS.get(dept_value, [])
    candidates = []
    for card in cards:
        for item in random_usage_for_card(card["number"]):
            usage_key = card_usage_key(card["number"], item)
            if usage_key in st.session_state.used_card_usage_keys:
                continue
            candidates.append({**item, "cardNumber": card["number"], "holder": card["holder"], "usageKey": usage_key})
    return dept_name, cards, candidates


def build_tx_from_candidate(picked, dept_value, dept_name):
    return {
        "cardNumber": picked["cardNumber"],
        "holder": picked["holder"],
        "usageKey": picked["usageKey"],
        "deptValue": dept_value,
        "deptName": dept_name,
        "store": picked["store"],
        "location": picked["location"],
        "date": picked["date"],
        "time": picked["time"],
        "amount": picked["amount"],
    }


# ---------------------------------------------------------------------------
# 공용 핵심 로직: 승인 / 취소 / 품의 등록 (UI 버튼과 챗봇이 동일하게 재사용)
# ---------------------------------------------------------------------------

def approve_items(ids):
    id_set = set(ids)
    approved = []
    for d in st.session_state.dataset:
        if d["id"] in id_set and d["status"] == "미승인":
            d["status"] = "승인"
            approved.append(d)
    save_data()
    return approved


def cancel_items(ids):
    """PRD §4.2 복원 규칙: 취소 후 남은 건들이 여전히 참조하는 카드결제 키는 되돌리지 않는다."""
    id_set = set(ids)
    to_cancel = [d for d in st.session_state.dataset if d["id"] in id_set and d["status"] == "미승인"]
    if not to_cancel:
        return []

    cancel_ids = {d["id"] for d in to_cancel}
    st.session_state.dataset = [d for d in st.session_state.dataset if d["id"] not in cancel_ids]

    still_referenced = set()
    for d in st.session_state.dataset:
        still_referenced.update(d.get("sourceUsageKeys", []))

    for item in to_cancel:
        for k in item.get("sourceUsageKeys", []):
            if k not in still_referenced:
                st.session_state.used_card_usage_keys.discard(k)

    save_data()
    return to_cancel


def finalize_document_submission(dept_name, doc_date_str, entries, tx=None, prop=None, summary=None):
    """entries(분할 포함 가능)를 dataset에 신규 라인으로 등록. 수동 폼/챗봇 양쪽에서 공용으로 사용."""
    next_id = max((d["id"] for d in st.session_state.dataset), default=1000) + 1
    source_keys = [tx["usageKey"]] if tx and tx.get("usageKey") else []
    created = []

    for entry in entries:
        if prop is not None:
            title = build_purpose_for_account(entry["account"], prop["attendeeLabel"], prop["topic"])
        else:
            budget = next(b for b in BUDGET_CODES if b["value"] == entry["account"])
            title = f'{summary} ({budget["name"]} 분할처리)' if len(entries) > 1 else summary

        item = {
            "id": next_id,
            "dept": dept_name,
            "date": doc_date_str,
            "title": title,
            "amount": entry["amount"],
            "account": entry["account"],
            "status": "미승인",
            "sourceUsageKeys": source_keys,
        }
        st.session_state.dataset.insert(0, item)
        created.append(item)
        next_id += 1

    if tx and tx.get("usageKey"):
        mark_card_usage_used(tx["usageKey"])

    save_data()
    return created


# ---------------------------------------------------------------------------
# 챗봇 NLP 유틸 (§5.2A: 날짜/시간/금액/상호 파싱 + 퍼지매칭)
# ---------------------------------------------------------------------------

def levenshtein(a, b):
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        dp[i][0] = i
    for j in range(len(b) + 1):
        dp[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1])
    return dp[len(a)][len(b)]


def store_matches(store, query):
    norm_store = re.sub(r"\s+", "", store)
    norm_query = re.sub(r"\s+", "", query)
    if not norm_query:
        return False
    if norm_query in norm_store:
        return True

    max_dist = 1 if len(norm_query) <= 2 else max(1, int(len(norm_query) * 0.25))

    if len(norm_query) >= len(norm_store):
        return levenshtein(norm_query, norm_store) <= max_dist
    for i in range(len(norm_store) - len(norm_query) + 1):
        window = norm_store[i:i + len(norm_query)]
        if levenshtein(window, norm_query) <= max_dist:
            return True
    return False


def parse_transaction_query(text):
    remaining = text
    result = {"date": None, "time": None, "amount": None, "store": None}

    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", remaining)
    if m:
        result["date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        remaining = remaining.replace(m.group(0), " ", 1)
    else:
        m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", remaining)
        if m:
            result["date"] = f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
            remaining = remaining.replace(m.group(0), " ", 1)

    m = re.search(r"(\d{1,2}):(\d{2})", remaining)
    if m:
        result["time"] = f"{int(m.group(1)):02d}:{m.group(2)}"
        remaining = remaining.replace(m.group(0), " ", 1)
    else:
        m = re.search(r"(\d{1,2})\s*시\s*(\d{1,2})?\s*분?", remaining)
        if m:
            minute = int(m.group(2)) if m.group(2) else 0
            result["time"] = f"{int(m.group(1)):02d}:{minute:02d}"
            remaining = remaining.replace(m.group(0), " ", 1)

    m = re.search(r"(\d+)\s*만\s*원?", remaining)
    if m:
        result["amount"] = int(m.group(1)) * 10000
        remaining = remaining.replace(m.group(0), " ", 1)
    else:
        m = re.search(r"(\d[\d,]{2,})\s*원", remaining)
        if m:
            result["amount"] = int(m.group(1).replace(",", ""))
            remaining = remaining.replace(m.group(0), " ", 1)
        else:
            m = re.search(r"\b(\d{4,7})\b", remaining)
            if m:
                result["amount"] = int(m.group(1))
                remaining = remaining.replace(m.group(0), " ", 1)

    remaining = re.sub(r"지출\s*품의", "", remaining)
    remaining = re.sub(r"품의서|품의|결재|상신", "", remaining)
    remaining = re.sub(r"내역|결제건|건(?=\s|$)", "", remaining)
    remaining = re.sub(r"올려줘|해\s*[줘주]|써\s*[줘주]|하겠다|할래(?:요)?|할게(?:요)?|부탁(?:해요|드려요|드립니다|해)?|상신해줘|등록해줘|상신|등록", "", remaining)
    remaining = re.sub(r"\s+", " ", remaining).strip()

    if remaining:
        result["store"] = remaining
    return result


def find_card_transaction_by_query(parsed):
    dept_value = st.session_state.dept_code
    dept_name, cards, candidates = collect_dept_card_usage(dept_value)

    if not cards:
        return {"error": "no-card", "deptName": dept_name}
    if not candidates:
        return {"error": "not-found", "deptName": dept_name}

    has_filter = parsed["date"] or parsed["time"] or parsed["amount"] is not None or parsed["store"]
    if not has_filter:
        # 조건 없이 "품의하겠다"처럼만 요청하면 무작위로 고르지 않고, 후보 목록을 보여주고 선택하게 한다.
        return {"error": "select", "deptValue": dept_value, "deptName": dept_name, "candidates": candidates}

    matches = candidates
    if parsed["date"]:
        matches = [c for c in matches if c["date"] == parsed["date"]]
    if parsed["time"]:
        matches = [c for c in matches if c["time"] == parsed["time"]]
    if parsed["amount"] is not None:
        matches = [c for c in matches if c["amount"] == parsed["amount"]]
    if parsed["store"]:
        matches = [c for c in matches if store_matches(c["store"], parsed["store"])]

    if not matches:
        return {"error": "not-found", "deptName": dept_name}
    if len(matches) > 1:
        return {"error": "ambiguous", "deptName": dept_name, "matches": matches}
    return {"tx": build_tx_from_candidate(matches[0], dept_value, dept_name)}


def is_card_command(text):
    # '품의'(품의서 포함)/'결재'/'상신' 중 하나라도 언급되면 품의 작성 요청의 핵심 명사로 간주한다.
    # ('결재해줘'/'결재해'처럼 승인 의도가 명확한 형태는 is_approval_command가 먼저 가로챈다.)
    if not re.search(r"품의|결재|상신", text):
        return False
    if re.search(r"올려줘|해\s*[줘주]|써\s*[줘주]|하겠다|할래|할게|부탁|상신|등록", text):
        return True
    # "...61000 품의"처럼 트리거 동사 없이 핵심 명사로 문장이 끝나는 경우도 등록 요청으로 간주
    return bool(re.search(r"(품의|결재|상신)\s*$", text.strip()))


def find_budget_code_mention(text):
    for b in BUDGET_CODES:
        if b["code"] in text:
            return b
    name_to_code = [
        (["업무추진비"], "08WA"),
        (["회의비"], "40AA"),
        (["조직활성화비"], "45BC"),
        (["전화료"], "12CC"),
        (["교통비"], "20DD"),
    ]
    for keys, code in name_to_code:
        if any(k in text for k in keys):
            return next(b for b in BUDGET_CODES if b["code"] == code)
    return None


def is_budget_query(text):
    return bool(re.search(r"예산|잔액|잔여|얼마|남았|남아", text))


def parse_attendee_input(text):
    # "참석자 : ... / 사유 : ..." 라벨 형식을 우선 인식 (줄바꿈/한 줄 모두 지원)
    attendee_match = re.search(r"참석자\s*[:：]\s*(.+?)\s*(?:\n|사유|내용|$)", text)
    reason_match = re.search(r"(?:사유|내용)\s*[:：]\s*(.+)", text, re.DOTALL)
    if attendee_match or reason_match:
        attendee_text = attendee_match.group(1).strip() if attendee_match else ""
        topic = reason_match.group(1).strip() if reason_match else ""
        if attendee_text or topic:
            return attendee_text, topic

    slash_idx = text.find("/")
    if slash_idx != -1:
        return text[:slash_idx].strip(), text[slash_idx + 1:].strip()

    segments = [s.strip() for s in re.split(r"[,，、]+", text) if s.strip()]
    if not segments:
        return text.strip(), ""

    last_segment = segments[-1]
    space_idx = last_segment.find(" ")
    if space_idx == -1:
        return text.strip(), ""

    last_name = last_segment[:space_idx].strip()
    topic = last_segment[space_idx + 1:].strip()
    attendee_text = ", ".join(segments[:-1] + [last_name])
    return attendee_text, topic


def build_attendee_label(attendee_text):
    names = [s.strip() for s in re.split(r"[,，、]+", attendee_text) if s.strip()]
    first = names[0] if names else attendee_text.strip()
    others_count = max(len(names) - 1, 0)
    return f"{first} 외 {others_count}인" if others_count > 0 else first


def build_purpose_for_account(account_value, attendee_label, topic):
    if account_value == MEETING_VALUE:
        return f"{attendee_label} {topic} 관련회의" if topic else f"{attendee_label} 회의"
    if account_value == ORG_ACTIVATION_VALUE:
        return (
            f"{attendee_label} {topic}관련 업무활성화로 인한 조직활성화비"
            if topic else f"{attendee_label} 업무활성화로 인한 조직활성화비"
        )
    return f"{attendee_label} {topic} 관련 업무추진비" if topic else f"{attendee_label} 업무추진비"


# ---------------------------------------------------------------------------
# 챗봇 승인/취소 명령 파싱 (§5.2F 신규)
# ---------------------------------------------------------------------------

def is_approval_command(text):
    return bool(re.search(r"승인해\s*줘|승인해|승인\s*처리|결재해\s*줘|결재해", text))


def is_cancel_command(text):
    return bool(re.search(r"승인취소|취소해\s*줘|취소해|반려해\s*줘|반려해|반려", text))


def parse_approval_target_query(text):
    remaining = text
    result = {"ids": [], "bulk": False, "amount": None, "amountOp": None, "dept": None, "content": None}

    bulk_re = r"전체|모두|전부|일괄|다(?=\s|건|승인|취소|반려|$)"
    if re.search(bulk_re, remaining):
        result["bulk"] = True
        remaining = re.sub(bulk_re, " ", remaining)

    m = re.search(r"(\d+)\s*만\s*원?\s*(이상|이하)?", remaining)
    if m:
        result["amount"] = int(m.group(1)) * 10000
        result["amountOp"] = m.group(2)
        remaining = remaining.replace(m.group(0), " ", 1)
    else:
        m = re.search(r"(\d[\d,]{2,})\s*원\s*(이상|이하)?", remaining)
        if m:
            result["amount"] = int(m.group(1).replace(",", ""))
            result["amountOp"] = m.group(2)
            remaining = remaining.replace(m.group(0), " ", 1)

    # 품의번호(ID): "번" 표시를 지운 뒤 남은 숫자 그룹을 모두 추출 (콤마/공백 다중 ID 지원)
    remaining = remaining.replace("번", " ")
    id_nums = re.findall(r"\d{3,7}", remaining)
    if id_nums:
        result["ids"] = [int(n) for n in id_nums]
        remaining = re.sub(r"\d{3,7}", " ", remaining)

    dept_hit = next((name for name in DEPT_NAMES if name in remaining), None)
    if dept_hit:
        result["dept"] = dept_hit
        remaining = remaining.replace(dept_hit, " ", 1)

    remaining = re.sub(r"미승인|결재대기|건(?=\s|$)", "", remaining)
    remaining = re.sub(r"승인취소|승인\s*처리|승인해\s*줘|승인해|결재해\s*줘|결재해|승인|결재", "", remaining)
    remaining = re.sub(r"취소해\s*줘|취소해|반려해\s*줘|반려해|반려|취소", "", remaining)
    remaining = re.sub(r"이상|이하", "", remaining)
    remaining = re.sub(r"[,，]", " ", remaining)
    remaining = re.sub(r"\s+", " ", remaining).strip()

    if remaining:
        result["content"] = remaining
    return result


def find_approval_targets(parsed):
    candidates = [d for d in st.session_state.dataset if d["status"] == "미승인"]

    if parsed["ids"]:
        candidates = [d for d in candidates if d["id"] in parsed["ids"]]
    if parsed["dept"]:
        candidates = [d for d in candidates if parsed["dept"] in d["dept"]]
    if parsed["amount"] is not None:
        if parsed["amountOp"] == "이상":
            candidates = [d for d in candidates if d["amount"] >= parsed["amount"]]
        elif parsed["amountOp"] == "이하":
            candidates = [d for d in candidates if d["amount"] <= parsed["amount"]]
        else:
            candidates = [d for d in candidates if d["amount"] == parsed["amount"]]
    if parsed["content"]:
        candidates = [
            d for d in candidates
            if store_matches(d["title"], parsed["content"]) or store_matches(d["account"], parsed["content"])
        ]

    return candidates


def handle_approval_command(text, action_type):
    parsed = parse_approval_target_query(text)
    action_label = "승인" if action_type == "approve" else "취소(반려)"

    matches = find_approval_targets(parsed)

    if not matches:
        add_message("system", "조건에 맞는 미승인 건을 찾지 못했습니다. (이미 승인 완료된 건은 대상이 되지 않습니다.)")
        return

    explicit_target = parsed["bulk"] or bool(parsed["ids"])
    if not explicit_target and len(matches) > 1:
        # "승인해줘"처럼 조건 없이 요청한 경우도 포함해, 대상이 여러 건이면 목록을 보여주고 번호로 고르게 한다
        # (카드결제 후보 목록 선택(AWAITING_CARD_SELECTION)과 동일한 방식).
        shown = matches[:8]
        lst = "<br>".join(
            f'{i}. [{d["id"]}] {d["dept"]} · {d["account"]} · {d["amount"]:,}원'
            for i, d in enumerate(shown, start=1)
        )
        st.session_state.pending_approval_selection = {"action_type": action_type, "matches": shown}
        st.session_state.chatbot_step = "AWAITING_APPROVAL_SELECTION"
        more_note = f' (최근 {len(shown)}건만 표시, 총 {len(matches)}건)' if len(matches) > len(shown) else ""
        add_message(
            "system",
            f'현재 미승인 건이 {len(matches)}건입니다{more_note}. 번호를 입력해 {action_label}할 건을 선택해주세요 '
            f'(예: "1" 또는 "1, 3"). 품의번호를 직접 지정하거나(예: "1001번 {action_label}") "전체"를 포함해 '
            f'다시 요청하셔도 됩니다.<br>{lst}',
        )
        return

    lst = " / ".join(f'[{d["id"]}] {d["dept"]} · {d["account"]} · {d["amount"]:,}원' for d in matches)
    st.session_state.pending_approval_action = {"type": action_type, "ids": [d["id"] for d in matches]}
    st.session_state.chatbot_step = "APPROVAL_CONFIRMATION"

    msg = f'다음 {len(matches)}건을 {action_label} 처리할까요?<br>{lst}'
    if len(matches) > 1:
        total = sum(d["amount"] for d in matches)
        msg += f'<br>(합계 {total:,}원)'
    add_message("bot", msg)


def execute_approval_action():
    action = st.session_state.pending_approval_action
    if not action:
        return

    target_ids = [
        i for i in action["ids"]
        if any(d["id"] == i and d["status"] == "미승인" for d in st.session_state.dataset)
    ]
    if not target_ids:
        add_message("system", "대상 건이 이미 처리되었거나 존재하지 않아 실행할 수 없습니다.")
        st.session_state.chatbot_step = "IDLE"
        st.session_state.pending_approval_action = None
        return

    if action["type"] == "approve":
        approved = approve_items(target_ids)
        ids_text = ", ".join(str(d["id"]) for d in approved)
        add_message("bot", f'✅ {len(approved)}건 승인 처리를 완료했습니다. (품의번호: {ids_text})')
    else:
        cancelled = cancel_items(target_ids)
        ids_text = ", ".join(str(d["id"]) for d in cancelled)
        add_message("bot", f'🗑 {len(cancelled)}건 승인취소(반려) 처리를 완료했습니다. (품의번호: {ids_text})')

    st.session_state.chatbot_step = "IDLE"
    st.session_state.pending_approval_action = None


# ---------------------------------------------------------------------------
# 챗봇 대화 상태 머신
# ---------------------------------------------------------------------------

def add_message(role, content):
    st.session_state.chat_messages.append({"role": role, "content": content})


def analyze_and_recommend(tx):
    add_message(
        "system",
        f'[알림] 법인카드 승인 내역이 연동되었습니다.<br>'
        f'<b>💳 {tx["cardNumber"]} ({tx["holder"]})</b><br>'
        f'<b>📍 {tx["store"]}</b> ({tx["location"]})<br>'
        f'<b>💰 {tx["amount"]:,}원</b> · {tx["date"].replace("-", "/")} {tx["time"]}',
    )

    location_ok = tx["location"] == "여의도"
    time_ok = time_to_minutes(tx["time"]) <= 20 * 60
    eligible = location_ok and time_ok

    account_value = MEETING_VALUE if eligible else ORG_ACTIVATION_VALUE
    budget = next(b for b in BUDGET_CODES if b["value"] == account_value)
    remains = get_budget_remains(account_value)
    promo_remains = get_budget_remains(WORK_PROMOTION_VALUE)
    requires_minutes = eligible and tx["amount"] >= 100000

    html = f'📌 추천 적요: <b>{budget["name"]}</b> · 잔여예산 {remains:,}원'
    if tx["amount"] > remains:
        shortfall = tx["amount"] - max(remains, 0)
        html += (
            f'<br>🚨 <b>예산 분기 적용:</b> 잔액 부족으로 부족분 {shortfall:,}원은 '
            f'[본사업무추진비(잔여 {promo_remains:,}원)]에서 자동 처리됩니다.'
        )
    if not eligible:
        reasons = []
        if not location_ok:
            reasons.append("여의도 외 지역")
        if not time_ok:
            reasons.append("오후 8시 이후 사용")
        html += f'<br>💡 {" / ".join(reasons)}으로 회의비 적용이 불가하여 [조직활성화비]로 분류합니다.'
    if requires_minutes:
        html += '<br>⚠️ 10만 원 이상 회의비 건이므로 품의 완료 후 [회의록 PDF] 업로드가 별도로 필요합니다.'

    st.session_state.recommended_proposal = {"accountValue": account_value, "requiresMinutes": requires_minutes}
    # 이전 건에서 올렸던 회의록 PDF가 이번 새 건에 그대로 남아 잘못 재사용되지 않도록 초기화한다.
    st.session_state.chat_meeting_minutes_file = None

    add_message(
        "bot",
        f'🤖 <b>AI 분석 결과:</b><br>{html}<br><br>'
        f'이 적요가 마음에 안 들면 <b>"회의비로 바꿔줘"</b>처럼 말씀해주세요.<br>'
        f'이 내역으로 지출 품의 작성을 시작할까요?<br>'
        f'<b>아래 참석자 / 사유 입력칸에 채워서 제출해주세요.</b>',
    )
    st.session_state.chatbot_step = "AWAITING_ATTENDEES"


def handle_card_command(parsed):
    result = find_card_transaction_by_query(parsed)

    if result.get("error") == "no-card":
        add_message(
            "system",
            f'현재 선택된 품의부서({result["deptName"]})에는 등록된 법인카드가 없습니다. '
            f'상단에서 카드를 보유한 부서를 먼저 선택해 주세요.',
        )
        return
    if result.get("error") == "not-found":
        add_message(
            "system",
            '조건에 맞는 카드 사용내역을 찾지 못했습니다. 날짜/시간/상호명/금액을 다시 확인해 주시거나, '
            '왼쪽 "💳 가상 카드결제 발생" 버튼을 눌러 보세요.',
        )
        return
    if result.get("error") == "ambiguous":
        lst = "<br>".join(
            f'- {c["date"].replace("-", "/")} {c["time"]} · {c["store"]} · {c["amount"]:,}원'
            for c in result["matches"][:5]
        )
        add_message(
            "system",
            f'조건에 맞는 내역이 {len(result["matches"])}건이라 특정할 수 없습니다. 날짜·시간·상호명·금액을 '
            f'함께 입력해 한 건으로 좁혀주세요.<br>{lst}',
        )
        return
    if result.get("error") == "select":
        list_card_candidates_for_selection(result["deptValue"], result["deptName"], result["candidates"])
        return

    st.session_state.pending_transaction = result["tx"]
    analyze_and_recommend(result["tx"])


def list_card_candidates_for_selection(dept_value, dept_name, candidates):
    """조건 없이 '품의하겠다'처럼만 요청했을 때, 무작위 선택 대신 후보 목록을 보여주고 번호로 고르게 한다."""
    shown = candidates[:10]
    lst = "<br>".join(
        f'{i}. {c["date"].replace("-", "/")} {c["time"]} · {c["store"]} · {c["amount"]:,}원'
        for i, c in enumerate(shown, start=1)
    )
    st.session_state.pending_selection = {"deptValue": dept_value, "deptName": dept_name, "candidates": shown}
    more_note = f' (최근 {len(shown)}건만 표시, 총 {len(candidates)}건)' if len(candidates) > len(shown) else ""
    add_message(
        "bot",
        f'품의하실 카드결제 내역을 선택해주세요{more_note}:<br>{lst}<br><br>번호를 입력해주세요. (예: <b>"1"</b> 또는 <b>"1번"</b>)',
    )
    st.session_state.chatbot_step = "AWAITING_CARD_SELECTION"


def trigger_virtual_card_usage():
    dept_value = st.session_state.dept_code
    dept_name, cards, candidates = collect_dept_card_usage(dept_value)

    if not cards:
        add_message(
            "system",
            f'현재 선택된 품의부서({dept_name})에는 등록된 법인카드가 없습니다. '
            f'상단에서 카드를 보유한 부서를 먼저 선택해 주세요.',
        )
        return
    if not candidates:
        add_message("system", f'현재 선택된 품의부서({dept_name})는 남은 카드 사용내역이 없습니다.')
        return

    picked = random.choice(candidates)
    st.session_state.pending_transaction = build_tx_from_candidate(picked, dept_value, dept_name)
    analyze_and_recommend(st.session_state.pending_transaction)


def try_handle_account_change(text):
    if not re.search(r"바꿔|변경|말고|대신", text):
        return False

    matched = find_budget_code_mention(text)
    if not matched:
        return False

    prop = st.session_state.recommended_proposal
    tx = st.session_state.pending_transaction
    if not prop or not tx:
        return False

    prop["accountValue"] = matched["value"]
    prop["requiresMinutes"] = matched["value"] == MEETING_VALUE and tx["amount"] >= 100000

    remains = get_budget_remains(matched["value"])
    add_message("system", f'✅ 적요를 <b>{matched["name"]}({matched["code"]})</b>로 변경했습니다. (잔여예산 {remains:,}원)')

    if st.session_state.chatbot_step == "CONFIRMATION" and prop.get("attendeeLabel") is not None:
        prop["purpose"] = build_purpose_for_account(prop["accountValue"], prop["attendeeLabel"], prop["topic"])
        add_message(
            "bot",
            f'📝 사유: {prop["purpose"]}<br>이대로 최종 품의 상신을 올릴까요? '
            f'(<b>\'응\'</b> 또는 <b>\'확인\'</b> 입력)',
        )
        st.session_state.chatbot_step = "CONFIRMATION"
    else:
        add_message(
            "bot",
            '아래 참석자 / 사유 입력칸에 채워서 제출해주세요.',
        )
        st.session_state.chatbot_step = "AWAITING_ATTENDEES"
    return True


def handle_budget_query(text):
    matched = find_budget_code_mention(text)

    if matched:
        used = get_budget_used(matched["value"])
        remains = get_budget_remains(matched["value"])
        add_message(
            "bot",
            f'💰 <b>{matched["name"]}({matched["code"]})</b><br>'
            f'배정액: {matched["allocated"]:,}원 · 사용: {used:,}원 · <b>잔여: {remains:,}원</b>',
        )
        return

    lines = []
    for b in BUDGET_CODES:
        remains = get_budget_remains(b["value"])
        lines.append(f'- {b["name"]}({b["code"]}): 잔여 {remains:,}원 / 배정 {b["allocated"]:,}원')
    add_message("bot", f'💰 <b>현재 예산 잔액 현황</b><br>{"<br>".join(lines)}')


def finish_new_document_flow(entries):
    tx = st.session_state.pending_transaction
    prop = st.session_state.recommended_proposal
    created = finalize_document_submission(tx["deptName"], st.session_state.doc_date.isoformat(), entries, tx=tx, prop=prop)
    ids_text = ", ".join(str(c["id"]) for c in created)
    add_message(
        "bot",
        f'🎉 <b>품의 상신이 완료되었습니다!</b><br>결재 대기 번호: <b>{ids_text}</b><br>'
        f'[2. 지출품의 승인] 탭에서 확인할 수 있습니다.',
    )
    st.session_state.chatbot_step = "IDLE"
    st.session_state.pending_transaction = None
    st.session_state.recommended_proposal = None


def send_chat_message(text):
    text = text.strip()
    if not text:
        return
    add_message("user", text)

    if is_budget_query(text):
        handle_budget_query(text)
        return

    if st.session_state.chatbot_step != "IDLE" and try_handle_account_change(text):
        return

    step = st.session_state.chatbot_step

    if step == "IDLE":
        if is_cancel_command(text):
            handle_approval_command(text, "cancel")
        elif is_approval_command(text):
            handle_approval_command(text, "approve")
        elif is_card_command(text):
            handle_card_command(parse_transaction_query(text))
        elif re.search(r"품의|결재|상신", text):
            # 품의 관련 핵심 명사는 있지만 구체적인 흐름으로 특정할 수 없는 애매한 입력 — 추측 대신 확인 질문
            add_message(
                "bot",
                '품의 작성을 도와드릴까요? 날짜·시간·상호명·금액을 함께 말씀해주시면 바로 진행할게요. '
                '(예: "1월 5일 19:15 스타벅스 88000원 품의해줘")',
            )
        else:
            add_message(
                "system",
                '💬 "1월 5일 19:15 스타벅스 88000원 품의해줘"처럼 날짜·시간·상호명·금액을 함께 말씀해주시거나, '
                '왼쪽 "💳 가상 카드결제 발생" 버튼을 눌러보세요. 결재 대기 중인 건은 "1001번 승인해줘"처럼 '
                '요청하시면 처리해드려요.',
            )

    elif step == "AWAITING_CARD_SELECTION":
        sel = st.session_state.pending_selection
        candidates = sel["candidates"] if sel else []
        m = re.search(r"\d+", text)
        idx = int(m.group()) - 1 if m else -1

        if not sel or idx < 0 or idx >= len(candidates):
            add_message("system", f'목록에 있는 번호(1~{len(candidates)})로 다시 선택해 주세요.')
        else:
            picked = candidates[idx]
            tx = build_tx_from_candidate(picked, sel["deptValue"], sel["deptName"])
            st.session_state.pending_selection = None
            st.session_state.pending_transaction = tx
            analyze_and_recommend(tx)

    elif step == "AWAITING_APPROVAL_SELECTION":
        sel = st.session_state.pending_approval_selection
        candidates = sel["matches"] if sel else []
        nums = [int(n) for n in re.findall(r"\d+", text)]
        idxs = sorted({n - 1 for n in nums if 1 <= n <= len(candidates)})

        if not sel or not idxs:
            add_message("system", f'목록에 있는 번호(1~{len(candidates)})로 다시 선택해 주세요. (예: "1" 또는 "1, 3")')
        else:
            chosen = [candidates[i] for i in idxs]
            action_type = sel["action_type"]
            action_label = "승인" if action_type == "approve" else "취소(반려)"
            lst = " / ".join(f'[{d["id"]}] {d["dept"]} · {d["account"]} · {d["amount"]:,}원' for d in chosen)
            st.session_state.pending_approval_action = {"type": action_type, "ids": [d["id"] for d in chosen]}
            st.session_state.pending_approval_selection = None
            st.session_state.chatbot_step = "APPROVAL_CONFIRMATION"

            msg = f'다음 {len(chosen)}건을 {action_label} 처리할까요?<br>{lst}'
            if len(chosen) > 1:
                total = sum(d["amount"] for d in chosen)
                msg += f'<br>(합계 {total:,}원)'
            add_message("bot", msg)

    elif step == "AWAITING_ATTENDEES":
        tx = st.session_state.pending_transaction
        prop = st.session_state.recommended_proposal
        budget = next(b for b in BUDGET_CODES if b["value"] == prop["accountValue"])

        attendee_text, topic = parse_attendee_input(text)
        prop["attendeeLabel"] = build_attendee_label(attendee_text)
        prop["topic"] = topic
        prop["purpose"] = build_purpose_for_account(prop["accountValue"], prop["attendeeLabel"], prop["topic"])

        add_message(
            "bot",
            f'📝 <b>상세 품의안 작성이 완료되었습니다.</b><br>'
            f'- 부서: {tx["deptName"]}<br>'
            f'- 계정: {budget["name"]}<br>'
            f'- 사유: {prop["purpose"]}<br>'
            f'- 금액: {tx["amount"]:,}원<br><br>'
            f'이대로 최종 품의 상신을 올릴까요? (<b>\'응\'</b> 또는 <b>\'확인\'</b> 입력)',
        )
        st.session_state.chatbot_step = "CONFIRMATION"

    elif step == "CONFIRMATION":
        if re.search(POSITIVE_RE, text, re.IGNORECASE):
            tx = st.session_state.pending_transaction
            prop = st.session_state.recommended_proposal

            valid, _ = validate_doc_date(st.session_state.doc_date)
            if not valid:
                add_message(
                    "system",
                    "품의일자 조건(월/수/금, 23일 제외)을 만족하지 않아 상신할 수 없습니다. "
                    "상단 [품의일자]를 먼저 수정한 뒤 다시 시도해 주세요.",
                )
                st.session_state.chatbot_step = "IDLE"
                return

            if prop.get("requiresMinutes") and st.session_state.get("chat_meeting_minutes_file") is None:
                add_message(
                    "system",
                    "10만원 이상 본사회의비는 회의록 PDF 파일을 업로드해야 합니다. "
                    "아래 업로드칸에 파일을 첨부한 뒤 다시 '응'이라고 답해주세요.",
                )
                return

            status, entries, message = build_ledger_entries(prop["accountValue"], tx["amount"])
            if status == "blocked":
                add_message("system", message.replace("\n", "<br>"))
                st.session_state.chatbot_step = "IDLE"
                return
            if status == "confirm":
                st.session_state.pending_shortfall = {"entries": entries}
                add_message(
                    "system",
                    message.replace("\n", "<br>") + '<br>(\'응\' 또는 \'확인\'으로 답해주세요)',
                )
                st.session_state.chatbot_step = "SHORTFALL_CONFIRMATION"
                return

            finish_new_document_flow(entries)
        else:
            add_message("bot", "취소되었습니다. 처음부터 다시 시작하려면 다시 요청해 주세요.")
            st.session_state.chatbot_step = "IDLE"
            st.session_state.pending_transaction = None
            st.session_state.recommended_proposal = None

    elif step == "SHORTFALL_CONFIRMATION":
        if re.search(POSITIVE_RE, text, re.IGNORECASE):
            entries = st.session_state.pending_shortfall["entries"]
            finish_new_document_flow(entries)
            st.session_state.pending_shortfall = None
        else:
            add_message("system", "예산 부족 등의 사유로 상신이 취소되었습니다.")
            st.session_state.chatbot_step = "IDLE"
            st.session_state.pending_transaction = None
            st.session_state.recommended_proposal = None
            st.session_state.pending_shortfall = None

    elif step == "APPROVAL_CONFIRMATION":
        if re.search(POSITIVE_RE, text, re.IGNORECASE):
            execute_approval_action()
        else:
            add_message("bot", "처리를 취소했습니다.")
            st.session_state.chatbot_step = "IDLE"
            st.session_state.pending_approval_action = None


# ---------------------------------------------------------------------------
# UI 테마: 라이트 모드 + 인디고 하이라이트 (PRD §8). 색상 팔레트 자체는
# .streamlit/config.toml에서 지정하고, 여기서는 config.toml만으로 표현 안 되는
# 세부 스타일(버튼 라운딩, 챗봇 상태 배지/점멸 인디케이터)만 CSS로 보충한다.
# ---------------------------------------------------------------------------

CUSTOM_CSS = """
<style>
html, body, [class^="css"], [class*=" css"] {
    font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}

div.stButton > button {
    border-radius: 8px;
    font-weight: 600;
    transition: filter 0.15s ease, transform 0.15s ease, box-shadow 0.15s ease;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
}
div.stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 10px rgba(15, 23, 42, 0.08);
}
div.stButton > button[kind="primary"] {
    background-color: #6366F1;
    border-color: #6366F1;
    box-shadow: 0 2px 8px rgba(99, 102, 241, 0.35);
}
div.stButton > button[kind="primary"]:hover {
    filter: brightness(1.12);
    box-shadow: 0 4px 14px rgba(99, 102, 241, 0.45);
}

/* st.container(border=True) 카드 모서리를 둥글고 은은한 그림자로 입체감 부여 */
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 12px !important;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
    transition: box-shadow 0.15s ease;
}

/* 채팅 메시지 말풍선도 같은 톤으로 둥글고 은은한 그림자 부여 */
div[data-testid="stChatMessage"] {
    border-radius: 14px;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
}

/* 텍스트 입력칸도 버튼/카드와 통일감 있게 라운딩 */
div[data-testid="stTextInput"] input,
div[data-testid="stChatInput"] textarea {
    border-radius: 10px !important;
}

/* 탭 하이라이트 밑줄을 인디고 톤으로 (기본 빨강 대신) */
button[data-baseweb="tab"][aria-selected="true"] {
    color: #6366F1 !important;
}
div[data-baseweb="tab-highlight"] {
    background-color: #6366F1 !important;
}

/* 사이드바 기본 상단 여백을 줄여 AI 에이전트 배너를 맨 위로 붙임 */
section[data-testid="stSidebar"] div[data-testid="stSidebarContent"],
section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] {
    padding-top: 0.5rem;
}

/* 사이드바 접기(<<) 버튼 영역의 과도한 여백 축소 (기본 height/margin-bottom을 강제 override) */
section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] {
    height: auto !important;
    min-height: 0 !important;
    margin-bottom: 0 !important;
    padding: 0 !important;
}

/* 우측 상단 Deploy 툴바 영역(메인 헤더 바)도 같은 방식으로 축소 */
header[data-testid="stHeader"] {
    height: 2rem !important;
    min-height: 2rem !important;
}
div[data-testid="stToolbar"] {
    padding-top: 0 !important;
    padding-bottom: 0 !important;
}

/* 메인 콘텐츠 기본 상단 여백(8rem)도 줄여 [업무 지침] 배너를 위로 붙임 */
div[data-testid="stMainBlockContainer"] {
    padding-top: 2rem !important;
}

/* 챗봇 상태 배지 + 애니메이션 점멸 인디케이터 (PRD §8.4) */
.chat-status-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 600;
    margin-bottom: 0.6rem;
}
.chat-status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #22C55E;
    position: relative;
    flex-shrink: 0;
}
.chat-status-dot::after {
    content: "";
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: #22C55E;
    animation: chat-pulse 1.6s ease-out infinite;
}
@keyframes chat-pulse {
    0%   { transform: scale(1);   opacity: 0.7; }
    100% { transform: scale(2.6); opacity: 0;   }
}

/* AI 에이전트 배너 - 더 크고 색감있게 강조 */
.ai-agent-banner {
    background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%);
    color: #FFFFFF;
    padding: 6px 10px;
    border-radius: 8px;
    font-size: 0.85rem;
    font-weight: 400;
    text-align: center;
    margin: 4px 0 8px 0;
    box-shadow: 0 3px 10px rgba(99, 102, 241, 0.35);
}
</style>
"""

# chatbot_step -> (배지 라벨, 강조색). CONFIRMATION류는 전부 인디고로 통일해 "확인 대기 중"임을 강조.
STEP_BADGE = {
    "IDLE": ("AI 에이전트 대기 중", "#22C55E"),
    "AWAITING_ATTENDEES": ("참석자 정보 입력 대기", "#F59E0B"),
    "CONFIRMATION": ("최종 확인 대기", "#6366F1"),
    "SHORTFALL_CONFIRMATION": ("예산분할 확인 대기", "#6366F1"),
    "APPROVAL_CONFIRMATION": ("승인/취소 확인 대기", "#6366F1"),
}


def render_chat_status_badge():
    label, color = STEP_BADGE.get(st.session_state.chatbot_step, STEP_BADGE["IDLE"])
    st.markdown(
        f'<div class="chat-status-badge" style="background:{color}22; color:{color}; '
        f'border:1px solid {color}66;"><span class="chat-status-dot"></span>{label}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Streamlit 페이지 설정 및 세션 상태 초기화
# ---------------------------------------------------------------------------

st.set_page_config(page_title="사내 지출결의/관리 시스템 (데모)", page_icon="📊", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

if "initialized" not in st.session_state:
    _data = load_data()
    st.session_state.dataset = _data["dataset"]
    st.session_state.card_usage_cache = _data["cardUsageCache"]
    st.session_state.used_card_usage_keys = set(_data["usedCardUsageKeys"])

    st.session_state.chatbot_step = "IDLE"
    st.session_state.pending_transaction = None
    st.session_state.recommended_proposal = None
    st.session_state.pending_approval_action = None
    st.session_state.pending_approval_selection = None
    st.session_state.pending_selection = None
    st.session_state.pending_shortfall = None
    st.session_state.pending_manual_submit = None
    st.session_state.chat_meeting_minutes_file = None
    st.session_state.chat_messages = [{"role": "bot", "content": GREETING}]

    st.session_state.dept_code = "C30 IT정보팀"
    st.session_state.doc_date = date.today()
    st.session_state.evidence_type = "3.법인카드 (사용내역 선택)"
    st.session_state.branch_code = "010 본지점"
    st.session_state.dr_account = BUDGET_CODES[0]["value"]
    st.session_state.dr_amount = 0
    st.session_state.doc_summary = ""
    st.session_state.payee = ""
    st.session_state._last_selection_key = None

    st.session_state.initialized = True


def on_evidence_change():
    """toggleCardSection()의 일반영수증 분기 - 데모용 예시값 자동 채움."""
    if st.session_state.evidence_type.startswith("0."):
        st.session_state.dr_account = "12CC (일반)전화료"
        st.session_state.dr_amount = 89000
        st.session_state.doc_summary = "부서장 통신비 지원금 (LG유플러스)"
        st.session_state.payee = "010 법인업무지원부"


# ---------------------------------------------------------------------------
# 메인: 업무 지침 배너 + 3개 탭
# ---------------------------------------------------------------------------

with st.sidebar:
    st.info("📌 [업무 지침] 월·수·금만 지출처리 가능 (단, 23일은 지출처리 불가)")
    if not ENFORCE_DOC_DATE_RULE:
        st.caption("※ 현재 ENFORCE_DOC_DATE_RULE = False (테스트 프리셋) — 품의일자 요일 제한이 비활성화되어 있습니다.")

    tab1, tab2, tab3 = st.tabs(["1. 지출결의 입력", "4. 지출품의 승인 (부서장)", "5·6. 승인현황 / 예산내역"])

    # --- Tab 1: 지출결의 입력 -----------------------------------------------------
    with tab1:
        st.subheader("[5221] 지출품의 입력")
        st.caption("품의번호: 자동발번")

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            dept_value = st.selectbox("품의부서", list(DEPT_CARDS.keys()), key="dept_code")
        with c2:
            doc_date = st.date_input("품의일자 / 지급일자", key="doc_date")
            valid, is23 = validate_doc_date(doc_date)
            if not valid:
                st.error("⚠ 23일은 지출처리가 불가능한 날짜입니다." if is23 else "⚠ 월·수·금만 처리 가능합니다.")
        with c3:
            evidence_type = st.selectbox(
                "증빙구분",
                ["3.법인카드 (사용내역 선택)", "0.일반영수증 (전화료 등)"],
                key="evidence_type",
                on_change=on_evidence_change,
            )
        with c4:
            st.text_input("격지지점", key="branch_code")

        is_card_evidence = evidence_type.startswith("3.")
        dept_name = " ".join(dept_value.split(" ")[1:])

        source_usage_keys = []
        meeting_eligible = False
        requires_minutes = False

        with st.container(border=True):
            if is_card_evidence:
                st.markdown("**💳 1-1. 부서 보유 법인카드 목록 / 2. 사용내역 조회**")
                cards = DEPT_CARDS.get(dept_value, [])

                if not cards:
                    st.info("이 부서는 보유한 법인카드가 없습니다.")
                else:
                    card_labels = [f'{c["number"]} ({c["holder"]})' for c in cards]
                    selected_label = st.radio("카드 선택", card_labels, key=f"card_select_{dept_value}", horizontal=True)
                    selected_card = cards[card_labels.index(selected_label)]

                    items = [
                        it for it in random_usage_for_card(selected_card["number"])
                        if card_usage_key(selected_card["number"], it) not in st.session_state.used_card_usage_keys
                    ]

                    if not items:
                        st.info("이미 품의 처리된 카드입니다 (남은 사용내역 없음).")
                    else:
                        table_rows = []
                        for it in items:
                            table_rows.append({
                                "선택": False,
                                "승인일": f'{it["date"].replace("-", "/")} {it["time"]}',
                                "가맹점명": it["store"],
                                "승인금액": it["amount"],
                                "매입여부": "매입",
                            })
                        usage_df = pd.DataFrame(table_rows)

                        edited = st.data_editor(
                            usage_df,
                            hide_index=True,
                            width='stretch',
                            disabled=["승인일", "가맹점명", "승인금액", "매입여부"],
                            key=f"usage_table_{selected_card['number']}",
                        )

                        selected_idx = edited.index[edited["선택"] == True].tolist()
                        selected_sum = int(edited.loc[selected_idx, "승인금액"].sum()) if selected_idx else 0
                        selected_items = [items[i] for i in selected_idx]
                        source_usage_keys = [card_usage_key(selected_card["number"], it) for it in selected_items]

                        selection_key = tuple(sorted(source_usage_keys))

                        if selected_sum > 0:
                            location_ok = all(it["location"] == "여의도" for it in selected_items)
                            time_ok = all(time_to_minutes(it["time"]) <= 20 * 60 for it in selected_items)
                            meeting_eligible = location_ok and time_ok

                            recommended_account = MEETING_VALUE if meeting_eligible else ORG_ACTIVATION_VALUE
                            requires_minutes = meeting_eligible and selected_sum >= 100000

                            # 체크된 카드결제 건이 바뀔 때만 추천값으로 덮어써서, 이후 사용자가 직접 바꾼 값은 유지한다
                            if st.session_state._last_selection_key != selection_key:
                                st.session_state.dr_account = recommended_account
                                st.session_state.dr_amount = selected_sum
                                st.session_state._last_selection_key = selection_key

                            remains = get_budget_remains(recommended_account)
                            promo_remains = get_budget_remains(WORK_PROMOTION_VALUE)
                            budget_name = next(b["name"] for b in BUDGET_CODES if b["value"] == recommended_account)

                            msg = f'📌 추천 적요: **{budget_name}** · 잔여예산 {remains:,}원'
                            if selected_sum > remains:
                                shortfall = selected_sum - max(remains, 0)
                                msg += f'\n\n⚠ 예산 부족 — 부족분 {shortfall:,}원은 본사 업무추진비(잔여 {promo_remains:,}원)에서 자동 처리됩니다.'
                            if not meeting_eligible:
                                reasons = []
                                if not location_ok:
                                    reasons.append("여의도 외 지역 사용 건 포함")
                                if not time_ok:
                                    reasons.append("오후 8시 이후 사용 건 포함")
                                msg += f'\n\n{" / ".join(reasons)}으로 회의비 적용이 불가하여 조직활성화비로 추천되었습니다.'
                            st.info(msg)
                            if requires_minutes:
                                st.warning("⚠ 10만원 이상 회의비 처리 — 아래에서 회의록 PDF를 업로드해 주세요.")
            else:
                st.info("일반영수증 처리 — 계정과목/금액/적요/지급처를 직접 입력해 주세요.")

        st.divider()
        with st.container(border=True):
            st.caption("💼 회계 분개")
            cdr, ccr = st.columns(2)
            with cdr:
                st.caption("차변 (정산 비용 과목)")
                account_options = [b["value"] for b in BUDGET_CODES]
                account = st.selectbox("계정과목", account_options, key="dr_account", label_visibility="collapsed")
                amount = st.number_input("금액", key="dr_amount", min_value=0, step=1000, label_visibility="collapsed")
            with ccr:
                st.caption("대변 (지급 방식 결정을 위한 내부 계정)")
                st.text_input(
                    "대변 계정",
                    value="미지급비용 (카드 결제건)" if is_card_evidence else "본지점 (현금/현장지급)",
                    disabled=True,
                    label_visibility="collapsed",
                )

        c_summary, c_payee = st.columns([2, 1])
        with c_summary:
            st.text_input("적요 (상세 내역 기재)", key="doc_summary")
        with c_payee:
            st.text_input("지급처 / 수신자", key="payee", placeholder="사번 또는 가맹점")

        meeting_minutes_file = None
        if requires_minutes:
            meeting_minutes_file = st.file_uploader("⚠ 10만원 이상 회의비 - 회의록 PDF 업로드 (필수)", type=["pdf"])

        if st.button("🚀 지출 결의서 전송 (품의 등록)", type="primary", width='stretch'):
            valid, is23 = validate_doc_date(doc_date)
            if not valid:
                st.error("23일은 지출처리가 불가능한 날짜입니다." if is23 else "지출처리는 월·수·금요일만 가능합니다.")
            elif amount <= 0:
                st.error("결의 금액을 입력하거나 카드를 선택해 주세요.")
            elif account == MEETING_VALUE and not meeting_eligible:
                st.error("본사회의비는 여의도 내에서 오후 8시 이전에 사용한 건만 처리 가능합니다.")
            elif account == MEETING_VALUE and amount >= 100000 and meeting_minutes_file is None:
                st.error("10만원 이상 본사회의비는 회의록 PDF 파일을 업로드해야 합니다.")
            else:
                status, entries, message = build_ledger_entries(account, int(amount))
                summary = st.session_state.doc_summary or "미지정 적요 지출"
                if status == "blocked":
                    st.error(message)
                elif status == "ok":
                    created = finalize_document_submission(
                        dept_name, doc_date.isoformat(), entries, summary=summary,
                    )
                    if source_usage_keys:
                        for k in source_usage_keys:
                            mark_card_usage_used(k)
                        for c in created:
                            c["sourceUsageKeys"] = source_usage_keys
                        save_data()
                    ids_text = ", ".join(str(c["id"]) for c in created)
                    st.success(f"지출 결의서가 정상 등록되었습니다. [결재 대기 번호: {ids_text}] — [4. 지출품의 승인] 탭에서 확인하세요.")
                    st.session_state._last_selection_key = None
                else:  # confirm
                    st.session_state.pending_manual_submit = {
                        "entries": entries,
                        "message": message,
                        "dept_name": dept_name,
                        "doc_date": doc_date.isoformat(),
                        "summary": summary,
                        "source_usage_keys": source_usage_keys,
                    }

        if st.session_state.pending_manual_submit:
            pm = st.session_state.pending_manual_submit
            st.warning(pm["message"])
            pc1, pc2 = st.columns(2)
            if pc1.button("계속 진행", key="manual_shortfall_confirm", width='stretch'):
                created = finalize_document_submission(pm["dept_name"], pm["doc_date"], pm["entries"], summary=pm["summary"])
                if pm["source_usage_keys"]:
                    for k in pm["source_usage_keys"]:
                        mark_card_usage_used(k)
                    for c in created:
                        c["sourceUsageKeys"] = pm["source_usage_keys"]
                    save_data()
                ids_text = ", ".join(str(c["id"]) for c in created)
                st.session_state.pending_manual_submit = None
                st.session_state._last_selection_key = None
                st.success(f"지출 결의서가 정상 등록되었습니다. [결재 대기 번호: {ids_text}]")
                st.rerun()
            if pc2.button("취소", key="manual_shortfall_cancel", width='stretch'):
                st.session_state.pending_manual_submit = None
                st.rerun()

    # --- Tab 2: 지출품의 승인 -----------------------------------------------------
    with tab2:
        st.subheader("[5232] 지출품의 승인 (미승인 -> 승인)")
        st.caption("결재 권한자: IT기획 파트장/부서장 · 체크 후 하단 버튼을 누르면 즉시 처리됩니다.")

        pending_items = [d for d in st.session_state.dataset if d["status"] == "미승인"]

        with st.container(border=True):
            if not pending_items:
                st.info("결재 대기 중인 문서가 없습니다.")
            else:
                approval_df = pd.DataFrame([{
                    "선택": False,
                    "부서명": d["dept"],
                    "품의일": d["date"],
                    "내역": d["title"],
                    "품의금액": d["amount"],
                    "전표번호": d["id"],
                } for d in pending_items])

                edited_approval = st.data_editor(
                    approval_df,
                    hide_index=True,
                    width='stretch',
                    disabled=["부서명", "품의일", "내역", "품의금액", "전표번호"],
                    key="approval_table",
                )
                selected_ids = edited_approval.loc[edited_approval["선택"] == True, "전표번호"].astype(int).tolist()

                ac1, ac2 = st.columns(2)
                if ac1.button("선택 항목 승인취소", width='stretch'):
                    if not selected_ids:
                        st.warning("승인취소 처리할 결의 항목을 선택해 주세요.")
                    else:
                        cancelled = cancel_items(selected_ids)
                        st.success(f"선택하신 {len(cancelled)}건이 승인취소(반려) 처리되었습니다.")
                        st.rerun()
                if ac2.button("선택 항목 일괄 승인 완료", type="primary", width='stretch'):
                    if not selected_ids:
                        st.warning("승인 처리할 결의 항목을 선택해 주세요.")
                    else:
                        approved = approve_items(selected_ids)
                        st.success(f"선택하신 {len(approved)}건의 부서장 결재가 완료되었습니다.")
                        st.rerun()

    # --- Tab 3: 승인현황 / 예산내역 ------------------------------------------------
    with tab3:
        st.subheader("[5307] 지출결의서 승인현황 (재무부 미승인시 전표)")
        with st.container(border=True):
            if st.session_state.dataset:
                status_df = pd.DataFrame([{
                    "번호": d["id"],
                    "결의일자": d["date"],
                    "세절명(적요)": d["title"],
                    "금액": d["amount"],
                    "계정과목": d["account"],
                    "결재상태": "승인완료" if d["status"] == "승인" else "결재 대기",
                } for d in st.session_state.dataset])
                st.dataframe(status_df, hide_index=True, width='stretch')
            else:
                st.info("등록된 지출결의 내역이 없습니다.")

        st.subheader("[5527] 예산사용내역 실적 검토 (적요별 배정/사용/잔액)")
        with st.container(border=True):
            budget_rows = []
            for b in BUDGET_CODES:
                used = get_budget_used(b["value"])
                remains = b["allocated"] - used
                budget_rows.append({
                    "예산코드": b["code"],
                    "적요": b["name"],
                    "초기배정액": b["allocated"],
                    "사용금액": used,
                    "예산 잔액": remains,
                })
            st.dataframe(pd.DataFrame(budget_rows), hide_index=True, width='stretch')


# ---------------------------------------------------------------------------
# 메인 화면 중앙: 브랜딩 / 초기화 / 챗봇 패널 (ERP 탭은 사이드바로 이동했음)
#
# 사이드바 탭(특히 1번 탭의 dept_code/doc_date 위젯)이 먼저 실행된 뒤에 이 블록이 실행되도록
# 일부러 스크립트 맨 아래에 둔다. 이 아래에서 st.rerun()을 호출하는데, 만약 이 블록이 사이드바
# 탭보다 먼저 실행되는 위치에 있으면 rerun이 스크립트를 중간에 끊어버려서 그 실행 동안 tab1의
# 위젯들이 전혀 렌더링되지 않게 되고, Streamlit이 "이번 실행에서 보이지 않은 위젯"으로 판단해
# 해당 session_state 키(dept_code 등)를 지워버려 다음 실행에서 KeyError가 난다. (사이드바/메인은
# 화면상 위치만 다를 뿐, 위젯 생명주기에 영향을 주는 건 어디까지나 스크립트 실행 순서다.)
# ---------------------------------------------------------------------------

_chat_col_left, chat_col_mid, _chat_col_right = st.columns([1, 5, 1])
with chat_col_mid:
    st.markdown('<div class="ai-agent-banner">💡✨ AI 지출결의 에이전트</div>', unsafe_allow_html=True)
    render_chat_status_badge()

    chat_box = st.container(height=460, key="chat_log_box")
    with chat_box:
        for m in st.session_state.chat_messages:
            name = "user" if m["role"] == "user" else "assistant"
            avatar = {"user": None, "bot": "🤖", "system": "📎"}.get(m["role"])
            with st.chat_message(name, avatar=avatar):
                st.markdown(m["content"], unsafe_allow_html=True)

        if st.session_state.chatbot_step == "IDLE":
            budget_col, doc_col, approve_col = st.columns(3)
            if budget_col.button("💰 예산조회", key="quick_budget_btn", width='stretch'):
                send_chat_message("예산 얼마 남았어?")
                st.rerun()
            if doc_col.button("📝 품의", key="quick_doc_btn", width='stretch'):
                send_chat_message("품의하겠다")
                st.rerun()
            if approve_col.button("✅ 승인", key="quick_approve_btn", width='stretch'):
                send_chat_message("승인해줘")
                st.rerun()

        if st.session_state.chatbot_step == "AWAITING_ATTENDEES":
            with st.form("attendee_form", clear_on_submit=True):
                attendee_col, reason_col = st.columns(2)
                with attendee_col:
                    attendee_val = st.text_input("참석자 :", placeholder="예: 김철수, 이영희")
                with reason_col:
                    reason_val = st.text_input("사유 :", placeholder="예: 3분기 마케팅 전략 회의")
                submitted = st.form_submit_button("제출", width='stretch', type="primary")
            if submitted:
                # 입력칸에 이미 라벨이 붙어있는데도 사용자가 "사유 : 회의"처럼 라벨을 중복 입력하는
                # 경우를 대비해, 합치기 전에 중복된 라벨 접두어를 제거한다.
                attendee_val = re.sub(r"^\s*참석자\s*[:：]\s*", "", attendee_val).strip()
                reason_val = re.sub(r"^\s*(?:사유|내용)\s*[:：]\s*", "", reason_val).strip()
                send_chat_message(f"참석자 : {attendee_val}\n사유 : {reason_val}")
                st.rerun()

        if st.session_state.chatbot_step == "CONFIRMATION" and (st.session_state.recommended_proposal or {}).get("requiresMinutes"):
            st.file_uploader(
                "⚠ 10만원 이상 회의비 - 회의록 PDF 업로드 (필수)",
                type=["pdf"],
                key="chat_meeting_minutes_file",
            )

        if st.session_state.chatbot_step in ("CONFIRMATION", "SHORTFALL_CONFIRMATION", "APPROVAL_CONFIRMATION"):
            yes_col, no_col = st.columns(2)
            if yes_col.button("✅ 예", key="confirm_yes_btn", width='stretch', type="primary"):
                send_chat_message("응")
                st.rerun()
            if no_col.button("❌ 아니오", key="confirm_no_btn", width='stretch'):
                send_chat_message("아니오")
                st.rerun()

        # 새 메시지가 추가돼도 chat_box(고정 높이 컨테이너)가 자동으로 스크롤되지 않아, 매 렌더링마다
        # 스크롤 위치를 맞춘다 (컴포넌트는 iframe이라 window.parent로 실제 문서에 접근). 맨 아래로
        # 내리면 그 답변 뒤에 이어지는 참석자/사유 폼 등이 화면 밖으로 밀려나 안 보이는 문제가 있어서,
        # "내가 방금 입력한 메시지"가 chat_box 맨 위에 오도록 스크롤한다 — 그 아래로 이어지는 응답/폼이
        # 자연스럽게 이어서 보인다. scrollIntoView()는 페이지 전체 스크롤까지 건드리므로 쓰지 않고,
        # chat_box의 scrollTop만 직접 계산해서 설정한다 (메인 페이지 스크롤 위치는 그대로 둔다).
        components.html(
            """
            <script>
            var box = window.parent.document.querySelector('.st-key-chat_log_box');
            var avatars = window.parent.document.querySelectorAll('[data-testid="stChatMessageAvatarUser"]');
            if (box && avatars.length) {
                var lastUserMsg = avatars[avatars.length - 1].closest('[data-testid="stChatMessage"]');
                if (lastUserMsg) {
                    var offset = lastUserMsg.getBoundingClientRect().top - box.getBoundingClientRect().top;
                    box.scrollTop = box.scrollTop + offset;
                }
            }
            </script>
            """,
            height=0,
        )

    # st.columns 안에서는 st.chat_input이 하단에 고정되지 않고 코드 순서 그대로("inline") 렌더링되므로,
    # 이보다 아래에 둔 요소는 실제로 입력창보다 아래에 나타난다. 개발/테스트용 버튼은 일부러
    # 이 아래에 배치해 실제 업무 흐름(챗봇 대화)과 시각적으로 분리한다.
    user_text = st.chat_input("메시지를 입력하세요...")
    if user_text:
        send_chat_message(user_text)
        st.rerun()

    st.divider()
    st.caption("🔧 테스트 도구 (개발용)")

    if st.button("💳 가상 카드결제 발생", width='stretch', help="현재 선택된 품의부서의 카드 중 하나로 결제를 시뮬레이션합니다"):
        trigger_virtual_card_usage()

    if st.button("🗑 테스트 데이터 초기화", width='stretch'):
        st.session_state.confirm_reset_pending = True

    if st.session_state.get("confirm_reset_pending"):
        st.warning("저장된 모든 테스트 데이터(품의/승인 내역 + 카드 사용내역)를 초기화할까요?")
        rc1, rc2 = st.columns(2)
        if rc1.button("초기화 확정", width='stretch'):
            reset_test_data()
        if rc2.button("취소", key="cancel_reset_btn", width='stretch'):
            st.session_state.confirm_reset_pending = False
            st.rerun()
