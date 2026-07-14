from __future__ import annotations

import sqlite3

from im_one_agent.sql_safety import apply_branch_scope_filter, parse_query_ast, validate_sql


ALLOWED = {"branches", "accounts", "product_sales", "voc_cases"}
ALLOWED_WITH_TARGETS = ALLOWED | {"branch_targets"}


def test_allows_safe_select_with_limit() -> None:
    result = validate_sql("SELECT branch_id, branch_name FROM branches LIMIT 10", ALLOWED)

    assert result.allowed
    assert result.issues == ()
    assert result.referenced_tables == ("branches",)


def test_blocks_mutation_statement() -> None:
    result = validate_sql("DELETE FROM accounts LIMIT 10", ALLOWED)

    assert not result.allowed
    assert any("SELECT" in issue or "위험" in issue for issue in result.issues)


def test_blocks_unknown_table() -> None:
    result = validate_sql("SELECT customer_id FROM customer_private LIMIT 10", ALLOWED)

    assert not result.allowed
    assert any("허용되지 않은 테이블" in issue for issue in result.issues)


def test_blocks_operational_audit_table_even_if_allowed_tables_include_it() -> None:
    result = validate_sql(
        "SELECT validation_status, execution_status FROM query_audit_log LIMIT 10",
        ALLOWED | {"query_audit_log"},
    )

    assert not result.allowed
    assert any("운영 감사/통제 테이블" in issue for issue in result.issues)


def test_blocks_quoted_operational_audit_table_reference() -> None:
    result = validate_sql(
        'SELECT validation_status, execution_status FROM "query_audit_log" LIMIT 10',
        ALLOWED | {"query_audit_log"},
    )

    assert not result.allowed
    assert result.referenced_tables == ("query_audit_log",)
    assert any("운영 감사/통제 테이블" in issue for issue in result.issues)


def test_blocks_quoted_unknown_table_reference() -> None:
    result = validate_sql('SELECT customer_id FROM "customer_private" LIMIT 10', ALLOWED)

    assert not result.allowed
    assert result.referenced_tables == ("customer_private",)
    assert any("허용되지 않은 테이블" in issue for issue in result.issues)


def test_blocks_unknown_column_when_connection_is_available() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE branches (branch_id INTEGER, branch_name TEXT)")
        result = validate_sql("SELECT missing_column FROM branches LIMIT 10", ALLOWED, connection=connection)
    finally:
        connection.close()

    assert not result.allowed
    assert any("SQL 문법 또는 실행 계획 오류" in issue for issue in result.issues)


def test_blocks_sql_syntax_error_when_connection_is_available() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE branches (branch_id INTEGER, branch_name TEXT)")
        result = validate_sql("SELECT branch_id branches LIMIT 10", ALLOWED, connection=connection)
    finally:
        connection.close()

    assert not result.allowed
    assert any("SQL 문법 또는 실행 계획 오류" in issue for issue in result.issues)


def test_uses_sqlglot_parser_when_available(monkeypatch) -> None:
    class FakeSqlglot:
        @staticmethod
        def parse_one(sql: str, read: str) -> None:
            raise ValueError(f"{read}: invalid")

    monkeypatch.setattr("im_one_agent.sql_safety.sqlglot", FakeSqlglot)

    result = validate_sql("SELECT branch_id FROM branches LIMIT 10", ALLOWED)

    assert not result.allowed
    assert any("SQL parser 검증 오류" in issue for issue in result.issues)


def test_blocks_select_star() -> None:
    result = validate_sql("SELECT * FROM accounts LIMIT 10", ALLOWED)

    assert not result.allowed
    assert any("SELECT *" in issue for issue in result.issues)


def test_blocks_qualified_select_star() -> None:
    result = validate_sql("SELECT a.* FROM accounts a LIMIT 10", ALLOWED)

    assert not result.allowed
    assert any("SELECT *" in issue for issue in result.issues)


def test_query_ast_extracts_ctes_tables_limit_and_scope() -> None:
    ast = parse_query_ast(
        """
WITH account_summary AS (
    SELECT branch_id, COUNT(*) AS account_count
    FROM accounts
    WHERE branch_id = 1
    GROUP BY branch_id
)
SELECT a.branch_id, a.account_count
FROM account_summary a
JOIN branches b ON a.branch_id = b.branch_id
LIMIT 10
""".strip()
    )

    assert ast.statement_type == "with"
    assert ast.cte_names == ("account_summary",)
    assert ast.referenced_tables == ("accounts", "branches")
    assert ast.limit == 10
    assert ast.branch_scope_branch_ids == (1,)
    assert ast.selected_sensitive_columns == ()
    assert ast.selected_event_detail_columns == ("branch_id",)
    assert {reference.alias: reference.table_name for reference in ast.table_references} == {
        "accounts": "accounts",
        "b": "branches",
    }


def test_query_ast_extracts_quoted_ctes_tables_and_aliases() -> None:
    ast = parse_query_ast(
        """
WITH "account_summary" AS (
    SELECT branch_id, COUNT(*) AS account_count
    FROM "accounts"
    WHERE branch_id = 1
    GROUP BY branch_id
)
SELECT b.branch_name, a.account_count
FROM "account_summary" a
JOIN "branches" AS b ON a.branch_id = b.branch_id
LIMIT 10
""".strip()
    )

    assert ast.cte_names == ("account_summary",)
    assert ast.referenced_tables == ("accounts", "branches")
    assert {reference.alias: reference.table_name for reference in ast.table_references} == {
        "accounts": "accounts",
        "b": "branches",
    }


def test_allows_quoted_business_table_reference() -> None:
    result = validate_sql(
        'SELECT branch_id, COUNT(*) AS account_count FROM "accounts" '
        "WHERE branch_id = 1 GROUP BY branch_id LIMIT 10",
        ALLOWED,
        branch_scope_branch_id=1,
    )

    assert result.allowed
    assert result.referenced_tables == ("accounts",)


def test_requires_limit() -> None:
    result = validate_sql("SELECT branch_id FROM accounts", ALLOWED)

    assert not result.allowed
    assert any("LIMIT" in issue for issue in result.issues)


def test_requires_top_level_limit_when_cte_has_inner_limit_only() -> None:
    result = validate_sql(
        """
WITH account_summary AS (
    SELECT branch_id, COUNT(*) AS account_count
    FROM accounts
    GROUP BY branch_id
    LIMIT 10
)
SELECT branch_id, account_count FROM account_summary
""".strip(),
        ALLOWED,
    )

    assert not result.allowed
    assert any("LIMIT" in issue for issue in result.issues)


def test_blocks_non_positive_limit() -> None:
    result = validate_sql("SELECT branch_id FROM accounts GROUP BY branch_id LIMIT 0", ALLOWED)

    assert not result.allowed
    assert any("LIMIT은 1 이상" in issue for issue in result.issues)


def test_blocks_large_offset() -> None:
    result = validate_sql(
        "SELECT branch_id, COUNT(*) AS account_count FROM accounts GROUP BY branch_id LIMIT 10 OFFSET 1001",
        ALLOWED,
    )

    assert not result.allowed
    assert any("OFFSET은 1000 이하" in issue for issue in result.issues)


def test_blocks_order_by_random_as_expensive_pattern() -> None:
    result = validate_sql(
        "SELECT branch_id, COUNT(*) AS account_count FROM accounts GROUP BY branch_id ORDER BY RANDOM() LIMIT 10",
        ALLOWED,
    )

    assert not result.allowed
    assert any("ORDER BY RANDOM" in issue for issue in result.issues)


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


def test_blocks_from_nested_subquery_and_requires_cte_instead() -> None:
    result = validate_sql(
        "SELECT branch_id, account_count FROM ("
        "SELECT branch_id, COUNT(*) AS account_count FROM accounts GROUP BY branch_id"
        ") AS account_summary LIMIT 10",
        ALLOWED,
    )

    assert not result.allowed
    assert any("nested subquery" in issue for issue in result.issues)


def test_blocks_scalar_nested_subquery() -> None:
    result = validate_sql(
        "SELECT branch_name, (SELECT COUNT(*) FROM accounts) AS account_count FROM branches LIMIT 10",
        ALLOWED,
    )

    assert not result.allowed
    assert any("nested subquery" in issue for issue in result.issues)


def test_branch_manager_requires_branch_scope() -> None:
    result = validate_sql(
        "SELECT branch_id, COUNT(*) AS account_count FROM accounts GROUP BY branch_id LIMIT 10",
        ALLOWED,
        branch_scope_branch_id=1,
    )

    assert not result.allowed
    assert any("branch_id = 1" in issue for issue in result.issues)


def test_branch_scope_filter_adds_where_before_group_by() -> None:
    sql = (
        "SELECT b.branch_name, COUNT(*) AS account_count "
        "FROM accounts a JOIN branches b ON a.branch_id = b.branch_id "
        "GROUP BY b.branch_name LIMIT 10"
    )

    scoped_sql = apply_branch_scope_filter(sql, 1)
    result = validate_sql(scoped_sql, ALLOWED, branch_scope_branch_id=1)

    assert "WHERE a.branch_id = 1 GROUP BY" in scoped_sql
    assert result.allowed


def test_branch_scope_filter_appends_to_existing_where() -> None:
    sql = (
        "SELECT b.branch_name, COUNT(*) AS account_count "
        "FROM accounts a JOIN branches b ON a.branch_id = b.branch_id "
        "WHERE a.opened_at >= DATE('2026-06-24', '-3 months') "
        "GROUP BY b.branch_name LIMIT 10"
    )

    scoped_sql = apply_branch_scope_filter(sql, 1)

    assert "AND a.branch_id = 1 GROUP BY" in scoped_sql


def test_branch_scope_filter_ignores_clause_keywords_inside_string_literals() -> None:
    sql = (
        "SELECT branch_id, COUNT(*) AS account_count "
        "FROM accounts "
        "WHERE channel = 'mobile GROUP BY branch_id (SELECT 1)' "
        "GROUP BY branch_id LIMIT 10"
    )

    scoped_sql = apply_branch_scope_filter(sql, 1)
    result = validate_sql(scoped_sql, ALLOWED, branch_scope_branch_id=1)

    assert "WHERE channel = 'mobile GROUP BY branch_id (SELECT 1)' AND accounts.branch_id = 1 GROUP BY" in scoped_sql
    assert result.allowed


def test_branch_scope_filter_leaves_risky_or_query_for_validation() -> None:
    sql = (
        "SELECT branch_id, COUNT(*) AS account_count FROM accounts "
        "WHERE channel = 'mobile' OR channel = 'web' GROUP BY branch_id LIMIT 10"
    )

    scoped_sql = apply_branch_scope_filter(sql, 1)
    result = validate_sql(scoped_sql, ALLOWED, branch_scope_branch_id=1)

    assert scoped_sql == sql
    assert not result.allowed
    assert any("branch_id = 1" in issue for issue in result.issues)


def test_branch_scope_filter_leaves_nested_select_for_validation() -> None:
    sql = (
        "SELECT branch_id, account_count FROM ("
        "SELECT branch_id, COUNT(*) AS account_count FROM accounts GROUP BY branch_id"
        ") LIMIT 10"
    )

    scoped_sql = apply_branch_scope_filter(sql, 1)

    assert scoped_sql == sql


def test_branch_manager_allows_scoped_query() -> None:
    result = validate_sql(
        "SELECT branch_id, COUNT(*) AS account_count FROM accounts WHERE branch_id = 1 GROUP BY branch_id LIMIT 10",
        ALLOWED,
        branch_scope_branch_id=1,
    )

    assert result.allowed


def test_branch_manager_blocks_unscoped_joined_branch_table() -> None:
    result = validate_sql(
        "SELECT a.branch_id, COUNT(*) AS joined_count "
        "FROM accounts a JOIN voc_cases v ON 1 = 1 "
        "WHERE a.branch_id = 1 GROUP BY a.branch_id LIMIT 10",
        ALLOWED,
        branch_scope_branch_id=1,
    )

    assert not result.allowed
    assert any("지점 범위가 연결되지 않은" in issue for issue in result.issues)


def test_branch_manager_allows_branch_join_path_scope() -> None:
    result = validate_sql(
        "SELECT a.branch_id, COUNT(*) AS joined_count "
        "FROM accounts a JOIN voc_cases v ON a.branch_id = v.branch_id "
        "WHERE a.branch_id = 1 GROUP BY a.branch_id LIMIT 10",
        ALLOWED,
        branch_scope_branch_id=1,
    )

    assert result.allowed


def test_branch_manager_applies_scope_to_branch_targets() -> None:
    sql = (
        "SELECT target_month, metric_name, SUM(target_value) AS target_value "
        "FROM branch_targets GROUP BY target_month, metric_name LIMIT 10"
    )

    scoped_sql = apply_branch_scope_filter(sql, 1)
    result = validate_sql(scoped_sql, ALLOWED_WITH_TARGETS, branch_scope_branch_id=1)

    assert "WHERE branch_targets.branch_id = 1 GROUP BY" in scoped_sql
    assert result.allowed


def test_branch_manager_blocks_unscoped_branch_targets() -> None:
    result = validate_sql(
        "SELECT target_month, metric_name, SUM(target_value) AS target_value "
        "FROM branch_targets GROUP BY target_month, metric_name LIMIT 10",
        ALLOWED_WITH_TARGETS,
        branch_scope_branch_id=1,
    )

    assert not result.allowed
    assert any("branch_id = 1" in issue for issue in result.issues)


def test_branch_manager_blocks_branch_targets_join_not_connected_to_scope() -> None:
    result = validate_sql(
        "SELECT a.branch_id, bt.target_month, COUNT(*) AS account_count, SUM(bt.target_value) AS target_value "
        "FROM accounts a JOIN branch_targets bt ON bt.metric_name = 'new_accounts' "
        "WHERE a.branch_id = 1 GROUP BY a.branch_id, bt.target_month LIMIT 10",
        ALLOWED_WITH_TARGETS,
        branch_scope_branch_id=1,
    )

    assert not result.allowed
    assert any("지점 범위가 연결되지 않은" in issue for issue in result.issues)


def test_branch_manager_allows_scoped_ctes_and_outer_branch_scope() -> None:
    sql = """
WITH els_sales AS (
    SELECT branch_id, COUNT(*) AS els_count
    FROM product_sales
    WHERE branch_id = 1
    GROUP BY branch_id
),
voc_summary AS (
    SELECT branch_id, COUNT(*) AS voc_count
    FROM voc_cases
    WHERE branch_id = 1
    GROUP BY branch_id
)
SELECT b.branch_name, COALESCE(e.els_count, 0) AS els_count, COALESCE(v.voc_count, 0) AS voc_count
FROM branches b
LEFT JOIN els_sales e ON b.branch_id = e.branch_id
LEFT JOIN voc_summary v ON b.branch_id = v.branch_id
WHERE b.branch_id = 1
LIMIT 10
""".strip()

    result = validate_sql(sql, ALLOWED, branch_scope_branch_id=1)

    assert result.allowed


def test_branch_manager_blocks_or_scope_bypass() -> None:
    result = validate_sql(
        "SELECT branch_id, COUNT(*) AS account_count FROM accounts "
        "WHERE branch_id = 1 OR branch_id = 2 GROUP BY branch_id LIMIT 10",
        ALLOWED,
        branch_scope_branch_id=1,
    )

    assert not result.allowed
    assert any("OR 조건" in issue for issue in result.issues)
    assert any("다른 branch_id" in issue for issue in result.issues)


def test_blocks_row_level_identifier_selection() -> None:
    result = validate_sql(
        "SELECT account_id, opened_at FROM accounts WHERE branch_id = 1 LIMIT 10",
        ALLOWED,
        branch_scope_branch_id=1,
    )

    assert not result.allowed
    assert any("row-level 식별자" in issue for issue in result.issues)


def test_blocks_row_level_event_detail_without_aggregate() -> None:
    result = validate_sql(
        "SELECT opened_at, channel, customer_segment FROM accounts WHERE branch_id = 1 LIMIT 10",
        ALLOWED,
        branch_scope_branch_id=1,
    )

    assert not result.allowed
    assert any("집계" in issue or "GROUP BY" in issue for issue in result.issues)


def test_blocks_mixed_aggregate_and_row_level_event_detail_without_group_by() -> None:
    result = validate_sql(
        "SELECT opened_at, channel, COUNT(*) AS account_count FROM accounts WHERE branch_id = 1 LIMIT 10",
        ALLOWED,
        branch_scope_branch_id=1,
    )

    assert not result.allowed
    assert any("row-level 상세 컬럼" in issue for issue in result.issues)


def test_blocks_cross_join_as_cartesian_join_pattern() -> None:
    result = validate_sql(
        "SELECT a.branch_id, COUNT(*) AS joined_count "
        "FROM accounts a CROSS JOIN voc_cases v "
        "GROUP BY a.branch_id LIMIT 10",
        ALLOWED,
    )

    assert not result.allowed
    assert any("카티션 조인" in issue for issue in result.issues)


def test_blocks_tautological_join_condition_as_cartesian_join_pattern() -> None:
    result = validate_sql(
        "SELECT a.branch_id, COUNT(*) AS joined_count "
        "FROM accounts a JOIN voc_cases v ON 1 = 1 "
        "GROUP BY a.branch_id LIMIT 10",
        ALLOWED,
    )

    assert not result.allowed
    assert any("카티션 조인" in issue for issue in result.issues)


def test_blocks_implicit_comma_join_as_cartesian_join_pattern() -> None:
    result = validate_sql(
        "SELECT a.branch_id, COUNT(*) AS joined_count "
        "FROM accounts a, voc_cases v "
        "GROUP BY a.branch_id LIMIT 10",
        ALLOWED,
    )

    assert not result.allowed
    assert any("카티션 조인" in issue for issue in result.issues)


def test_blocks_recursive_cte_as_long_running_risk() -> None:
    result = validate_sql(
        """
WITH RECURSIVE numbers(value) AS (
    SELECT 1
    UNION ALL
    SELECT value + 1 FROM numbers WHERE value < 1000000
)
SELECT value FROM numbers LIMIT 10
""".strip(),
        ALLOWED,
    )

    assert not result.allowed
    assert any("WITH RECURSIVE" in issue for issue in result.issues)


def test_blocks_compound_set_operations() -> None:
    for operator in ("UNION", "INTERSECT", "EXCEPT"):
        result = validate_sql(
            f"SELECT branch_id FROM branches {operator} SELECT branch_id FROM branches LIMIT 10",
            ALLOWED,
        )

        assert not result.allowed
        assert any("UNION/INTERSECT/EXCEPT" in issue for issue in result.issues)


def test_blocks_sqlite_file_and_extension_functions() -> None:
    for function_name in ("load_extension", "readfile", "writefile"):
        result = validate_sql(f"SELECT {function_name}('/tmp/value') AS value LIMIT 1", ALLOWED)

        assert not result.allowed
        assert any(function_name in issue for issue in result.issues)


def test_ignores_policy_keywords_and_table_names_inside_string_literals() -> None:
    sql = (
        "SELECT case_type, COUNT(*) AS case_count "
        "FROM voc_cases "
        "WHERE case_type = 'DROP; -- UNION readfile(''x'') FROM customer_private JOIN query_audit_log' "
        "GROUP BY case_type LIMIT 10"
    )

    result = validate_sql(sql, ALLOWED)

    assert result.allowed
    assert result.referenced_tables == ("voc_cases",)


def test_allows_identifier_inside_count_aggregate() -> None:
    result = validate_sql(
        "SELECT COUNT(account_id) AS new_account_count FROM accounts WHERE branch_id = 1 LIMIT 10",
        ALLOWED,
        branch_scope_branch_id=1,
    )

    assert result.allowed


def test_allows_grouped_event_query_without_direct_identifiers() -> None:
    result = validate_sql(
        "SELECT branch_id, channel, COUNT(*) AS account_count "
        "FROM accounts WHERE branch_id = 1 GROUP BY branch_id, channel LIMIT 10",
        ALLOWED,
        branch_scope_branch_id=1,
    )

    assert result.allowed
