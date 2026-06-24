from __future__ import annotations

from dataclasses import dataclass

from im_one_agent.domain import AS_OF_DATE
from im_one_agent.schema_retrieval import SchemaContext


@dataclass(frozen=True)
class GeneratedSQL:
    sql: str
    reason: str


def generate_sql(question: str, context: SchemaContext) -> GeneratedSQL:
    """Deterministic SQL generator for a stable Friday POC demo.

    In the bootcamp, this function can become an LLM node that receives the
    same retrieved schema context and still passes through the validation node.
    """
    normalized = question.lower()
    metric_names = {metric.name for metric in context.matched_metrics}

    if "els" in normalized and ("민원" in normalized or "비교" in normalized):
        return GeneratedSQL(
            sql=f"""
WITH els_sales AS (
    SELECT branch_id, SUM(amount) AS els_amount, COUNT(*) AS els_count
    FROM product_sales
    WHERE product_type = 'ELS'
      AND sold_at >= DATE('{AS_OF_DATE}', '-3 months')
    GROUP BY branch_id
),
voc_summary AS (
    SELECT branch_id, COUNT(*) AS voc_count
    FROM voc_cases
    WHERE received_at >= DATE('{AS_OF_DATE}', '-3 months')
    GROUP BY branch_id
)
SELECT
    b.branch_name,
    COALESCE(e.els_amount, 0) AS els_amount,
    COALESCE(e.els_count, 0) AS els_count,
    COALESCE(v.voc_count, 0) AS voc_count
FROM branches b
LEFT JOIN els_sales e ON b.branch_id = e.branch_id
LEFT JOIN voc_summary v ON b.branch_id = v.branch_id
ORDER BY els_amount DESC, voc_count DESC
LIMIT 20
""".strip(),
            reason="ELS 가입 금액과 VOC 건수를 영업점 단위로 비교합니다.",
        )

    if "voc_status" in metric_names or "민원" in normalized or "voc" in normalized:
        return GeneratedSQL(
            sql=f"""
SELECT
    v.case_type,
    v.status,
    COUNT(*) AS case_count
FROM voc_cases v
WHERE v.received_at >= DATE('{AS_OF_DATE}', '-30 days')
GROUP BY v.case_type, v.status
ORDER BY case_count DESC, v.case_type
LIMIT 30
""".strip(),
            reason="최근 30일 VOC를 유형과 처리 상태별로 집계합니다.",
        )

    if "high_risk_product_sales" in metric_names or "고위험" in normalized or "리스크" in normalized:
        return GeneratedSQL(
            sql=f"""
SELECT
    b.branch_name,
    COUNT(*) AS high_risk_sale_count,
    SUM(ps.amount) AS total_amount
FROM product_sales ps
JOIN branches b ON ps.branch_id = b.branch_id
WHERE ps.risk_grade >= 4
  AND ps.sold_at >= DATE('{AS_OF_DATE}', 'start of month')
GROUP BY b.branch_name
ORDER BY high_risk_sale_count DESC, total_amount DESC
LIMIT 20
""".strip(),
            reason="이번 달 위험등급 4 이상 상품 가입 건수를 영업점별로 집계합니다.",
        )

    if "investment_review_status" in metric_names or "투자성향" in normalized or "적합성" in normalized:
        return GeneratedSQL(
            sql=f"""
SELECT
    b.branch_name,
    ir.status,
    COUNT(*) AS review_count
FROM investment_reviews ir
JOIN branches b ON ir.branch_id = b.branch_id
WHERE ir.created_at >= DATE('{AS_OF_DATE}', '-60 days')
GROUP BY b.branch_name, ir.status
ORDER BY review_count DESC, b.branch_name
LIMIT 30
""".strip(),
            reason="최근 60일 투자성향/적합성 점검 상태를 영업점별로 집계합니다.",
        )

    return GeneratedSQL(
        sql=f"""
SELECT
    b.branch_name,
    STRFTIME('%Y-%m', a.opened_at) AS opened_month,
    COUNT(*) AS new_account_count
FROM accounts a
JOIN branches b ON a.branch_id = b.branch_id
WHERE a.opened_at >= DATE('{AS_OF_DATE}', '-3 months')
GROUP BY b.branch_name, opened_month
ORDER BY opened_month, b.branch_name
LIMIT 50
""".strip(),
        reason="지난 3개월 신규 계좌 개설 수를 영업점과 월 기준으로 집계합니다.",
    )
