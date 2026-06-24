from __future__ import annotations

from dataclasses import dataclass

from im_one_agent.domain import BUSINESS_RULES, METRICS, ROLE_TABLE_POLICY, TABLES, MetricSpec, TableSpec


@dataclass(frozen=True)
class SchemaContext:
    matched_metrics: tuple[MetricSpec, ...]
    tables: tuple[TableSpec, ...]
    business_rules: tuple[str, ...]
    example_queries: tuple[str, ...]

    @property
    def allowed_table_names(self) -> set[str]:
        return {table.name for table in self.tables}


def retrieve_schema(question: str, user_role: str = "branch_manager") -> SchemaContext:
    """Return the smallest useful schema context for a business question."""
    normalized = question.lower()
    role_allowed = ROLE_TABLE_POLICY.get(user_role, ROLE_TABLE_POLICY["branch_manager"])

    scored: list[tuple[int, MetricSpec]] = []
    for metric in METRICS:
        score = sum(1 for keyword in metric.keywords if keyword.lower() in normalized)
        if score:
            scored.append((score, metric))

    if not scored:
        scored = [(1, METRICS[0])]

    scored.sort(key=lambda item: item[0], reverse=True)
    matched_metrics = tuple(metric for _, metric in scored[:2])

    table_names: set[str] = set()
    for metric in matched_metrics:
        table_names.update(metric.tables)

    table_names &= role_allowed
    tables = tuple(TABLES[name] for name in sorted(table_names))
    examples = tuple(metric.sample_question for metric in matched_metrics)

    return SchemaContext(
        matched_metrics=matched_metrics,
        tables=tables,
        business_rules=BUSINESS_RULES,
        example_queries=examples,
    )
