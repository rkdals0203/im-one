from __future__ import annotations

from dataclasses import dataclass


AS_OF_DATE = "2026-06-24"
DEFAULT_USER_ROLE = "branch_manager"
DEFAULT_BRANCH_ID = 1
DEMO_BRANCH_IDS = tuple(range(1, 11))
MAX_QUESTION_LENGTH = 1000


def normalize_question_text(question: object | None) -> str:
    normalized = str(question or "").strip()
    if not normalized:
        raise ValueError("질문을 입력해주세요.")
    if len(normalized) > MAX_QUESTION_LENGTH:
        raise ValueError(f"질문은 {MAX_QUESTION_LENGTH}자 이하로 입력해주세요.")
    return normalized


@dataclass(frozen=True)
class TableSpec:
    name: str
    description: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class MetricSpec:
    name: str
    description: str
    definition: str
    keywords: tuple[str, ...]
    tables: tuple[str, ...]
    related_columns: tuple[str, ...]
    date_column: str
    default_period: str
    filters: tuple[str, ...]
    join_paths: tuple[str, ...]
    default_grouping: str
    sample_question: str


TABLES: dict[str, TableSpec] = {
    "branches": TableSpec(
        name="branches",
        description="영업점/지역 기준 정보",
        columns=("branch_id", "branch_name", "region", "branch_type", "opened_date", "active_flag"),
    ),
    "accounts": TableSpec(
        name="accounts",
        description="가상 신규 계좌 개설 이력",
        columns=(
            "account_id",
            "branch_id",
            "opened_at",
            "channel",
            "customer_segment",
            "age_band",
            "risk_profile_band",
            "is_first_account",
        ),
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
            "channel",
            "suitability_checked",
            "cooling_off_eligible",
        ),
    ),
    "voc_cases": TableSpec(
        name="voc_cases",
        description="가상 VOC/민원 접수 및 처리 이력",
        columns=(
            "case_id",
            "branch_id",
            "case_type",
            "status",
            "received_at",
            "resolved_at",
            "severity",
            "product_type",
            "sla_due_at",
        ),
    ),
    "investment_reviews": TableSpec(
        name="investment_reviews",
        description="가상 투자성향/적합성 점검 이력",
        columns=(
            "review_id",
            "branch_id",
            "review_type",
            "status",
            "created_at",
            "due_at",
            "product_type",
            "risk_grade",
        ),
    ),
    "branch_targets": TableSpec(
        name="branch_targets",
        description="가상 영업점 월별 목표 지표",
        columns=("target_id", "branch_id", "target_month", "metric_name", "target_value"),
    ),
    "demo_dataset_metadata": TableSpec(
        name="demo_dataset_metadata",
        description="합성 POC 데이터셋 분류와 비운영 고지 메타데이터",
        columns=("metadata_key", "metadata_value"),
    ),
    "query_audit_log": TableSpec(
        name="query_audit_log",
        description="운영 전환을 위한 SQL 질의 감사 로그 테이블",
        columns=(
            "audit_id",
            "created_at",
            "user_id",
            "auth_mode",
            "user_role",
            "original_question",
            "question",
            "selected_semantic_metrics",
            "semantic_metrics",
            "generated_sql",
            "llm_generated_sql",
            "policy_applied_sql",
            "validated_sql",
            "sql_policy_transformations",
            "generation_engine",
            "llm_model",
            "prompt_version",
            "validation_status",
            "execution_status",
            "validation_issues",
            "referenced_tables",
            "row_count",
            "pre_execution_row_count",
            "pre_execution_row_count_status",
            "pre_execution_check_ms",
            "query_plan_summary",
            "execution_ms",
            "blocked_reason",
        ),
    ),
}


METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        name="new_accounts",
        description="신규 계좌 수: accounts.account_id 건수",
        definition="COUNT(accounts.account_id)",
        keywords=("신규 계좌", "계좌 개설", "계좌 수", "계좌"),
        tables=("accounts", "branches"),
        related_columns=("accounts.account_id", "accounts.opened_at", "accounts.branch_id", "branches.branch_name"),
        date_column="accounts.opened_at",
        default_period="최근 3개월",
        filters=(),
        join_paths=("accounts.branch_id = branches.branch_id",),
        default_grouping="영업점, 월",
        sample_question="지난 3개월간 지점별 신규 계좌 수 추이는?",
    ),
    MetricSpec(
        name="high_risk_product_sales",
        description="고위험 상품 가입 건수: risk_grade >= 4인 product_sales 건수",
        definition="COUNT(product_sales.sale_id)",
        keywords=("고위험", "위험등급", "고난도", "리스크", "위험한 상품", "위험 상품", "많이 판"),
        tables=("product_sales", "branches"),
        related_columns=(
            "product_sales.sale_id",
            "product_sales.risk_grade",
            "product_sales.amount",
            "product_sales.sold_at",
            "product_sales.branch_id",
            "branches.branch_name",
        ),
        date_column="product_sales.sold_at",
        default_period="이번 달 또는 질문의 명시 기간",
        filters=("product_sales.risk_grade >= 4",),
        join_paths=("product_sales.branch_id = branches.branch_id",),
        default_grouping="영업점",
        sample_question="이번 달 고위험 상품 가입 건수가 많은 지점은?",
    ),
    MetricSpec(
        name="voc_status",
        description="VOC 처리 현황: voc_cases.case_type/status별 건수",
        definition="COUNT(voc_cases.case_id)",
        keywords=("VOC", "민원", "불만", "처리 현황"),
        tables=("voc_cases", "branches"),
        related_columns=(
            "voc_cases.case_id",
            "voc_cases.case_type",
            "voc_cases.status",
            "voc_cases.received_at",
            "voc_cases.branch_id",
            "branches.branch_name",
        ),
        date_column="voc_cases.received_at",
        default_period="최근 30일",
        filters=(),
        join_paths=("voc_cases.branch_id = branches.branch_id",),
        default_grouping="VOC 유형, 처리 상태",
        sample_question="최근 30일 VOC 유형별 처리 현황 알려줘.",
    ),
    MetricSpec(
        name="els_sales_vs_voc",
        description="ELS 가입 금액과 민원 건수 비교",
        definition="SUM(product_sales.amount) for ELS sales and COUNT(voc_cases.case_id)",
        keywords=("ELS", "가입 금액", "민원 건수", "비교"),
        tables=("product_sales", "voc_cases", "branches"),
        related_columns=(
            "product_sales.amount",
            "product_sales.product_type",
            "product_sales.sold_at",
            "product_sales.branch_id",
            "voc_cases.case_id",
            "voc_cases.received_at",
            "voc_cases.branch_id",
            "branches.branch_name",
        ),
        date_column="product_sales.sold_at, voc_cases.received_at",
        default_period="최근 3개월",
        filters=("product_sales.product_type = 'ELS'",),
        join_paths=(
            "product_sales.branch_id = branches.branch_id",
            "voc_cases.branch_id = branches.branch_id",
        ),
        default_grouping="영업점",
        sample_question="영업점별 ELS 가입 금액과 민원 건수를 비교해줘.",
    ),
    MetricSpec(
        name="investment_review_status",
        description="투자성향/적합성 점검 처리 현황",
        definition="COUNT(investment_reviews.review_id)",
        keywords=("투자성향", "적합성", "점검", "심사"),
        tables=("investment_reviews", "branches"),
        related_columns=(
            "investment_reviews.review_id",
            "investment_reviews.review_type",
            "investment_reviews.status",
            "investment_reviews.created_at",
            "investment_reviews.branch_id",
            "branches.branch_name",
        ),
        date_column="investment_reviews.created_at",
        default_period="최근 60일",
        filters=("investment_reviews.status != 'completed' for 미완료 questions",),
        join_paths=("investment_reviews.branch_id = branches.branch_id",),
        default_grouping="영업점, 점검 상태",
        sample_question="최근 투자성향 점검 미완료 건수가 많은 지점은?",
    ),
    MetricSpec(
        name="new_accounts_vs_target",
        description="신규 계좌 실적과 지점별 목표 비교",
        definition="COUNT(accounts.account_id) compared with branch_targets.target_value where metric_name = 'new_accounts'",
        keywords=("목표 대비", "목표", "실적", "타겟"),
        tables=("accounts", "branch_targets", "branches"),
        related_columns=(
            "accounts.account_id",
            "accounts.opened_at",
            "accounts.branch_id",
            "branch_targets.target_month",
            "branch_targets.metric_name",
            "branch_targets.target_value",
            "branches.branch_name",
        ),
        date_column="accounts.opened_at, branch_targets.target_month",
        default_period="최근 3개월",
        filters=("branch_targets.metric_name = 'new_accounts'",),
        join_paths=(
            "accounts.branch_id = branches.branch_id",
            "branch_targets.branch_id = branches.branch_id",
        ),
        default_grouping="영업점, 월",
        sample_question="지점별 신규 계좌 목표 대비 실적을 비교해줘.",
    ),
)


ROLE_TABLE_POLICY: dict[str, set[str]] = {
    "sales_planning": {
        "branches",
        "accounts",
        "product_sales",
        "voc_cases",
        "investment_reviews",
        "branch_targets",
    },
    "branch_manager": {
        "branches",
        "accounts",
        "product_sales",
        "voc_cases",
        "investment_reviews",
        "branch_targets",
    },
    "compliance": {"branches", "product_sales", "voc_cases", "investment_reviews"},
}


def normalize_user_role(user_role: str | None) -> str:
    normalized = (user_role or DEFAULT_USER_ROLE).strip()
    if normalized in ROLE_TABLE_POLICY:
        return normalized
    return DEFAULT_USER_ROLE


def normalize_branch_id(branch_id: object | None) -> int:
    try:
        normalized = int(branch_id) if branch_id is not None else DEFAULT_BRANCH_ID
    except (TypeError, ValueError):
        return DEFAULT_BRANCH_ID
    if normalized in DEMO_BRANCH_IDS:
        return normalized
    return DEFAULT_BRANCH_ID


BUSINESS_RULES = (
    "모든 데이터는 가상 데모 데이터입니다.",
    "고위험 상품은 risk_grade >= 4로 정의합니다.",
    "신규 계좌 수는 accounts.account_id 기준으로 집계합니다.",
    "VOC 처리 현황은 voc_cases.case_type과 status 기준으로 집계합니다.",
    "모든 조회는 읽기 전용 SELECT만 허용합니다.",
    "고객 단위 원천 정보 대신 집계 결과 중심으로 응답합니다.",
)
