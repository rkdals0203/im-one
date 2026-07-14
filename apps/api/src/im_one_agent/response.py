from __future__ import annotations

from im_one_agent.schema_retrieval import SchemaContext
from im_one_agent.sql_safety import ValidationResult


LLM_RETRY_GUIDANCE = (
    "LLM 생성이 실패했습니다. API 키, 모델명, base URL, 네트워크 상태를 확인한 뒤 다시 실행하거나 "
    "질문에 기간/지점/지표 기준을 더 구체적으로 적어 재시도하세요."
)


def format_rows(columns: list[str], rows: list[dict[str, object]], max_rows: int = 10) -> str:
    if not rows:
        return "조건에 맞는 데이터가 없습니다."

    visible_rows = rows[:max_rows]
    widths = {column: len(column) for column in columns}
    for row in visible_rows:
        for column in columns:
            widths[column] = max(widths[column], len(str(row.get(column, ""))))

    header = " | ".join(column.ljust(widths[column]) for column in columns)
    divider = "-+-".join("-" * widths[column] for column in columns)
    lines = [header, divider]
    for row in visible_rows:
        lines.append(" | ".join(str(row.get(column, "")).ljust(widths[column]) for column in columns))

    if len(rows) > max_rows:
        lines.append(f"... {len(rows) - max_rows} rows omitted")

    return "\n".join(lines)


def build_explanation(
    question: str,
    context: SchemaContext,
    validation: ValidationResult,
    row_count: int,
    generation_reason: str,
    generation_assumptions: tuple[str, ...] = (),
) -> str:
    metric_names = ", ".join(metric.name for metric in context.matched_metrics)
    metric_definitions = "; ".join(f"{metric.name}: {metric.definition}" for metric in context.matched_metrics)
    date_criteria = "; ".join(
        f"{metric.name}: {metric.date_column} / {metric.default_period}" for metric in context.matched_metrics
    )
    grouping_criteria = "; ".join(f"{metric.name}: {metric.default_grouping}" for metric in context.matched_metrics)
    filter_criteria = "; ".join(
        f"{metric.name}: {', '.join(metric.filters) if metric.filters else '없음'}"
        for metric in context.matched_metrics
    )
    table_names = ", ".join(table.name for table in context.tables)
    referenced = ", ".join(validation.referenced_tables) or "없음"
    assumptions = "; ".join(generation_assumptions) if generation_assumptions else "없음"
    clarification = "; ".join(context.clarification_options) if context.clarification_options else "없음"
    validation_evidence = "; ".join(build_validation_evidence(validation))

    return "\n".join(
        [
            f"질문: {question}",
            f"해석한 업무 지표: {metric_names}",
            f"해석 신뢰도: {context.retrieval_confidence}",
            f"확인 질문 제안: {clarification}",
            f"지표 정의: {metric_definitions}",
            f"기간 기준: {date_criteria}",
            f"집계 기준: {grouping_criteria}",
            f"필터 기준: {filter_criteria}",
            f"참조 가능한 스키마: {table_names}",
            f"실제 SQL 참조 테이블: {referenced}",
            f"생성 기준: {generation_reason}",
            f"생성 가정: {assumptions}",
            f"검증 결과: {'통과' if validation.allowed else '차단'}",
            f"검증 근거: {validation_evidence}",
            f"조회 행 수: {row_count}",
            "주의: 이 결과는 합성 데이터 기반 POC 결과이며, 보고나 의사결정 전 기준과 SQL을 검토해야 합니다.",
        ]
    )


def format_validation_evidence(
    allowed: bool,
    issues: tuple[str, ...] | list[str],
    referenced_tables: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    referenced = ", ".join(referenced_tables) if referenced_tables else "없음"
    if allowed:
        return (
            "읽기 전용 SELECT/WITH 조회 정책을 통과했습니다.",
            "단일 문장, 주석 없음, 금지 DML/DDL 키워드 없음 검사를 통과했습니다.",
            f"허용 테이블 whitelist 검사를 통과했습니다: {referenced}.",
            "LIMIT/조회량 제한과 위험 query-shape 검사를 통과했습니다.",
            "row-level 식별자, 상세 이벤트 조회, 역할별 지점 범위 검사를 통과했습니다.",
        )

    blocked_evidence = ["SQL Validation Layer에서 실행 전 차단되었습니다."]
    blocked_evidence.extend(f"차단 사유: {issue}" for issue in issues)
    return tuple(blocked_evidence)


def build_validation_evidence(validation: ValidationResult) -> tuple[str, ...]:
    return format_validation_evidence(
        validation.allowed,
        validation.issues,
        validation.referenced_tables,
    )


def retry_guidance_for(validation: ValidationResult) -> str:
    if any("LLM SQL 생성 실패" in issue for issue in validation.issues):
        return LLM_RETRY_GUIDANCE
    return ""


def build_blocked_answer(question: str, validation: ValidationResult) -> str:
    issues = "\n".join(f"- {issue}" for issue in validation.issues)
    guidance = retry_guidance_for(validation)
    if guidance:
        return f"질문 '{question}'에 대한 SQL 실행이 차단되었습니다.\n{issues}\n\n재시도 안내\n- {guidance}"
    return f"질문 '{question}'에 대한 SQL 실행이 차단되었습니다.\n{issues}"
