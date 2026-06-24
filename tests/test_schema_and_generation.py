from __future__ import annotations

from im_one_agent.schema_retrieval import retrieve_schema
from im_one_agent.sql_generator import generate_sql


def test_retrieves_voc_context() -> None:
    context = retrieve_schema("최근 30일 VOC 유형별 처리 현황 알려줘.")

    assert "voc_cases" in context.allowed_table_names
    assert any(metric.name == "voc_status" for metric in context.matched_metrics)


def test_generates_high_risk_sql() -> None:
    context = retrieve_schema("이번 달 고위험 상품 가입 건수가 많은 지점은?")
    generated = generate_sql("이번 달 고위험 상품 가입 건수가 많은 지점은?", context)

    assert "risk_grade >= 4" in generated.sql
    assert "product_sales" in generated.sql
    assert "LIMIT" in generated.sql
