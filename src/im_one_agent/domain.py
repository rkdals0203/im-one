from __future__ import annotations

from dataclasses import dataclass


AS_OF_DATE = "2026-06-24"


@dataclass(frozen=True)
class TableSpec:
    name: str
    description: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class MetricSpec:
    name: str
    description: str
    keywords: tuple[str, ...]
    tables: tuple[str, ...]
    default_grouping: str
    sample_question: str


TABLES: dict[str, TableSpec] = {
    "branches": TableSpec(
        name="branches",
        description="영업점/지역 기준 정보",
        columns=("branch_id", "branch_name", "region"),
    ),
    "accounts": TableSpec(
        name="accounts",
        description="가상 신규 계좌 개설 이력",
        columns=("account_id", "branch_id", "opened_at", "channel", "customer_segment"),
    ),
    "product_sales": TableSpec(
        name="product_sales",
        description="가상 금융상품 가입/판매 이력",
        columns=(
            "sale_id",
            "branch_id",
            "customer_segment",
            "product_type",
            "risk_grade",
            "amount",
            "sold_at",
        ),
    ),
    "voc_cases": TableSpec(
        name="voc_cases",
        description="가상 VOC/민원 접수 및 처리 이력",
        columns=("case_id", "branch_id", "case_type", "status", "received_at", "resolved_at"),
    ),
    "investment_reviews": TableSpec(
        name="investment_reviews",
        description="가상 투자성향/적합성 점검 이력",
        columns=("review_id", "branch_id", "review_type", "status", "created_at"),
    ),
}


METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        name="new_accounts",
        description="신규 계좌 수: accounts.account_id 건수",
        keywords=("신규 계좌", "계좌 개설", "계좌 수", "계좌"),
        tables=("accounts", "branches"),
        default_grouping="영업점, 월",
        sample_question="지난 3개월간 지점별 신규 계좌 수 추이는?",
    ),
    MetricSpec(
        name="high_risk_product_sales",
        description="고위험 상품 가입 건수: risk_grade >= 4인 product_sales 건수",
        keywords=("고위험", "위험등급", "고난도", "리스크"),
        tables=("product_sales", "branches"),
        default_grouping="영업점",
        sample_question="이번 달 고위험 상품 가입 건수가 많은 지점은?",
    ),
    MetricSpec(
        name="voc_status",
        description="VOC 처리 현황: voc_cases.case_type/status별 건수",
        keywords=("VOC", "민원", "불만", "처리 현황"),
        tables=("voc_cases", "branches"),
        default_grouping="VOC 유형, 처리 상태",
        sample_question="최근 30일 VOC 유형별 처리 현황 알려줘.",
    ),
    MetricSpec(
        name="els_sales_vs_voc",
        description="ELS 가입 금액과 민원 건수 비교",
        keywords=("ELS", "가입 금액", "민원 건수", "비교"),
        tables=("product_sales", "voc_cases", "branches"),
        default_grouping="영업점",
        sample_question="영업점별 ELS 가입 금액과 민원 건수를 비교해줘.",
    ),
    MetricSpec(
        name="investment_review_status",
        description="투자성향/적합성 점검 처리 현황",
        keywords=("투자성향", "적합성", "점검", "심사"),
        tables=("investment_reviews", "branches"),
        default_grouping="영업점, 점검 상태",
        sample_question="최근 투자성향 점검 미완료 건수가 많은 지점은?",
    ),
)


ROLE_TABLE_POLICY: dict[str, set[str]] = {
    "sales_planning": {"branches", "accounts", "product_sales", "voc_cases", "investment_reviews"},
    "branch_manager": {"branches", "accounts", "product_sales", "voc_cases", "investment_reviews"},
    "compliance": {"branches", "product_sales", "voc_cases", "investment_reviews"},
}


BUSINESS_RULES = (
    "모든 데이터는 가상 데모 데이터입니다.",
    "고위험 상품은 risk_grade >= 4로 정의합니다.",
    "신규 계좌 수는 accounts.account_id 기준으로 집계합니다.",
    "VOC 처리 현황은 voc_cases.case_type과 status 기준으로 집계합니다.",
    "모든 조회는 읽기 전용 SELECT만 허용합니다.",
    "고객 단위 원천 정보 대신 집계 결과 중심으로 응답합니다.",
)
