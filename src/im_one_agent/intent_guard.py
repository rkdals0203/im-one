from __future__ import annotations

import re
from dataclasses import dataclass


MUTATION_INTENT_PATTERN = re.compile(
    r"("
    r"삭제|지워|드랍|drop|delete|truncate|insert|update|alter|create|attach|detach|pragma|vacuum|"
    r"(?:테이블|데이터|행|레코드|고객|계좌|accounts|branches)\s*(?:을|를|에)?\s*(?:추가|수정|변경)|"
    r"(?:추가|수정|변경)(?:해|하되|해서)?\s*(?:줘|주세요)?\s*(?:테이블|데이터|행|레코드)"
    r")",
    re.IGNORECASE,
)
PRIVATE_DETAIL_PATTERN = re.compile(
    r"(계좌번호|주민등록|전화번호|휴대폰|주소|고객명|고객\s*id|customer\s*id|원장|전체\s*고객|고객별\s*전체|row[-\s]*level|원천\s*데이터)",
    re.IGNORECASE,
)
RAW_EXPORT_PATTERN = re.compile(
    r"(select\s+\*|모든\s*원천|전체\s*원천|raw\s*data|dump|덤프)",
    re.IGNORECASE,
)
INVESTMENT_ADVICE_PATTERN = re.compile(
    r"("
    r"매수\s*추천|매도\s*추천|종목\s*추천|투자\s*자문|수익률\s*보장|"
    r"고객별\s*추천|적합\s*상품\s*추천|포트폴리오\s*추천|상품\s*추천|"
    r"어떤\s*(?:종목|상품).*(?:사|매수|가입|투자)|"
    r"(?:사도|매수해도|투자해도|가입해도)\s*(?:돼|될까|괜찮|좋)|"
    r"(?:팔아야|매도해야|손절해야|익절해야)\s*(?:해|할까|돼)|"
    r"(?:사야|매수해야|투자해야|가입해야)\s*(?:해|할까|돼)"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IntentGuardResult:
    allowed: bool
    issues: tuple[str, ...] = ()


def guard_question_intent(question: str) -> IntentGuardResult:
    normalized = question.strip()
    issues: list[str] = []

    if MUTATION_INTENT_PATTERN.search(normalized):
        issues.append("데이터 변경, 운영 명령, DB 관리 명령은 실행할 수 없습니다.")

    if PRIVATE_DETAIL_PATTERN.search(normalized):
        issues.append("개인정보 또는 고객 단위 원장/상세 데이터 요청은 집계 조회로 전환해야 합니다.")

    if RAW_EXPORT_PATTERN.search(normalized):
        issues.append("원천 데이터 전체 조회 또는 SELECT * 요청은 허용되지 않습니다.")

    if INVESTMENT_ADVICE_PATTERN.search(normalized):
        issues.append("투자 자문, 매수/매도 추천, 고객별 상품 추천은 이 도구의 범위가 아닙니다.")

    return IntentGuardResult(allowed=not issues, issues=tuple(issues))
