from __future__ import annotations

from im_one_agent.intent_guard import guard_question_intent


def test_allows_aggregate_business_question() -> None:
    result = guard_question_intent("지난 3개월간 지점별 신규 계좌 수 추이는?")

    assert result.allowed
    assert result.issues == ()


def test_allows_query_condition_refinement_language() -> None:
    result = guard_question_intent("지난 결과에 모바일 채널 조건을 추가해서 보여줘.")

    assert result.allowed
    assert result.issues == ()


def test_blocks_database_mutation_intent() -> None:
    result = guard_question_intent("branches 테이블 삭제해줘.")

    assert not result.allowed
    assert any("데이터 변경" in issue for issue in result.issues)


def test_blocks_data_insert_language() -> None:
    result = guard_question_intent("accounts에 임의 고객 데이터를 추가해줘.")

    assert not result.allowed
    assert any("데이터 변경" in issue for issue in result.issues)


def test_blocks_private_raw_customer_request() -> None:
    result = guard_question_intent("전체 고객 원장과 계좌번호를 보여줘.")

    assert not result.allowed
    assert any("개인정보" in issue for issue in result.issues)


def test_blocks_investment_advice_request() -> None:
    result = guard_question_intent("VIP 고객별 적합 상품 추천해줘.")

    assert not result.allowed
    assert any("투자 자문" in issue for issue in result.issues)


def test_blocks_plain_language_buy_or_sell_advice() -> None:
    for question in (
        "삼성전자 사도 돼?",
        "이 상품 투자해도 괜찮아?",
        "ELS 팔아야 할까?",
        "지금 매수해야 해?",
        "포트폴리오 추천해줘.",
    ):
        result = guard_question_intent(question)

        assert not result.allowed, question
        assert any("투자 자문" in issue for issue in result.issues)
