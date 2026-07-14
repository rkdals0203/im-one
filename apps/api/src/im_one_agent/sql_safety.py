from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

try:
    import sqlglot
except ModuleNotFoundError:  # pragma: no cover - optional dependency path
    sqlglot = None


FORBIDDEN_PATTERNS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|attach|detach|pragma|vacuum|copy)\b",
    re.IGNORECASE,
)
DANGEROUS_FUNCTION_PATTERN = re.compile(r"\b(load_extension|readfile|writefile)\s*\(", re.IGNORECASE)
TABLE_PATTERN = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
TABLE_REFERENCE_PATTERN = re.compile(
    r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)"
    r"(?:\s+(?:as\s+)?([a-zA-Z_][a-zA-Z0-9_]*))?",
    re.IGNORECASE,
)
CTE_PATTERN = re.compile(r"(?:\bwith|,)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", re.IGNORECASE)
QUOTED_IDENTIFIER_PATTERN = r'"([^"]+)"|`([^`]+)`|\[([^\]]+)\]'
QUOTED_TABLE_PATTERN = re.compile(
    rf"\b(?:from|join)\s+(?:{QUOTED_IDENTIFIER_PATTERN})",
    re.IGNORECASE,
)
QUOTED_TABLE_REFERENCE_PATTERN = re.compile(
    rf"\b(?:from|join)\s+(?:{QUOTED_IDENTIFIER_PATTERN})"
    rf"(?:\s+(?:as\s+)?(?:(?:{QUOTED_IDENTIFIER_PATTERN})|([a-zA-Z_][a-zA-Z0-9_]*)))?",
    re.IGNORECASE,
)
QUOTED_CTE_PATTERN = re.compile(rf"(?:\bwith|,)\s+(?:{QUOTED_IDENTIFIER_PATTERN})\s+as\s*\(", re.IGNORECASE)
RECURSIVE_CTE_PATTERN = re.compile(r"\bwith\s+recursive\b", re.IGNORECASE)
SET_OPERATION_PATTERN = re.compile(r"\b(union|intersect|except)\b", re.IGNORECASE)
LIMIT_PATTERN = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)
OFFSET_PATTERN = re.compile(r"\boffset\s+(\d+)\b", re.IGNORECASE)
WHERE_PATTERN = re.compile(r"\bwhere\b", re.IGNORECASE)
SCOPE_INSERT_CLAUSE_PATTERN = re.compile(r"\b(group\s+by|order\s+by|limit)\b", re.IGNORECASE)
GROUP_BY_PATTERN = re.compile(r"\bgroup\s+by\b", re.IGNORECASE)
AGGREGATE_FUNCTION_PATTERN = re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE)
AGGREGATE_CALL_PATTERN = re.compile(r"\b(?:count|sum|avg|min|max)\s*\([^)]*\)", re.IGNORECASE)
SELECT_CLAUSE_PATTERN = re.compile(r"\bselect\b(?P<select>.*?)\bfrom\b", re.IGNORECASE | re.DOTALL)
NESTED_SELECT_PATTERN = re.compile(r"\(\s*select\b", re.IGNORECASE)
SELECT_STAR_PATTERN = re.compile(r"(^|,)\s*(?:[a-zA-Z_][a-zA-Z0-9_]*\.)?\*", re.IGNORECASE)
OR_OPERATOR_PATTERN = re.compile(r"\bor\b", re.IGNORECASE)
BRANCH_SCOPE_ANY_PATTERN = re.compile(
    r"\b(?:[a-zA-Z_][a-zA-Z0-9_]*\.)?branch_id\s*=\s*(\d+)\b",
    re.IGNORECASE,
)
QUALIFIED_BRANCH_SCOPE_PATTERN = re.compile(
    r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.branch_id\s*=\s*(\d+)\b",
    re.IGNORECASE,
)
UNQUALIFIED_BRANCH_SCOPE_PATTERN = re.compile(r"(?<!\.)\bbranch_id\s*=\s*(\d+)\b", re.IGNORECASE)
BRANCH_JOIN_PATTERN = re.compile(
    r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.branch_id\s*=\s*([a-zA-Z_][a-zA-Z0-9_]*)\.branch_id\b",
    re.IGNORECASE,
)
CROSS_JOIN_PATTERN = re.compile(r"\bcross\s+join\b", re.IGNORECASE)
TAUTOLOGICAL_JOIN_PATTERN = re.compile(
    r"\bjoin\s+[a-zA-Z_][a-zA-Z0-9_]*"
    r"(?:\s+(?:as\s+)?[a-zA-Z_][a-zA-Z0-9_]*)?"
    r"\s+on\s+(?:1\s*=\s*1|true)\b",
    re.IGNORECASE,
)
ORDER_BY_RANDOM_PATTERN = re.compile(r"\border\s+by\s+random\s*\(", re.IGNORECASE)
FROM_CLAUSE_PATTERN = re.compile(
    r"\bfrom\b(?P<from_clause>.*?)(?:\bwhere\b|\bgroup\s+by\b|\border\s+by\b|\blimit\b|$)",
    re.IGNORECASE | re.DOTALL,
)
SQL_KEYWORDS = {
    "cross",
    "full",
    "group",
    "inner",
    "join",
    "left",
    "limit",
    "on",
    "order",
    "right",
    "where",
}
SENSITIVE_ROW_COLUMNS = (
    "account_id",
    "sale_id",
    "case_id",
    "review_id",
    "target_id",
    "audit_id",
)
BRANCH_SCOPED_TABLES = {
    "accounts",
    "product_sales",
    "voc_cases",
    "investment_reviews",
    "branch_targets",
    "branches",
}
DETAIL_EVENT_TABLES = {
    "accounts",
    "product_sales",
    "voc_cases",
    "investment_reviews",
}
OPERATIONAL_ONLY_TABLES = {
    "query_audit_log",
}
EVENT_DETAIL_COLUMNS = (
    "branch_id",
    "opened_at",
    "channel",
    "customer_segment",
    "age_band",
    "risk_profile_band",
    "is_first_account",
    "sold_at",
    "product_type",
    "risk_grade",
    "amount",
    "suitability_checked",
    "cooling_off_eligible",
    "case_type",
    "status",
    "received_at",
    "resolved_at",
    "severity",
    "sla_due_at",
    "review_type",
    "created_at",
    "due_at",
)


@dataclass(frozen=True)
class ValidationResult:
    allowed: bool
    sql: str
    issues: tuple[str, ...]
    referenced_tables: tuple[str, ...]


@dataclass(frozen=True)
class TableReference:
    table_name: str
    alias: str


@dataclass(frozen=True)
class QueryAst:
    statement_type: str
    cte_names: tuple[str, ...]
    referenced_tables: tuple[str, ...]
    limit: int | None
    offset: int | None
    has_comments: bool
    has_semicolon: bool
    has_select_star: bool
    has_or_operator: bool
    has_cartesian_join_pattern: bool
    has_nested_subquery: bool
    has_random_order: bool
    has_recursive_cte: bool
    has_set_operation: bool
    dangerous_functions: tuple[str, ...]
    has_group_by: bool
    has_aggregate_function: bool
    forbidden_keywords: tuple[str, ...]
    branch_scope_branch_ids: tuple[int, ...]
    selected_sensitive_columns: tuple[str, ...]
    selected_event_detail_columns: tuple[str, ...]
    table_references: tuple[TableReference, ...]
    branch_join_pairs: tuple[tuple[str, str], ...]
    qualified_branch_scope_ids: tuple[tuple[str, int], ...]
    unqualified_branch_scope_ids: tuple[int, ...]


def parse_query_ast(sql: str) -> QueryAst:
    normalized_sql = sql.strip()
    policy_sql = mask_sql_literals(normalized_sql)
    first_token_match = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)", normalized_sql)
    statement_type = first_token_match.group(1).lower() if first_token_match else ""
    cte_names = tuple(sorted(set(CTE_PATTERN.findall(policy_sql)) | set(find_quoted_cte_names(policy_sql))))
    referenced_tables = tuple(
        sorted(
            (set(TABLE_PATTERN.findall(policy_sql)) | set(find_quoted_table_names(policy_sql)))
            - set(cte_names)
        )
    )
    limit = find_top_level_numeric_clause(policy_sql, "limit")
    offset = find_top_level_numeric_clause(policy_sql, "offset")
    select_clauses = SELECT_CLAUSE_PATTERN.findall(policy_sql)
    forbidden_keywords = tuple(
        sorted({match.group(1).lower() for match in FORBIDDEN_PATTERNS.finditer(policy_sql)})
    )

    return QueryAst(
        statement_type=statement_type,
        cte_names=cte_names,
        referenced_tables=referenced_tables,
        limit=limit,
        offset=offset,
        has_comments="--" in policy_sql or "/*" in policy_sql or "*/" in policy_sql,
        has_semicolon=";" in policy_sql,
        has_select_star=any(SELECT_STAR_PATTERN.search(select_clause) for select_clause in select_clauses),
        has_or_operator=bool(OR_OPERATOR_PATTERN.search(policy_sql)),
        has_cartesian_join_pattern=has_cartesian_join_pattern(policy_sql),
        has_nested_subquery=has_nested_subquery_pattern(policy_sql),
        has_random_order=bool(ORDER_BY_RANDOM_PATTERN.search(policy_sql)),
        has_recursive_cte=bool(RECURSIVE_CTE_PATTERN.search(policy_sql)),
        has_set_operation=bool(SET_OPERATION_PATTERN.search(policy_sql)),
        dangerous_functions=tuple(
            sorted({match.group(1).lower() for match in DANGEROUS_FUNCTION_PATTERN.finditer(policy_sql)})
        ),
        has_group_by=bool(GROUP_BY_PATTERN.search(policy_sql)),
        has_aggregate_function=bool(AGGREGATE_FUNCTION_PATTERN.search(policy_sql)),
        forbidden_keywords=forbidden_keywords,
        branch_scope_branch_ids=tuple(sorted({int(match) for match in BRANCH_SCOPE_ANY_PATTERN.findall(policy_sql)})),
        selected_sensitive_columns=find_selected_sensitive_columns(select_clauses),
        selected_event_detail_columns=find_selected_event_detail_columns(select_clauses),
        table_references=find_table_references(policy_sql, cte_names),
        branch_join_pairs=find_branch_join_pairs(policy_sql),
        qualified_branch_scope_ids=find_qualified_branch_scope_ids(policy_sql),
        unqualified_branch_scope_ids=tuple(
            sorted({int(match) for match in UNQUALIFIED_BRANCH_SCOPE_PATTERN.findall(policy_sql)})
        ),
    )


def mask_sql_literals(sql: str) -> str:
    chars = list(sql)
    index = 0
    while index < len(chars):
        if chars[index] != "'":
            index += 1
            continue

        chars[index] = " "
        index += 1
        while index < len(chars):
            if chars[index] != "'":
                chars[index] = " "
                index += 1
                continue

            chars[index] = " "
            if index + 1 < len(chars) and chars[index + 1] == "'":
                chars[index + 1] = " "
                index += 2
                continue
            index += 1
            break

    return "".join(chars)


def find_top_level_numeric_clause(sql: str, keyword: str) -> int | None:
    pattern = re.compile(rf"\b{re.escape(keyword)}\s+(\d+)\b", re.IGNORECASE)
    depth = 0
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "(":
            depth += 1
            index += 1
            continue
        if char == ")":
            depth = max(depth - 1, 0)
            index += 1
            continue
        if depth == 0:
            match = pattern.match(sql, index)
            if match:
                return int(match.group(1))
        index += 1
    return None


def has_cartesian_join_pattern(sql: str) -> bool:
    if CROSS_JOIN_PATTERN.search(sql) or TAUTOLOGICAL_JOIN_PATTERN.search(sql):
        return True

    for match in FROM_CLAUSE_PATTERN.finditer(sql):
        from_clause = match.group("from_clause")
        if "," in from_clause and " join " not in f" {from_clause.lower()} ":
            return True

    return False


def has_nested_subquery_pattern(sql: str) -> bool:
    for match in NESTED_SELECT_PATTERN.finditer(sql):
        before = sql[: match.start()]
        if is_cte_body_start(before):
            continue
        return True
    return False


def is_cte_body_start(text_before_open_parenthesis: str) -> bool:
    tail = text_before_open_parenthesis[-160:]
    return bool(
        re.search(
            r"(?:\bwith|,)\s+[a-zA-Z_][a-zA-Z0-9_]*(?:\s*\([^)]*\))?\s+as\s*$",
            tail,
            re.IGNORECASE | re.DOTALL,
        )
    )


def find_table_references(sql: str, cte_names: tuple[str, ...]) -> tuple[TableReference, ...]:
    references: list[TableReference] = []
    cte_name_set = set(cte_names)
    for match in TABLE_REFERENCE_PATTERN.finditer(sql):
        table_name = match.group(1)
        if table_name in cte_name_set:
            continue
        raw_alias = match.group(2)
        alias = table_name
        if raw_alias and raw_alias.lower() not in SQL_KEYWORDS:
            alias = raw_alias
        references.append(TableReference(table_name=table_name, alias=alias))
    for table_name, raw_alias in find_quoted_table_references(sql):
        if table_name in cte_name_set:
            continue
        alias = table_name
        if raw_alias and raw_alias.lower() not in SQL_KEYWORDS:
            alias = raw_alias
        references.append(TableReference(table_name=table_name, alias=alias))
    return tuple(references)


def first_identifier(groups: tuple[str | None, ...]) -> str | None:
    return next((group for group in groups if group), None)


def find_quoted_table_names(sql: str) -> tuple[str, ...]:
    names = []
    for match in QUOTED_TABLE_PATTERN.finditer(sql):
        name = first_identifier(match.groups())
        if name:
            names.append(name)
    return tuple(names)


def find_quoted_cte_names(sql: str) -> tuple[str, ...]:
    names = []
    for match in QUOTED_CTE_PATTERN.finditer(sql):
        name = first_identifier(match.groups())
        if name:
            names.append(name)
    return tuple(names)


def find_quoted_table_references(sql: str) -> tuple[tuple[str, str | None], ...]:
    references = []
    for match in QUOTED_TABLE_REFERENCE_PATTERN.finditer(sql):
        groups = match.groups()
        table_name = first_identifier(groups[:3])
        alias = first_identifier(groups[3:])
        if table_name:
            references.append((table_name, alias))
    return tuple(references)


def find_branch_join_pairs(sql: str) -> tuple[tuple[str, str], ...]:
    pairs: set[tuple[str, str]] = set()
    for left_alias, right_alias in BRANCH_JOIN_PATTERN.findall(sql):
        ordered = tuple(sorted((left_alias, right_alias)))
        pairs.add((ordered[0], ordered[1]))
    return tuple(sorted(pairs))


def find_qualified_branch_scope_ids(sql: str) -> tuple[tuple[str, int], ...]:
    return tuple(
        sorted(
            {
                (alias, int(branch_id))
                for alias, branch_id in QUALIFIED_BRANCH_SCOPE_PATTERN.findall(sql)
            }
        )
    )


def find_selected_sensitive_columns(select_clauses: list[str]) -> tuple[str, ...]:
    selected_columns: set[str] = set()
    for select_clause in select_clauses:
        searchable_clause = select_clause
        for column in SENSITIVE_ROW_COLUMNS:
            aggregate_pattern = re.compile(
                rf"\bcount\s*\(\s*(?:distinct\s+)?(?:[a-zA-Z_][a-zA-Z0-9_]*\.)?{column}\s*\)",
                re.IGNORECASE,
            )
            searchable_clause = aggregate_pattern.sub("", searchable_clause)
            if re.search(rf"\b(?:[a-zA-Z_][a-zA-Z0-9_]*\.)?{column}\b", searchable_clause, re.IGNORECASE):
                selected_columns.add(column)
    return tuple(sorted(selected_columns))


def find_selected_event_detail_columns(select_clauses: list[str]) -> tuple[str, ...]:
    selected_columns: set[str] = set()
    for select_clause in select_clauses:
        searchable_clause = AGGREGATE_CALL_PATTERN.sub("", select_clause)
        for column in EVENT_DETAIL_COLUMNS:
            if re.search(rf"\b(?:[a-zA-Z_][a-zA-Z0-9_]*\.)?{column}\b", searchable_clause, re.IGNORECASE):
                selected_columns.add(column)
    return tuple(sorted(selected_columns))


def find_unscoped_branch_aliases(ast: QueryAst, branch_scope_branch_id: int) -> tuple[str, ...]:
    branch_aliases = {
        reference.alias: reference.table_name
        for reference in ast.table_references
        if reference.table_name in BRANCH_SCOPED_TABLES
    }
    if not branch_aliases:
        return ()

    scoped_aliases = {
        alias
        for alias, branch_id in ast.qualified_branch_scope_ids
        if branch_id == branch_scope_branch_id and alias in branch_aliases
    }

    if branch_scope_branch_id in ast.unqualified_branch_scope_ids:
        if len(branch_aliases) == 1:
            scoped_aliases.update(branch_aliases)
        else:
            scoped_aliases.update(
                alias for alias, table_name in branch_aliases.items() if alias == table_name
            )

    joined_aliases = {alias: set[str]() for alias in branch_aliases}
    for left_alias, right_alias in ast.branch_join_pairs:
        if left_alias in joined_aliases and right_alias in joined_aliases:
            joined_aliases[left_alias].add(right_alias)
            joined_aliases[right_alias].add(left_alias)

    pending = list(scoped_aliases)
    while pending:
        alias = pending.pop()
        for joined_alias in joined_aliases.get(alias, set()):
            if joined_alias not in scoped_aliases:
                scoped_aliases.add(joined_alias)
                pending.append(joined_alias)

    return tuple(sorted(set(branch_aliases) - scoped_aliases))


def validate_sql(
    sql: str,
    allowed_tables: set[str],
    connection: sqlite3.Connection | None = None,
    max_limit: int = 100,
    max_offset: int = 1000,
    branch_scope_branch_id: int | None = None,
) -> ValidationResult:
    normalized_sql = sql.strip()
    issues: list[str] = []

    if not normalized_sql:
        return ValidationResult(False, normalized_sql, ("SQL이 비어 있습니다.",), ())

    ast = parse_query_ast(normalized_sql)

    if ast.statement_type not in {"select", "with"}:
        issues.append("읽기 전용 SELECT/WITH 조회만 허용됩니다.")

    if ast.has_semicolon:
        issues.append("단일 SQL 문만 허용되며 세미콜론은 사용할 수 없습니다.")

    if ast.has_comments:
        issues.append("SQL 주석은 허용되지 않습니다.")

    if ast.forbidden_keywords:
        issues.append("데이터 변경 또는 운영 위험 명령은 허용되지 않습니다.")

    if ast.has_select_star:
        issues.append("SELECT * 대신 필요한 컬럼만 명시해야 합니다.")

    if ast.has_cartesian_join_pattern:
        issues.append("과도한 조회 위험이 있는 카티션 조인(CROSS JOIN, ON 1=1, comma join)은 허용되지 않습니다.")

    if ast.has_nested_subquery:
        issues.append("검증 가능한 조회 흐름을 위해 nested subquery는 허용하지 않습니다. WITH CTE로 분리해야 합니다.")

    if ast.has_random_order:
        issues.append("전체 결과 정렬 비용이 큰 ORDER BY RANDOM()은 허용되지 않습니다.")

    if ast.has_recursive_cte:
        issues.append("장시간 실행 위험이 있는 WITH RECURSIVE 쿼리는 허용되지 않습니다.")

    if ast.has_set_operation:
        issues.append("검증 가능한 단일 조회 흐름을 위해 UNION/INTERSECT/EXCEPT set operation은 허용되지 않습니다.")

    if ast.dangerous_functions:
        issues.append(
            "SQLite 파일/확장 함수는 허용되지 않습니다: "
            + ", ".join(ast.dangerous_functions)
        )

    if ast.selected_sensitive_columns:
        issues.append(
            "row-level 식별자는 직접 조회할 수 없습니다: "
            + ", ".join(ast.selected_sensitive_columns)
            + ". 집계 결과로 변환해야 합니다."
        )

    referenced_tables = ast.referenced_tables
    unknown_tables = [table for table in referenced_tables if table not in allowed_tables]
    if unknown_tables:
        issues.append(f"허용되지 않은 테이블 참조: {', '.join(unknown_tables)}")

    operational_tables = sorted(OPERATIONAL_ONLY_TABLES.intersection(referenced_tables))
    if operational_tables:
        issues.append(
            "운영 감사/통제 테이블은 사용자 자연어 질의 대상이 아닙니다: "
            + ", ".join(operational_tables)
        )

    detail_event_tables = DETAIL_EVENT_TABLES.intersection(referenced_tables)
    if detail_event_tables and not (ast.has_group_by or ast.has_aggregate_function):
        issues.append(
            "고객/거래 이벤트 테이블은 집계 또는 GROUP BY 중심으로만 조회할 수 있습니다: "
            + ", ".join(sorted(detail_event_tables))
        )
    if detail_event_tables and ast.has_aggregate_function and not ast.has_group_by and ast.selected_event_detail_columns:
        issues.append(
            "집계 결과와 row-level 상세 컬럼을 함께 조회할 수 없습니다. GROUP BY로 집계 단위를 명시해야 합니다: "
            + ", ".join(ast.selected_event_detail_columns)
        )

    if branch_scope_branch_id is not None and BRANCH_SCOPED_TABLES.intersection(referenced_tables):
        scoped_branch_ids = set(ast.branch_scope_branch_ids)
        if branch_scope_branch_id not in scoped_branch_ids:
            issues.append(f"branch_manager 권한은 branch_id = {branch_scope_branch_id} 범위 조건이 필요합니다.")
        unexpected_branch_ids = sorted(scoped_branch_ids - {branch_scope_branch_id})
        if unexpected_branch_ids:
            issues.append(
                "branch_manager 권한에서 다른 branch_id 조건은 허용되지 않습니다: "
                + ", ".join(str(branch_id) for branch_id in unexpected_branch_ids)
            )
        if ast.has_or_operator:
            issues.append("branch_manager 권한에서는 지점 범위를 넓힐 수 있는 OR 조건을 허용하지 않습니다.")
        unscoped_aliases = find_unscoped_branch_aliases(ast, branch_scope_branch_id)
        if unscoped_aliases:
            issues.append(
                "branch_manager 권한에서 지점 범위가 연결되지 않은 테이블 alias가 있습니다: "
                + ", ".join(unscoped_aliases)
            )

    if ast.limit is None:
        issues.append("조회량 제한을 위해 LIMIT 절이 필요합니다.")
    elif ast.limit <= 0:
        issues.append("LIMIT은 1 이상이어야 합니다.")
    elif ast.limit > max_limit:
        issues.append(f"LIMIT은 {max_limit} 이하만 허용됩니다.")

    if ast.offset is not None and ast.offset > max_offset:
        issues.append(f"OFFSET은 {max_offset} 이하만 허용됩니다.")

    if not issues:
        issues.extend(validate_sql_parser(normalized_sql))

    if connection is not None and not issues:
        try:
            connection.execute(f"EXPLAIN QUERY PLAN {normalized_sql}")
        except sqlite3.Error as exc:
            issues.append(f"SQL 문법 또는 실행 계획 오류: {exc}")

    return ValidationResult(
        allowed=not issues,
        sql=normalized_sql,
        issues=tuple(issues),
        referenced_tables=referenced_tables,
    )


def apply_branch_scope_filter(sql: str, branch_scope_branch_id: int) -> str:
    normalized_sql = sql.strip()
    policy_sql = mask_sql_literals(normalized_sql)
    ast = parse_query_ast(normalized_sql)

    if ast.statement_type != "select":
        return normalized_sql
    if ast.has_nested_subquery:
        return normalized_sql
    if ast.has_or_operator:
        return normalized_sql

    scoped_branch_ids = set(ast.branch_scope_branch_ids)
    if branch_scope_branch_id in scoped_branch_ids:
        return normalized_sql
    if scoped_branch_ids:
        return normalized_sql

    branch_references = [
        reference
        for reference in ast.table_references
        if reference.table_name in BRANCH_SCOPED_TABLES
    ]
    if not branch_references:
        return normalized_sql

    preferred_reference = next(
        (reference for reference in branch_references if reference.table_name != "branches"),
        branch_references[0],
    )
    predicate = f"{preferred_reference.alias}.branch_id = {branch_scope_branch_id}"

    insert_match = SCOPE_INSERT_CLAUSE_PATTERN.search(policy_sql)
    if insert_match:
        head = normalized_sql[: insert_match.start()].rstrip()
        tail = normalized_sql[insert_match.start():].lstrip()
        policy_head = policy_sql[: insert_match.start()].rstrip()
    else:
        head = normalized_sql
        tail = ""
        policy_head = policy_sql

    if WHERE_PATTERN.search(policy_head):
        scoped_head = f"{head} AND {predicate}"
    else:
        scoped_head = f"{head} WHERE {predicate}"

    return f"{scoped_head} {tail}".strip()


def validate_sql_parser(sql: str) -> list[str]:
    if sqlglot is None:
        return []

    try:
        sqlglot.parse_one(sql, read="sqlite")
    except Exception as exc:
        return [f"SQL parser 검증 오류: {exc}"]

    return []
