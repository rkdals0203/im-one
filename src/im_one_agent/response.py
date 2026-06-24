from __future__ import annotations

from im_one_agent.schema_retrieval import SchemaContext
from im_one_agent.sql_safety import ValidationResult


def format_rows(columns: list[str], rows: list[dict[str, object]], max_rows: int = 10) -> str:
    if not rows:
        return "조회 결과가 없습니다."

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
) -> str:
    metric_names = ", ".join(metric.name for metric in context.matched_metrics)
    table_names = ", ".join(table.name for table in context.tables)
    referenced = ", ".join(validation.referenced_tables) or "없음"

    return "\n".join(
        [
            f"질문: {question}",
            f"해석한 업무 지표: {metric_names}",
            f"참조 가능한 스키마: {table_names}",
            f"실제 SQL 참조 테이블: {referenced}",
            f"생성 기준: {generation_reason}",
            f"검증 결과: {'통과' if validation.allowed else '차단'}",
            f"조회 행 수: {row_count}",
        ]
    )


def build_blocked_answer(question: str, validation: ValidationResult) -> str:
    issues = "\n".join(f"- {issue}" for issue in validation.issues)
    return f"질문 '{question}'에 대한 SQL 실행이 차단되었습니다.\n{issues}"
