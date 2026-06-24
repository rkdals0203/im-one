from __future__ import annotations

from im_one_agent.sql_safety import validate_sql


ALLOWED = {"branches", "accounts", "product_sales", "voc_cases"}


def test_allows_safe_select_with_limit() -> None:
    result = validate_sql("SELECT branch_id FROM accounts LIMIT 10", ALLOWED)

    assert result.allowed
    assert result.issues == ()
    assert result.referenced_tables == ("accounts",)


def test_blocks_mutation_statement() -> None:
    result = validate_sql("DELETE FROM accounts LIMIT 10", ALLOWED)

    assert not result.allowed
    assert any("SELECT" in issue or "위험" in issue for issue in result.issues)


def test_blocks_unknown_table() -> None:
    result = validate_sql("SELECT * FROM customer_private LIMIT 10", ALLOWED)

    assert not result.allowed
    assert any("허용되지 않은 테이블" in issue for issue in result.issues)


def test_requires_limit() -> None:
    result = validate_sql("SELECT branch_id FROM accounts", ALLOWED)

    assert not result.allowed
    assert any("LIMIT" in issue for issue in result.issues)


def test_allows_cte_names_when_source_tables_are_allowed() -> None:
    sql = """
WITH account_summary AS (
    SELECT branch_id, COUNT(*) AS account_count
    FROM accounts
    GROUP BY branch_id
)
SELECT b.branch_id, a.account_count
FROM branches b
JOIN account_summary a ON b.branch_id = a.branch_id
LIMIT 10
""".strip()

    result = validate_sql(sql, ALLOWED)

    assert result.allowed
    assert result.referenced_tables == ("accounts", "branches")
