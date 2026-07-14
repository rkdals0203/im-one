from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import im_one_agent.evidence as evidence_module
import im_one_agent.preflight as preflight_module
from im_one_agent.database_backend import QueryExecutionResult
from im_one_agent.evaluation import (
    EVALUATION_CASES,
    build_evaluation_diff_summary,
    build_evaluation_summary,
    build_gold_snapshot,
    build_verified_question_manifest,
    conversation_context_for_seed_case,
    evaluation_threshold_failures,
    gold_sql_for_case,
    run_evaluation,
    write_evaluation_report,
    write_evaluation_diff_summary,
    write_evaluation_markdown_summary,
)
from im_one_agent.graph import StateGraph, build_agent, execute_sql_node, execution_status_for, query_timeout_ms, write_audit_node
from im_one_agent.preflight import (
    audit_append_only_errors,
    build_preflight_report,
    check_database_backend_policy,
    check_demo_query_latency,
    check_evaluation_readiness,
    check_gold_coverage,
    check_health_disclosure_policy,
    check_llm_prompt_policy,
    check_query_plan_policy,
    check_prd_traceability,
    check_result_explanation_policy,
    check_schema_retrieval_policy,
    check_sql_validation_policy,
    check_static_ui_assets,
    check_synthetic_dataset_metadata,
    check_synthetic_data_policy,
    check_trace_audit_policy,
    check_web_api_auth_policy,
    preflight_requirements_for_profile,
    run_preflight,
)
from im_one_agent.response import build_explanation, format_rows
from im_one_agent.sample_data import (
    REQUIRED_DATASET_METADATA,
    connect_database,
    database_has_required_dataset_metadata,
    database_has_required_schema,
    ensure_demo_database,
    initialize_demo_database,
)
from im_one_agent.domain import DEFAULT_BRANCH_ID, TABLES, normalize_branch_id
from im_one_agent.schema_retrieval import RetrievalScore, SchemaContext, retrieve_schema
from im_one_agent.sql_generator import DEFAULT_LLM_MODEL, PROMPT_VERSION, GeneratedSQL
from im_one_agent.sql_safety import ValidationResult


def table_count(db_path, table_name: str) -> int:
    connection = connect_database(db_path)
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
    finally:
        connection.close()


def test_agent_uses_installed_langgraph_runtime() -> None:
    assert StateGraph is not None
    agent = build_agent()
    assert type(agent).__name__ == "CompiledStateGraph"
    graph_nodes = set(agent.get_graph().nodes)
    assert "question_intake" in graph_nodes
    assert {"retrieve_schema", "generate_sql", "validate_sql", "write_audit"}.issubset(graph_nodes)


def test_demo_database_matches_v2_prd_scale(tmp_path) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)

    assert 8 <= table_count(db_path, "branches") <= 12
    assert 1000 <= table_count(db_path, "accounts") <= 5000
    assert 1000 <= table_count(db_path, "product_sales") <= 3000
    assert 300 <= table_count(db_path, "voc_cases") <= 800
    assert 500 <= table_count(db_path, "investment_reviews") <= 1500
    assert table_count(db_path, "branch_targets") == table_count(db_path, "branches") * 12 * 3

    connection = connect_database(db_path)
    try:
        assert database_has_required_dataset_metadata(connection)
        metadata = {
            row["metadata_key"]: row["metadata_value"]
            for row in connection.execute("SELECT metadata_key, metadata_value FROM demo_dataset_metadata")
        }
    finally:
        connection.close()
    assert metadata["dataset_classification"] == "synthetic_poc"
    assert metadata["contains_real_customer_data"] == "false"
    assert metadata["contains_real_account_numbers"] == "false"
    assert metadata["contains_real_employee_data"] == "false"
    assert metadata["contains_real_branch_performance"] == "false"
    assert metadata["as_of_date"] == REQUIRED_DATASET_METADATA["as_of_date"]


def test_ensure_demo_database_handles_parallel_initialization(tmp_path) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: ensure_demo_database(db_path), range(4)))

    assert 8 <= table_count(db_path, "branches") <= 12
    assert 1000 <= table_count(db_path, "accounts") <= 5000
    assert table_count(db_path, "branch_targets") == table_count(db_path, "branches") * 12 * 3


def test_evaluation_set_covers_prd_minimum_cases() -> None:
    assert len(EVALUATION_CASES) >= 30
    assert sum(1 for case in EVALUATION_CASES if case.should_block) >= 5
    follow_up_cases = [case for case in EVALUATION_CASES if case.case_id.startswith("follow-")]
    assert len(follow_up_cases) >= 5
    assert all(case.conversation_seed_case_id for case in follow_up_cases)
    assert any(case.intent == "els_sales_vs_voc" for case in EVALUATION_CASES)
    assert any(case.intent == "branch_targets" for case in EVALUATION_CASES)


def test_follow_up_evaluation_cases_build_seed_context(tmp_path) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)
    case_by_id = {case.case_id: case for case in EVALUATION_CASES}
    follow_up_cases = [case for case in EVALUATION_CASES if case.case_id.startswith("follow-")]

    for case in follow_up_cases:
        context = conversation_context_for_seed_case(case, case_by_id, str(db_path))
        assert context is not None
        assert context["previous_validation_allowed"] is True
        assert context["previous_sql"]
        assert context["previous_columns"]
        assert context["previous_tables"]


def test_demo_documents_reflect_current_poc_capabilities() -> None:
    demo_script = Path("docs/demo_script.md").read_text(encoding="utf-8")
    poc_brief = Path("docs/poc_brief.md").read_text(encoding="utf-8")

    for required in (
        "LangGraph Supervisor",
        "업무지식 에이전트",
        "생성 SQL은 읽기 전용",
        "추천 차트",
        "확인하고 실행",
        "http://127.0.0.1:8000",
        "make test",
    ):
        assert required in demo_script

    for required in (
        "React + FastAPI",
        "세 전문 에이전트",
        "role-based access control",
        "자동 시각화",
        "확인 토큰",
        "합성 데이터",
    ):
        assert required in poc_brief


def test_result_explanation_includes_metric_definition_and_period() -> None:
    context = retrieve_schema("최근 30일 VOC 유형별 처리 현황 알려줘.")
    explanation = build_explanation(
        question="최근 30일 VOC 유형별 처리 현황 알려줘.",
        context=context,
        validation=ValidationResult(True, "SELECT case_type FROM voc_cases LIMIT 10", (), ("voc_cases",)),
        row_count=3,
        generation_reason="VOC 처리 상태를 집계합니다.",
    )

    assert "지표 정의:" in explanation
    assert "해석 신뢰도:" in explanation
    assert "확인 질문 제안:" in explanation
    assert "COUNT(voc_cases.case_id)" in explanation
    assert "기간 기준:" in explanation
    assert "voc_cases.received_at / 최근 30일" in explanation
    assert "집계 기준:" in explanation
    assert "VOC 유형, 처리 상태" in explanation
    assert "필터 기준:" in explanation
    assert "검증 근거:" in explanation
    assert "읽기 전용 SELECT/WITH" in explanation
    assert "허용 테이블 whitelist" in explanation
    assert "voc_cases" in explanation
    assert "합성 데이터 기반 POC" in explanation


def test_ambiguous_question_adds_system_assumption_to_result(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "ambiguous.sqlite"
    audit_path = tmp_path / "ambiguous_audit.jsonl"
    initialize_demo_database(db_path)

    def fake_generate_sql(*args, **kwargs):
        return GeneratedSQL(
            sql=(
                "SELECT v.case_type, v.status, COUNT(*) AS case_count "
                "FROM voc_cases v WHERE v.branch_id = 1 "
                "GROUP BY v.case_type, v.status LIMIT 10"
            ),
            reason="모호한 가입 현황 질문을 VOC 처리 현황 기준으로 해석합니다.",
            engine="llm",
            model=DEFAULT_LLM_MODEL,
            assumptions=(),
        )

    monkeypatch.setattr("im_one_agent.graph.generate_sql", fake_generate_sql)

    result = build_agent().invoke(
        {
            "question": "가입 현황 알려줘.",
            "user_role": "branch_manager",
            "branch_id": 1,
            "conversation_context": {},
            "db_path": str(db_path),
            "audit_path": str(audit_path),
        }
    )

    assert result["validation"].allowed
    assert result["context"].retrieval_confidence == "low"
    assert result["context"].clarification_options
    assert any("질문이 모호해" in assumption for assumption in result["generated"].assumptions)
    assert "확인 질문을 선택하세요" in result["explanation"]


def test_graph_passes_generated_semicolon_to_validation_without_trimming(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "semicolon.sqlite"
    audit_path = tmp_path / "semicolon_audit.jsonl"
    initialize_demo_database(db_path)

    def fake_generate_sql(*args, **kwargs):
        return GeneratedSQL(
            sql="SELECT v.case_type FROM voc_cases v WHERE v.branch_id = 1 LIMIT 10;",
            reason="LLM이 세미콜론을 포함한 SQL을 반환한 경우를 검증합니다.",
            engine="llm",
            model=DEFAULT_LLM_MODEL,
        )

    monkeypatch.setattr("im_one_agent.graph.generate_sql", fake_generate_sql)

    result = build_agent().invoke(
        {
            "question": "최근 30일 VOC 유형별 처리 현황 알려줘.",
            "user_role": "branch_manager",
            "branch_id": 1,
            "conversation_context": {},
            "db_path": str(db_path),
            "audit_path": str(audit_path),
        }
    )

    assert not result["validation"].allowed
    assert result["validation"].sql.endswith(";")
    assert any("세미콜론" in issue for issue in result["validation"].issues)
    assert result["rows"] == []


def test_empty_result_is_explained_as_no_matching_data() -> None:
    assert format_rows(["branch_name"], []) == "조건에 맞는 데이터가 없습니다."


def test_blocked_evaluation_cases_pass_without_llm(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    blocked_cases = tuple(case for case in EVALUATION_CASES if case.should_block)

    results = run_evaluation(str(db_path), str(audit_path), cases=blocked_cases)

    assert blocked_cases
    assert all(result.passed for result in results)
    assert all(not result.allowed for result in results)
    assert all("위험 요청 차단" in " ".join(result.issues) for result in results)


def test_evaluation_report_contains_prd_summary_metrics(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    report_path = tmp_path / "evaluation_report.json"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    blocked_cases = tuple(case for case in EVALUATION_CASES if case.should_block)

    results = run_evaluation(str(db_path), str(audit_path), cases=blocked_cases)
    summary = build_evaluation_summary(results)
    write_evaluation_report(results, report_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert summary["blocked_total"] == len(blocked_cases)
    assert summary["blocked_rejection_rate"] == 1.0
    assert payload["summary"]["blocked_rejection_rate"] == 1.0
    assert "non_blocked_execution_success_rate" in payload["summary"]
    assert "core_demo_success_rate" in payload["summary"]
    assert payload["summary"]["latency_target_ms"] == 10000.0
    assert payload["summary"]["latency_success_rate"] == 1.0
    assert payload["summary"]["max_elapsed_ms"] >= 0
    assert payload["case_metadata"]
    first_case_metadata = payload["case_metadata"][0]
    for required_key in (
        "question",
        "intent",
        "required_tables",
        "expected_metric",
        "expected_sql_pattern",
        "expected_result_shape",
        "should_block",
        "notes",
    ):
        assert required_key in first_case_metadata
    first_result = payload["results"][0]
    assert "expected_metric" in first_result
    assert "expected_result_shape" in first_result
    assert "expected_sql_pattern" in first_result
    assert "should_block" in first_result
    assert first_result["elapsed_ms"] >= 0


def test_evaluation_markdown_summary_contains_prd_scorecard(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    markdown_path = tmp_path / "evaluation_summary.md"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    blocked_cases = tuple(case for case in EVALUATION_CASES if case.should_block)

    results = run_evaluation(str(db_path), str(audit_path), cases=blocked_cases)
    write_evaluation_markdown_summary(results, markdown_path, cases=blocked_cases)
    markdown = markdown_path.read_text(encoding="utf-8")

    assert "# iM One NL2SQL Evaluation Summary" in markdown
    assert "PRD targets" in markdown
    assert "| Blocked rejection | 100.0% |" in markdown
    assert "## Failed Cases" in markdown
    assert "No failed cases" in markdown
    assert "| block | 7 |" in markdown


def test_evaluation_report_records_gold_differences_for_non_blocked_cases(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    report_path = tmp_path / "evaluation_report.json"
    diff_path = tmp_path / "evaluation_diff_summary.json"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    case = next(case for case in EVALUATION_CASES if case.case_id == "core-003")

    results = run_evaluation(str(db_path), str(audit_path), cases=(case,), role="sales_planning")
    summary = build_evaluation_summary(results, cases=(case,))
    diff_summary = build_evaluation_diff_summary(results, cases=(case,))
    write_evaluation_report(results, report_path, cases=(case,))
    write_evaluation_diff_summary(results, diff_path, cases=(case,))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    diff_payload = json.loads(diff_path.read_text(encoding="utf-8"))
    result = payload["results"][0]

    assert summary["gold_compared_total"] == 1
    assert summary["gold_mismatched_total"] == 1
    assert summary["gold_match_rate"] == 0.0
    assert result["gold_sql"]
    assert result["gold_columns"] == ["case_type", "status", "case_count"]
    assert result["gold_row_count"] > 0
    assert result["row_count_delta"] < 0
    assert result["first_mismatch"]["kind"] == "columns"
    assert "columns" in result
    assert "rows_sample" in result
    assert diff_summary["diff_case_total"] == 1
    assert diff_summary["gold_mismatched_total"] == 1
    assert diff_summary["llm_generation_failure_total"] == 1
    assert diff_payload["cases"][0]["case_id"] == "core-003"
    assert diff_payload["cases"][0]["failure_reasons"] == [
        "expected_execution_but_blocked",
        "missing_required_tables",
        "missing_expected_columns",
        "missing_expected_sql_fragments",
        "gold_result_mismatch",
        "llm_generation_failure",
    ]
    assert diff_payload["cases"][0]["first_mismatch"]["kind"] == "columns"
    assert diff_payload["cases"][0]["gold_sql"]


def test_evaluation_threshold_failures_enforce_prd_rates() -> None:
    summary = {
        "total_cases": 5,
        "core_demo_total": 5,
        "non_blocked_total": 5,
        "blocked_total": 0,
        "gold_compared_total": 5,
        "core_demo_success_rate": 0.8,
        "non_blocked_execution_success_rate": 0.69,
        "blocked_rejection_rate": 1.0,
        "pass_rate": 0.75,
        "latency_success_rate": 0.5,
    }

    failures = evaluation_threshold_failures(
        summary,
        min_total_cases=30,
        min_core_demo_total=5,
        min_non_blocked_total=30,
        min_blocked_total=2,
        min_gold_compared_total=30,
        min_core_demo_success_rate=1.0,
        min_non_blocked_execution_success_rate=0.7,
        min_blocked_rejection_rate=1.0,
        min_latency_success_rate=1.0,
    )

    assert "total_cases=5 below required 30" in failures
    assert "non_blocked_total=5 below required 30" in failures
    assert "blocked_total=0 below required 2" in failures
    assert "gold_compared_total=5 below required 30" in failures
    assert "core_demo_success_rate=0.8 below required 1.0" in failures
    assert "non_blocked_execution_success_rate=0.69 below required 0.7" in failures
    assert "latency_success_rate=0.5 below required 1.0" in failures
    assert all("blocked_rejection_rate" not in failure for failure in failures)


def test_gold_snapshots_cover_non_blocked_evaluation_cases(tmp_path) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)

    non_blocked_cases = [case for case in EVALUATION_CASES if not case.should_block]
    assert non_blocked_cases

    for case in non_blocked_cases:
        assert gold_sql_for_case(case, role="sales_planning")
        snapshot = build_gold_snapshot(case, str(db_path), role="sales_planning")
        assert snapshot.sql
        assert snapshot.columns
        assert all(set(snapshot.columns) == set(row) for row in snapshot.rows)


def test_preflight_gold_coverage_executes_gold_sql_and_expected_shapes(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)

    check = check_gold_coverage(str(db_path))

    assert check.passed
    assert check.required
    assert "expected_shapes=covered" in check.detail

    original_gold_sql_for_case = preflight_module.gold_sql_for_case

    def broken_gold_sql(case, *args, **kwargs):
        if case.case_id == "core-001":
            return "SELECT branch_name FROM branches LIMIT 1"
        return original_gold_sql_for_case(case, *args, **kwargs)

    monkeypatch.setattr(preflight_module, "gold_sql_for_case", broken_gold_sql)

    bad_check = preflight_module.check_gold_coverage(str(db_path))

    assert not bad_check.passed
    assert bad_check.required
    assert "core-001: gold result missing expected column(s): opened_month, new_account_count" in bad_check.detail


def test_preflight_gold_coverage_fails_when_gold_sql_does_not_execute(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)
    original_gold_sql_for_case = preflight_module.gold_sql_for_case

    def invalid_gold_sql(case, *args, **kwargs):
        if case.case_id == "core-002":
            return "SELECT missing_column FROM product_sales LIMIT 1"
        return original_gold_sql_for_case(case, *args, **kwargs)

    monkeypatch.setattr(preflight_module, "gold_sql_for_case", invalid_gold_sql)

    check = preflight_module.check_gold_coverage(str(db_path))

    assert not check.passed
    assert check.required
    assert "core-002: gold SQL execution failed" in check.detail


def test_verified_question_manifest_covers_gold_sql_and_safety_cases() -> None:
    manifest = build_verified_question_manifest(role="sales_planning")
    non_blocked_cases = [case for case in EVALUATION_CASES if not case.should_block]
    blocked_cases = [case for case in EVALUATION_CASES if case.should_block]
    source_case_ids = {item["source_case_id"] for item in manifest["verified_questions"]}

    assert manifest["role"] == "sales_planning"
    assert manifest["verified_total"] >= 100
    assert source_case_ids == {case.case_id for case in non_blocked_cases}
    assert manifest["safety_total"] == len(blocked_cases)
    assert all(item["gold_sql"] for item in manifest["verified_questions"])
    assert all(item["status"] == "verified" for item in manifest["verified_questions"])
    assert any(item["variant_type"] == "criteria" for item in manifest["verified_questions"])
    assert all(item["expected_behavior"] == "blocked" for item in manifest["safety_cases"])


def test_preflight_checks_evaluation_readiness(monkeypatch) -> None:
    check = check_evaluation_readiness()

    assert check.passed
    assert check.required
    assert "verified=" in check.detail
    assert "safety=" in check.detail

    monkeypatch.setattr(preflight_module, "EVALUATION_CASES", EVALUATION_CASES[:4])
    bad_check = preflight_module.check_evaluation_readiness()

    assert not bad_check.passed
    assert bad_check.required
    assert "below PRD minimum 30" in bad_check.detail
    assert "verified questions=" in bad_check.detail


def test_prd_wording_policy_blocks_internal_fallback_language(tmp_path, monkeypatch) -> None:
    prd_path = tmp_path / "prd.md"
    prd_path.write_text("# PRD\nLLM 기반 NL2SQL 제품 요구사항입니다.\n", encoding="utf-8")
    monkeypatch.setattr(preflight_module, "PRD_DOC_PATH", prd_path)

    clean_check = preflight_module.check_prd_wording_policy()

    assert clean_check.passed
    assert clean_check.required
    assert "forbidden_terms=0" in clean_check.detail

    prd_path.write_text(
        "규칙 기반 SQL 생성은 네트워크나 API 키가 없을 때 데모를 유지하기 위한 fallback일 뿐입니다.",
        encoding="utf-8",
    )

    bad_check = preflight_module.check_prd_wording_policy()

    assert not bad_check.passed
    assert bad_check.required
    assert "규칙 기반 SQL 생성" in bad_check.detail
    assert "네트워크나 API 키" in bad_check.detail


def test_graph_blocks_when_llm_is_not_configured(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    initialize_demo_database(db_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = build_agent().invoke(
        {
            "question": "최근 30일 VOC 유형별 처리 현황 알려줘.",
            "user_role": "branch_manager",
            "db_path": str(db_path),
            "audit_path": str(audit_path),
        }
    )

    assert not result["validation"].allowed
    assert any("LLM SQL 생성 실패" in issue for issue in result["validation"].issues)
    assert result["rows"] == []
    assert "재시도 안내" in result["answer"]
    assert "API 키" in result["answer"]

    connection = connect_database(db_path)
    try:
        audit_row = connection.execute(
            "SELECT user_role, original_question, selected_semantic_metrics, llm_model, prompt_version, "
            "validation_status, execution_status, row_count, blocked_reason "
            "FROM query_audit_log"
        ).fetchone()
    finally:
        connection.close()

    assert audit_row["user_role"] == "branch_manager"
    assert audit_row["original_question"] == "최근 30일 VOC 유형별 처리 현황 알려줘."
    assert "voc_status" in audit_row["selected_semantic_metrics"]
    assert audit_row["llm_model"] == DEFAULT_LLM_MODEL
    assert audit_row["prompt_version"] == PROMPT_VERSION
    assert audit_row["validation_status"] == "blocked"
    assert audit_row["execution_status"] == "blocked"
    assert audit_row["row_count"] == 0
    assert "LLM SQL 생성 실패" in audit_row["blocked_reason"]


def test_graph_question_intake_normalizes_question_identity_role_and_branch(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    initialize_demo_database(db_path)
    captured = {}

    def fake_generate_sql(question, *args, **kwargs):
        captured["question"] = question
        captured.update(kwargs)
        return GeneratedSQL(
            sql=(
                "SELECT v.case_type, COUNT(*) AS case_count "
                "FROM voc_cases v GROUP BY v.case_type LIMIT 10"
            ),
            reason="Question Intake 정규화 검증용 SQL입니다.",
            engine="llm",
            model=DEFAULT_LLM_MODEL,
        )

    monkeypatch.setattr("im_one_agent.graph.generate_sql", fake_generate_sql)

    result = build_agent().invoke(
        {
            "question": "  최근 30일 VOC 유형별 처리 현황 알려줘.  ",
            "user_id": "  kim.kangmin  ",
            "auth_mode": "  trusted_headers  ",
            "user_role": "",
            "branch_id": 999,
            "db_path": str(db_path),
            "audit_path": str(audit_path),
        }
    )

    assert result["question"] == "최근 30일 VOC 유형별 처리 현황 알려줘."
    assert result["user_id"] == "kim.kangmin"
    assert result["auth_mode"] == "trusted_headers"
    assert result["user_role"] == "branch_manager"
    assert result["branch_id"] == DEFAULT_BRANCH_ID
    assert captured["question"] == result["question"]
    assert captured["user_role"] == "branch_manager"
    assert captured["branch_id"] == DEFAULT_BRANCH_ID

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["original_question"] == "최근 30일 VOC 유형별 처리 현황 알려줘."
    assert event["user_id"] == "kim.kangmin"
    assert event["auth_mode"] == "trusted_headers"


def test_graph_question_intake_rejects_blank_questions(tmp_path) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)

    try:
        build_agent().invoke(
            {
                "question": "   ",
                "user_role": "sales_planning",
                "db_path": str(db_path),
                "audit_path": str(tmp_path / "audit.jsonl"),
            }
        )
    except ValueError as exc:
        assert "질문을 입력해주세요" in str(exc)
    else:
        raise AssertionError("blank graph question must fail in Question Intake")


def test_query_execution_timeout_blocks_long_running_sql(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IM_ONE_QUERY_TIMEOUT_MS", "1")
    sql = """
WITH RECURSIVE cnt(x) AS (
    VALUES(0)
    UNION ALL
    SELECT x + 1 FROM cnt WHERE x < 100000000
)
SELECT MAX(x) AS max_value FROM cnt
""".strip()

    result = execute_sql_node(
        {
            "db_path": str(tmp_path / "timeout.sqlite"),
            "validation": ValidationResult(True, sql, (), ()),
        }
    )

    assert query_timeout_ms() == 1
    assert not result["validation"].allowed
    assert result["rows"] == []
    assert "SQL 실행 시간 제한 초과" in result["validation"].issues[0]
    assert isinstance(result["execution_ms"], float)
    assert execution_status_for(result) == "failed"


def test_execute_sql_node_enforces_role_tables_at_db_boundary(tmp_path) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)
    context = SchemaContext(
        matched_metrics=(),
        tables=(TABLES["branches"],),
        business_rules=(),
        example_queries=(),
    )

    result = execute_sql_node(
        {
            "db_path": str(db_path),
            "context": context,
            "validation": ValidationResult(
                True,
                "SELECT COUNT(*) AS account_count FROM accounts LIMIT 10",
                (),
                ("accounts",),
            ),
        }
    )

    assert not result["validation"].allowed
    assert result["rows"] == []
    assert result["validation"].referenced_tables == ("accounts",)
    assert "DB 권한 정책 차단" in result["validation"].issues[0]
    assert "accounts" in result["validation"].issues[0]
    assert execution_status_for(result) == "blocked"


def test_runtime_execution_failure_is_audited_as_failed(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    initialize_demo_database(db_path)

    def fake_generate_sql(*args, **kwargs):
        return GeneratedSQL(
            sql="SELECT case_type, COUNT(*) AS case_count FROM voc_cases GROUP BY case_type LIMIT 10",
            reason="VOC 유형별 건수를 집계합니다.",
            engine="llm",
            model=DEFAULT_LLM_MODEL,
        )

    class FailingBackend:
        name = "sqlite"

        def execute_validated_sql(
            self,
            db_path: str,
            sql: str,
            timeout_ms: int,
            allowed_tables: set[str] | None = None,
        ) -> QueryExecutionResult:
            return QueryExecutionResult(
                rows=[],
                columns=[],
                column_metadata=[],
                query_plan_summary=["SCAN voc_cases"],
                pre_execution_row_count=4,
                pre_execution_row_count_status="checked",
                pre_execution_check_ms=0.2,
                execution_ms=3.5,
                error_issue="SQL 실행 오류: simulated runtime failure",
            )

    monkeypatch.setattr("im_one_agent.graph.generate_sql", fake_generate_sql)
    monkeypatch.setattr("im_one_agent.graph.execution_backend_for_name", lambda: FailingBackend())

    result = build_agent().invoke(
        {
            "question": "최근 30일 VOC 유형별 처리 현황 알려줘.",
            "user_role": "sales_planning",
            "db_path": str(db_path),
            "audit_path": str(audit_path),
        }
    )

    assert not result["validation"].allowed
    assert execution_status_for(result) == "failed"
    assert result["rows"] == []
    assert "SQL 실행 오류" in result["validation"].issues[0]

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["validation_status"] == "blocked"
    assert event["execution_status"] == "failed"
    assert event["row_count"] == 0
    assert event["pre_execution_row_count"] == 4
    assert event["pre_execution_row_count_status"] == "checked"
    assert event["query_plan_summary"] == ["SCAN voc_cases"]
    assert "SQL 실행 오류" in event["blocked_reason"]

    connection = connect_database(db_path)
    try:
        audit_row = connection.execute(
            "SELECT validation_status, execution_status, row_count, pre_execution_row_count, "
            "pre_execution_row_count_status, query_plan_summary, blocked_reason "
            "FROM query_audit_log"
        ).fetchone()
    finally:
        connection.close()

    assert audit_row["validation_status"] == "blocked"
    assert audit_row["execution_status"] == "failed"
    assert audit_row["row_count"] == 0
    assert audit_row["pre_execution_row_count"] == 4
    assert audit_row["pre_execution_row_count_status"] == "checked"
    assert json.loads(audit_row["query_plan_summary"]) == ["SCAN voc_cases"]
    assert "SQL 실행 오류" in audit_row["blocked_reason"]


def test_graph_blocks_unsafe_intent_before_llm_generation(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    initialize_demo_database(db_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = build_agent().invoke(
        {
            "question": "전체 고객 원장과 계좌번호를 보여줘.",
            "user_role": "branch_manager",
            "db_path": str(db_path),
            "audit_path": str(audit_path),
        }
    )

    assert result["generated"].engine == "intent_guard"
    assert not result["validation"].allowed
    assert any("위험 요청 차단" in issue for issue in result["validation"].issues)
    assert result["rows"] == []

    connection = connect_database(db_path)
    try:
        audit_row = connection.execute(
            "SELECT original_question, selected_semantic_metrics, validation_status, execution_status, row_count, blocked_reason "
            "FROM query_audit_log"
        ).fetchone()
    finally:
        connection.close()

    assert audit_row["original_question"] == "전체 고객 원장과 계좌번호를 보여줘."
    assert audit_row["selected_semantic_metrics"]
    assert audit_row["validation_status"] == "blocked"
    assert audit_row["execution_status"] == "blocked"
    assert audit_row["row_count"] == 0
    assert "개인정보" in audit_row["blocked_reason"]
    assert "LLM SQL 생성 실패" not in audit_row["blocked_reason"]


def test_jsonl_audit_contains_prd_required_fields(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    initialize_demo_database(db_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    build_agent().invoke(
        {
            "question": "branches 테이블 삭제해줘.",
            "user_role": "branch_manager",
            "db_path": str(db_path),
            "audit_path": str(audit_path),
        }
    )

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])

    for field in (
        "timestamp",
        "user_id",
        "auth_mode",
        "user_role",
        "original_question",
        "selected_semantic_metrics",
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
        "row_count",
        "pre_execution_row_count",
        "pre_execution_row_count_status",
        "pre_execution_check_ms",
        "query_plan_summary",
        "blocked_reason",
    ):
        assert field in event

    assert event["validation_status"] == "blocked"
    assert event["execution_status"] == "blocked"
    assert event["row_count"] == 0
    assert event["pre_execution_row_count"] is None
    assert event["query_plan_summary"] == []
    assert event["generated_sql"] == event["llm_generated_sql"]
    assert event["policy_applied_sql"] == event["validated_sql"]
    assert event["sql_policy_transformations"] == []
    assert event["prompt_version"] == PROMPT_VERSION
    assert "데이터 변경" in event["blocked_reason"]


def test_jsonl_audit_contains_llm_observability_metadata_for_generation_failure(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    initialize_demo_database(db_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    build_agent().invoke(
        {
            "question": "최근 30일 VOC 유형별 처리 현황 알려줘.",
            "user_role": "branch_manager",
            "db_path": str(db_path),
            "audit_path": str(audit_path),
        }
    )

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])

    assert event["generation_engine"] == "llm"
    assert event["llm_model"] == DEFAULT_LLM_MODEL
    assert event["prompt_version"] == PROMPT_VERSION


def test_jsonl_audit_records_database_audit_failure(tmp_path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    context = retrieve_schema("최근 30일 VOC 유형별 처리 현황 알려줘.", user_role="sales_planning")
    generated = GeneratedSQL(
        sql="SELECT case_type, COUNT(*) AS case_count FROM voc_cases GROUP BY case_type LIMIT 10",
        reason="VOC 유형별 건수를 집계합니다.",
        engine="llm",
        model=DEFAULT_LLM_MODEL,
    )
    validation = ValidationResult(
        True,
        generated.sql,
        (),
        ("voc_cases",),
    )

    result = write_audit_node(
        {
            "question": "최근 30일 VOC 유형별 처리 현황 알려줘.",
            "user_role": "sales_planning",
            "branch_id": 1,
            "db_path": str(tmp_path / "missing_audit_table.sqlite"),
            "audit_path": str(audit_path),
            "context": context,
            "generated": generated,
            "validation": validation,
            "rows": [{"case_type": "상품설명", "case_count": 3}],
            "execution_ms": 2.5,
        }
    )

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])

    assert result["database_audit_status"] == "failed"
    assert event["database_audit_status"] == "failed"
    assert "query_audit_log" in event["database_audit_error"]
    assert event["generated_sql"] == generated.sql
    assert event["llm_generated_sql"] == generated.sql
    assert event["policy_applied_sql"] == generated.sql
    assert event["validated_sql"] == generated.sql
    assert event["sql_policy_transformations"] == []


def test_unknown_role_is_normalized_and_branch_scope_is_applied(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    initialize_demo_database(db_path)

    def fake_generate_sql(*args, **kwargs):
        return GeneratedSQL(
            sql=(
                "SELECT b.branch_name, COUNT(a.account_id) AS new_account_count "
                "FROM accounts a JOIN branches b ON a.branch_id = b.branch_id "
                "GROUP BY b.branch_name LIMIT 10"
            ),
            reason="역할 정규화 검증용 SQL입니다.",
            engine="llm",
        )

    monkeypatch.setattr("im_one_agent.graph.generate_sql", fake_generate_sql)

    result = build_agent().invoke(
        {
            "question": "지난 3개월간 지점별 신규 계좌 수 추이는?",
            "user_role": "admin",
            "db_path": str(db_path),
            "audit_path": str(audit_path),
        }
    )

    assert result["user_role"] == "branch_manager"
    assert result["validation"].allowed
    assert result["llm_generated_sql"] == (
        "SELECT b.branch_name, COUNT(a.account_id) AS new_account_count "
        "FROM accounts a JOIN branches b ON a.branch_id = b.branch_id "
        "GROUP BY b.branch_name LIMIT 10"
    )
    assert result["policy_applied_sql"] == result["validation"].sql
    assert result["llm_generated_sql"] != result["validation"].sql
    assert result["sql_policy_transformations"] == ["branch_scope_filter_applied"]
    assert "WHERE a.branch_id = 1 GROUP BY" in result["validation"].sql
    assert "branch_manager 권한에 맞춰 branch_id 범위 조건을 적용했습니다." in result["generated"].assumptions

    connection = connect_database(db_path)
    try:
        audit_row = connection.execute(
            "SELECT user_role, generated_sql, llm_generated_sql, policy_applied_sql, validated_sql, sql_policy_transformations "
            "FROM query_audit_log"
        ).fetchone()
    finally:
        connection.close()

    assert audit_row["user_role"] == "branch_manager"
    assert audit_row["generated_sql"] == result["llm_generated_sql"]
    assert audit_row["llm_generated_sql"] == result["llm_generated_sql"]
    assert audit_row["policy_applied_sql"] == result["validation"].sql
    assert audit_row["validated_sql"] == result["validation"].sql
    assert json.loads(audit_row["sql_policy_transformations"]) == ["branch_scope_filter_applied"]


def test_branch_scope_is_normalized_to_known_demo_branch(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    initialize_demo_database(db_path)
    captured = {}

    def fake_generate_sql(*args, **kwargs):
        captured.update(kwargs)
        return GeneratedSQL(
            sql=(
                "SELECT b.branch_name, COUNT(a.account_id) AS new_account_count "
                "FROM accounts a JOIN branches b ON a.branch_id = b.branch_id "
                "WHERE a.branch_id = 1 GROUP BY b.branch_name LIMIT 10"
            ),
            reason="지점 범위 정규화 검증용 SQL입니다.",
            engine="llm",
        )

    monkeypatch.setattr("im_one_agent.graph.generate_sql", fake_generate_sql)

    result = build_agent().invoke(
        {
            "question": "지난 3개월간 지점별 신규 계좌 수 추이는?",
            "user_role": "branch_manager",
            "branch_id": 999,
            "db_path": str(db_path),
            "audit_path": str(audit_path),
        }
    )

    assert normalize_branch_id(999) == DEFAULT_BRANCH_ID
    assert result["branch_id"] == DEFAULT_BRANCH_ID
    assert captured["branch_id"] == DEFAULT_BRANCH_ID
    assert result["validation"].allowed


def test_compliance_role_blocks_disallowed_account_tables_in_full_graph(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    audit_path = tmp_path / "audit.jsonl"
    initialize_demo_database(db_path)

    def fake_generate_sql(*args, **kwargs):
        return GeneratedSQL(
            sql=(
                "SELECT b.branch_name, COUNT(a.account_id) AS new_account_count, "
                "MAX(bt.target_value) AS target_value "
                "FROM accounts a "
                "JOIN branches b ON a.branch_id = b.branch_id "
                "JOIN branch_targets bt ON bt.branch_id = b.branch_id "
                "GROUP BY b.branch_name LIMIT 10"
            ),
            reason="허용되지 않은 계좌/목표 테이블을 포함한 SQL입니다.",
            engine="llm",
            model=DEFAULT_LLM_MODEL,
        )

    monkeypatch.setattr("im_one_agent.graph.generate_sql", fake_generate_sql)

    result = build_agent().invoke(
        {
            "question": "신규 계좌 목표 대비 실적을 비교해줘.",
            "user_role": "compliance",
            "db_path": str(db_path),
            "audit_path": str(audit_path),
        }
    )

    assert result["user_role"] == "compliance"
    assert "accounts" not in result["context"].allowed_table_names
    assert "branch_targets" not in result["context"].allowed_table_names
    assert not result["validation"].allowed
    assert any("허용되지 않은 테이블 참조: accounts, branch_targets" in issue for issue in result["validation"].issues)
    assert result["rows"] == []

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["user_role"] == "compliance"
    assert event["validation_status"] == "blocked"
    assert event["execution_status"] == "blocked"
    assert event["referenced_tables"] == ["accounts", "branch_targets", "branches"]

    connection = connect_database(db_path)
    try:
        audit_row = connection.execute(
            "SELECT user_role, validation_status, execution_status, referenced_tables, blocked_reason "
            "FROM query_audit_log"
        ).fetchone()
    finally:
        connection.close()

    assert audit_row["user_role"] == "compliance"
    assert audit_row["validation_status"] == "blocked"
    assert audit_row["execution_status"] == "blocked"
    assert json.loads(audit_row["referenced_tables"]) == ["accounts", "branch_targets", "branches"]
    assert "허용되지 않은 테이블 참조: accounts, branch_targets" in audit_row["blocked_reason"]


def test_read_only_database_connection_blocks_writes(tmp_path) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)

    connection = connect_database(db_path, read_only=True)
    try:
        branch_count = connection.execute("SELECT COUNT(*) FROM branches").fetchone()[0]
        assert branch_count > 0
        try:
            connection.execute("CREATE TABLE should_fail (id INTEGER)")
        except Exception as exc:
            assert "readonly" in str(exc).lower() or "read-only" in str(exc).lower()
        else:
            raise AssertionError("read-only database connection allowed a write")
    finally:
        connection.close()


def test_preflight_read_only_mode_executes_query_and_skips_sqlite_audit(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)
    monkeypatch.setenv("IM_ONE_DB_READONLY", "1")

    checks = run_preflight(str(db_path), expect_read_only=True)
    by_name = {check.name: check for check in checks}

    connection = connect_database(db_path, read_only=False)
    try:
        audit_count = connection.execute("SELECT COUNT(*) FROM query_audit_log").fetchone()[0]
    finally:
        connection.close()

    assert by_name["read_only_mode"].passed
    assert by_name["read_only_mode"].required
    assert "agent query executed" in by_name["read_only_mode"].detail
    assert "sqlite audit skipped" in by_name["read_only_mode"].detail
    assert audit_count == 0


def test_query_audit_log_is_append_only(tmp_path) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)

    connection = connect_database(db_path)
    try:
        connection.execute(
            """
            INSERT INTO query_audit_log (
                created_at,
                user_id,
                auth_mode,
                user_role,
                original_question,
                question,
                selected_semantic_metrics,
                semantic_metrics,
                generated_sql,
                llm_generated_sql,
                policy_applied_sql,
                validated_sql,
                sql_policy_transformations,
                generation_engine,
                llm_model,
                prompt_version,
                validation_status,
                execution_status,
                row_count,
                blocked_reason
            )
            VALUES (
                '2026-07-09T00:00:00Z',
                'local-demo',
                'none',
                'branch_manager',
                '질문',
                '질문',
                'new_accounts',
                'new_accounts',
                'SELECT 1',
                'SELECT 1',
                'SELECT 1',
                'SELECT 1',
                '[]',
                'llm',
                'gpt-5.6-luna',
                'im-one-nl2sql-v1',
                'passed',
                'executed',
                1,
                NULL
            )
            """
        )
        connection.commit()

        for sql in (
            "UPDATE query_audit_log SET row_count = 2 WHERE audit_id = 1",
            "DELETE FROM query_audit_log WHERE audit_id = 1",
        ):
            try:
                connection.execute(sql)
            except Exception as exc:
                assert "append-only" in str(exc)
            else:
                raise AssertionError(f"query_audit_log allowed mutation: {sql}")
    finally:
        connection.close()


def test_audit_append_only_preflight_inspects_required_trigger_semantics(tmp_path) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)

    checks = run_preflight(str(db_path))
    by_name = {check.name: check for check in checks}

    assert by_name["audit_append_only"].passed
    assert by_name["audit_append_only"].required
    assert "query_audit_log_no_update" in by_name["audit_append_only"].detail
    assert "query_audit_log_no_delete" in by_name["audit_append_only"].detail
    assert by_name["trace_audit_policy"].passed
    assert by_name["trace_audit_policy"].required
    assert "trace_nodes=7" in by_name["trace_audit_policy"].detail

    connection = connect_database(":memory:")
    try:
        connection.executescript(
            """
            CREATE TABLE query_audit_log (audit_id INTEGER PRIMARY KEY, row_count INTEGER);
            CREATE TRIGGER query_audit_log_no_update
            BEFORE UPDATE ON query_audit_log
            BEGIN
                SELECT 1;
            END;
            """
        )

        errors = audit_append_only_errors(connection)
    finally:
        connection.close()

    assert "missing audit trigger: query_audit_log_no_delete" in errors
    assert any("append-only abort semantics" in error for error in errors)


def test_trace_audit_policy_preflight_verifies_required_contract(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    check = check_trace_audit_policy(str(db_path))

    assert check.passed
    assert check.required
    assert "trace_nodes=7" in check.detail
    assert "audit_fields=22" in check.detail
    assert "database_audit=recorded" in check.detail


def test_trace_audit_policy_preflight_fails_when_trace_contract_changes(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)
    monkeypatch.setattr(
        preflight_module,
        "REQUIRED_TRACE_NODES",
        preflight_module.REQUIRED_TRACE_NODES + ("Missing Node",),
    )

    check = preflight_module.check_trace_audit_policy(str(db_path))

    assert not check.passed
    assert check.required
    assert "trace nodes mismatch" in check.detail


def test_schema_readiness_requires_audit_observability_columns() -> None:
    connection = connect_database(":memory:")
    try:
        connection.executescript(
            """
            CREATE TABLE branches (branch_id INTEGER, branch_name TEXT, region TEXT, branch_type TEXT, opened_date TEXT, active_flag INTEGER);
            CREATE TABLE accounts (account_id INTEGER, branch_id INTEGER, opened_at TEXT, channel TEXT, customer_segment TEXT, age_band TEXT, risk_profile_band TEXT, is_first_account INTEGER);
            CREATE TABLE product_sales (sale_id INTEGER, branch_id INTEGER, customer_segment TEXT, product_type TEXT, risk_grade INTEGER, amount INTEGER, sold_at TEXT, channel TEXT, suitability_checked INTEGER, cooling_off_eligible INTEGER);
            CREATE TABLE voc_cases (case_id INTEGER, branch_id INTEGER, case_type TEXT, status TEXT, received_at TEXT, resolved_at TEXT, severity TEXT, product_type TEXT, sla_due_at TEXT);
            CREATE TABLE investment_reviews (review_id INTEGER, branch_id INTEGER, review_type TEXT, status TEXT, created_at TEXT, due_at TEXT, product_type TEXT, risk_grade INTEGER);
            CREATE TABLE branch_targets (target_id INTEGER, branch_id INTEGER, target_month TEXT, metric_name TEXT, target_value INTEGER);
            CREATE TABLE query_audit_log (
                audit_id INTEGER,
                created_at TEXT,
                user_role TEXT,
                question TEXT,
                semantic_metrics TEXT,
                generated_sql TEXT,
                validation_status TEXT,
                execution_status TEXT,
                row_count INTEGER,
                blocked_reason TEXT
            );
            """
        )

        assert not database_has_required_schema(connection)
    finally:
        connection.close()


def test_synthetic_data_policy_preflight_blocks_sensitive_demo_data(tmp_path) -> None:
    clean_db_path = tmp_path / "clean.sqlite"
    initialize_demo_database(clean_db_path)

    clean_check = check_synthetic_data_policy(str(clean_db_path))

    assert clean_check.passed
    assert clean_check.required
    connection = connect_database(clean_db_path)
    try:
        branch_names = [
            row["branch_name"]
            for row in connection.execute("SELECT branch_name FROM branches ORDER BY branch_id").fetchall()
        ]
    finally:
        connection.close()
    assert branch_names
    assert all("합성" in branch_name for branch_name in branch_names)

    realistic_branch_db_path = tmp_path / "realistic_branch.sqlite"
    initialize_demo_database(realistic_branch_db_path)
    connection = connect_database(realistic_branch_db_path)
    try:
        connection.execute("UPDATE branches SET branch_name = '서울중앙WM센터' WHERE branch_id = 1")
        connection.commit()
    finally:
        connection.close()

    realistic_branch_check = check_synthetic_data_policy(str(realistic_branch_db_path))

    assert not realistic_branch_check.passed
    assert "branches.branch_name: missing explicit synthetic marker for branch_id=1" in realistic_branch_check.detail

    bad_db_path = tmp_path / "bad.sqlite"
    connection = connect_database(bad_db_path)
    try:
        connection.execute(
            "CREATE TABLE accounts (account_id INTEGER, customer_name TEXT, phone_number TEXT, phone_no TEXT, employee_no TEXT, rrn_hash TEXT, email_address TEXT)"
        )
        connection.execute(
            "INSERT INTO accounts VALUES (1, '홍길동', '010-1234-5678', NULL, NULL, NULL, NULL)"
        )
        connection.commit()
    finally:
        connection.close()

    bad_check = check_synthetic_data_policy(str(bad_db_path))

    assert not bad_check.passed
    assert bad_check.required
    assert "accounts.customer_name: forbidden column" in bad_check.detail
    assert "accounts.phone_number: forbidden column" in bad_check.detail
    assert "accounts.phone_no: forbidden column pattern: phone" in bad_check.detail
    assert "accounts.employee_no: forbidden column pattern: employee_identifier" in bad_check.detail
    assert "accounts.rrn_hash: forbidden column pattern: resident_registration_number" in bad_check.detail
    assert "accounts.email_address: forbidden column pattern: email" in bad_check.detail


def test_synthetic_dataset_metadata_preflight_requires_poc_disclosure(tmp_path) -> None:
    clean_db_path = tmp_path / "clean-metadata.sqlite"
    initialize_demo_database(clean_db_path)

    clean_check = check_synthetic_dataset_metadata(str(clean_db_path))

    assert clean_check.passed
    assert clean_check.required
    assert "classification=synthetic_poc" in clean_check.detail

    bad_db_path = tmp_path / "bad-metadata.sqlite"
    initialize_demo_database(bad_db_path)
    connection = connect_database(bad_db_path)
    try:
        connection.execute(
            "UPDATE demo_dataset_metadata SET metadata_value = 'true' WHERE metadata_key = 'contains_real_customer_data'"
        )
        connection.execute("DELETE FROM demo_dataset_metadata WHERE metadata_key = 'notice_ko'")
        connection.commit()
    finally:
        connection.close()

    bad_check = check_synthetic_dataset_metadata(str(bad_db_path))

    assert not bad_check.passed
    assert bad_check.required
    assert "contains_real_customer_data" in bad_check.detail
    assert "notice_ko" in bad_check.detail


def test_preflight_reports_required_llm_missing(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    checks = run_preflight(str(db_path), require_llm=True)
    by_name = {check.name: check for check in checks}

    assert by_name["database_access"].passed
    assert by_name["demo_query_latency"].passed
    assert by_name["demo_query_latency"].required
    assert by_name["static_ui_assets"].passed
    assert by_name["static_ui_assets"].required
    assert by_name["web_api_auth_policy"].passed
    assert by_name["web_api_auth_policy"].required
    assert by_name["health_disclosure_policy"].passed
    assert by_name["health_disclosure_policy"].required
    assert by_name["sql_validation_policy"].passed
    assert by_name["sql_validation_policy"].required
    assert by_name["schema_retrieval_policy"].passed
    assert by_name["schema_retrieval_policy"].required
    assert by_name["result_explanation_policy"].passed
    assert by_name["result_explanation_policy"].required
    assert by_name["role_policy"].passed
    assert by_name["role_policy"].required
    assert by_name["synthetic_data_policy"].passed
    assert by_name["synthetic_data_policy"].required
    assert by_name["synthetic_dataset_metadata"].passed
    assert by_name["synthetic_dataset_metadata"].required
    assert by_name["evaluation_readiness"].passed
    assert by_name["evaluation_readiness"].required
    assert not by_name["llm_configuration"].passed
    assert by_name["llm_configuration"].required
    assert by_name["gold_coverage"].passed


def test_preflight_checks_static_ui_assets() -> None:
    check = check_static_ui_assets()

    assert check.passed
    assert check.required
    assert "icons=" in check.detail
    assert "csp=same-origin" in check.detail
    assert "status=llm-validation-trace" in check.detail
    assert "responsive_layout=checked" in check.detail


def test_preflight_blocks_external_or_incomplete_static_assets(tmp_path) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(
        """
        <html>
          <head><link rel="stylesheet" href="https://cdn.example.com/styles.css"></head>
          <body>
            <script type="module" src="https://cdn.example.com/main.js"></script>
          </body>
        </html>
        """,
        encoding="utf-8",
    )

    check = check_static_ui_assets(static_dir)

    assert not check.passed
    assert check.required
    assert "missing static file: package.json" in check.detail
    assert "missing static file: src/App.tsx" in check.detail


def test_preflight_checks_web_api_auth_policy() -> None:
    check = check_web_api_auth_policy()

    assert check.passed
    assert check.required
    assert "protected_get=" in check.detail
    assert "protected_post=" in check.detail


def test_preflight_blocks_public_and_protected_api_policy_overlap() -> None:
    check = check_web_api_auth_policy(
        public_get_paths={"/api/health", "/api/metrics"},
        protected_get_paths={"/api/metrics", "/api/catalog"},
        protected_post_paths={"/api/query"},
    )

    assert not check.passed
    assert check.required
    assert "missing public GET paths: /api/demo-questions" in check.detail
    assert "missing protected GET paths: /api/audit-summary" in check.detail
    assert "missing protected POST paths: /api/export" in check.detail
    assert "GET paths cannot be both public and protected: /api/metrics" in check.detail


def test_preflight_checks_health_disclosure_policy(tmp_path) -> None:
    check = check_health_disclosure_policy(str(tmp_path / "health.sqlite"))

    assert check.passed
    assert check.required
    assert check.detail == "public=minimal, authorized=detailed"


def test_preflight_blocks_health_payload_internal_detail_exposure(monkeypatch) -> None:
    def unsafe_health_payload(db_path: str, include_sensitive: bool = False) -> dict[str, object]:
        if include_sensitive:
            return {
                "database": {"ok": True},
                "llm": {"configured": True, "model": "gpt-approved"},
                "embedding": {"configured": True, "base_url": "https://embedding.internal/v1"},
            }
        return {
            "database": {"ok": True, "path": db_path},
            "llm": {"configured": True, "model": "gpt-approved", "base_url": "https://llm.internal/v1"},
            "embedding": {"configured": True, "base_url": "https://embedding.internal/v1"},
        }

    monkeypatch.setattr("im_one_agent.web.build_health_payload", unsafe_health_payload)

    check = check_health_disclosure_policy("data/im_one_demo.sqlite")

    assert not check.passed
    assert check.required
    assert "public health exposes database path" in check.detail
    assert "public health exposes llm.model" in check.detail
    assert "public health exposes embedding.base_url" in check.detail
    assert "authorized health omits database path" in check.detail
    assert "authorized health omits llm.base_url" in check.detail
    assert "authorized health omits embedding.model" in check.detail


def test_preflight_checks_core_demo_query_latency(tmp_path) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"

    check = check_demo_query_latency(str(db_path))

    assert check.passed
    assert check.required
    assert "queries=5" in check.detail
    assert "max_ms=" in check.detail


def test_database_backend_policy_allows_default_sqlite_and_rejects_unknown(monkeypatch) -> None:
    monkeypatch.delenv("IM_ONE_DB_BACKEND", raising=False)
    check = check_database_backend_policy()

    assert check.passed
    assert check.required
    assert "backend=sqlite" in check.detail

    monkeypatch.setenv("IM_ONE_DB_BACKEND", "snowflake")
    failed = check_database_backend_policy()

    assert not failed.passed
    assert failed.required
    assert "지원하지 않는 DB backend" in failed.detail


def test_execute_sql_node_reports_unsupported_database_backend(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IM_ONE_DB_BACKEND", "postgres")

    result = execute_sql_node(
        {
            "db_path": str(tmp_path / "backend.sqlite"),
            "validation": ValidationResult(True, "SELECT 1 AS value LIMIT 1", (), ()),
        }
    )

    assert not result["validation"].allowed
    assert result["rows"] == []
    assert "DB 실행 backend 오류" in result["validation"].issues[0]
    assert execution_status_for(result) == "failed"


def test_preflight_checks_query_plan_policy(tmp_path) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"

    check = check_query_plan_policy(str(db_path))

    assert check.passed
    assert check.required
    assert "plan_steps=" in check.detail
    assert "pre_execution_rows=" in check.detail
    assert "execution_ms=" in check.detail


def test_preflight_checks_sql_validation_policy(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    check = check_sql_validation_policy(str(db_path))

    assert check.passed
    assert check.required
    assert "probes=" in check.detail
    assert "probes=19" in check.detail

    def unsafe_validate_sql(*args, **kwargs):
        return ValidationResult(True, str(args[0]), (), ())

    monkeypatch.setattr(preflight_module, "validate_sql", unsafe_validate_sql)

    bad_check = preflight_module.check_sql_validation_policy(str(db_path))

    assert not bad_check.passed
    assert bad_check.required
    assert "dml_block" in bad_check.detail
    assert "multi_statement_block" in bad_check.detail
    assert "unknown_column_block" in bad_check.detail
    assert "syntax_error_block" in bad_check.detail
    assert "select_star_block" in bad_check.detail
    assert "large_limit_block" in bad_check.detail


def test_preflight_checks_schema_retrieval_policy(monkeypatch) -> None:
    check = check_schema_retrieval_policy()

    assert check.passed
    assert check.required
    assert "probes=6" in check.detail

    original_retrieve_schema = preflight_module.retrieve_schema

    def broken_retrieve_schema(question, *args, **kwargs):
        context = original_retrieve_schema(question, *args, **kwargs)
        if "VOC 유형별" in question:
            return context.__class__(
                tables=context.tables,
                matched_metrics=context.matched_metrics,
                business_rules=context.business_rules,
                example_queries=context.example_queries,
                retrieval_scores=context.retrieval_scores,
                retrieval_confidence="low",
                clarification_options=context.clarification_options,
            )
        return context

    monkeypatch.setattr(preflight_module, "retrieve_schema", broken_retrieve_schema)

    bad_check = preflight_module.check_schema_retrieval_policy()

    assert not bad_check.passed
    assert bad_check.required
    assert "voc_status: expected high confidence" in bad_check.detail


def test_preflight_checks_result_explanation_policy(monkeypatch) -> None:
    check = check_result_explanation_policy()

    assert check.passed
    assert check.required
    assert "allowed_and_blocked_explanations=covered" in check.detail

    monkeypatch.setattr(preflight_module, "build_explanation", lambda *args, **kwargs: "질문만 표시")

    bad_check = preflight_module.check_result_explanation_policy()

    assert not bad_check.passed
    assert bad_check.required
    assert "missing snippets" in bad_check.detail
    assert "지표 정의" in bad_check.detail


def test_preflight_role_policy_blocks_operational_table_exposure(monkeypatch) -> None:
    monkeypatch.setitem(
        preflight_module.ROLE_TABLE_POLICY,
        "branch_manager",
        {"branches", "accounts", "query_audit_log"},
    )

    check = preflight_module.check_role_policy()

    assert not check.passed
    assert check.required
    assert "branch_manager: operational tables exposed: query_audit_log" in check.detail


def test_preflight_reports_query_timeout_configuration(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"

    checks = run_preflight(str(db_path))
    by_name = {check.name: check for check in checks}
    assert by_name["query_timeout"].passed
    assert by_name["query_timeout"].required
    assert by_name["llm_timeout"].passed
    assert by_name["llm_timeout"].required

    monkeypatch.setenv("IM_ONE_QUERY_TIMEOUT_MS", "0")
    disabled_checks = run_preflight(str(db_path))
    disabled_by_name = {check.name: check for check in disabled_checks}
    assert not disabled_by_name["query_timeout"].passed
    assert disabled_by_name["query_timeout"].required


def test_preflight_reports_llm_timeout_configuration(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"

    monkeypatch.setenv("IM_ONE_LLM_TIMEOUT", "11")
    slow_checks = run_preflight(str(db_path))
    slow_by_name = {check.name: check for check in slow_checks}
    assert not slow_by_name["llm_timeout"].passed
    assert slow_by_name["llm_timeout"].required

    monkeypatch.setenv("IM_ONE_LLM_TIMEOUT", "invalid")
    invalid_checks = run_preflight(str(db_path))
    invalid_by_name = {check.name: check for check in invalid_checks}
    assert not invalid_by_name["llm_timeout"].passed
    assert invalid_by_name["llm_timeout"].required


def test_llm_prompt_policy_preflight_validates_payload_contract(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    check = check_llm_prompt_policy()

    assert check.passed
    assert check.required
    assert "model=preflight-llm" in check.detail
    assert "rules=" in check.detail


def test_llm_prompt_policy_preflight_fails_when_selected_schema_is_missing(monkeypatch) -> None:
    original_builder = preflight_module.build_llm_payload

    def broken_payload(*args, **kwargs):
        payload = original_builder(*args, **kwargs)
        user_context = json.loads(payload["messages"][1]["content"])
        user_context.pop("selected_schema")
        payload["messages"][1]["content"] = json.dumps(user_context)
        return payload

    monkeypatch.setattr(preflight_module, "build_llm_payload", broken_payload)

    check = preflight_module.check_llm_prompt_policy()

    assert not check.passed
    assert check.required
    assert "selected_schema.allowed_tables missing or inconsistent" in check.detail


def test_preflight_profiles_apply_grouped_readiness_requirements() -> None:
    poc_requirements = preflight_requirements_for_profile("poc")
    pilot_requirements = preflight_requirements_for_profile("pilot")

    assert poc_requirements["require_llm"]
    assert poc_requirements["check_llm"]
    assert pilot_requirements["require_llm"]
    assert pilot_requirements["check_llm"]
    assert pilot_requirements["require_api_token"]
    assert pilot_requirements["expect_read_only"]
    assert pilot_requirements["require_sql_parser"]
    assert pilot_requirements["require_embedding"]
    assert pilot_requirements["check_embedding"]
    assert pilot_requirements["require_trusted_auth"]
    assert pilot_requirements["require_trusted_proxy_token"]
    assert pilot_requirements["require_feedback_store"]
    assert preflight_requirements_for_profile(None) == {}


def test_prd_traceability_preflight_covers_all_functional_requirements() -> None:
    check = check_prd_traceability(set(preflight_module.PREFLIGHT_NEXT_ACTIONS))

    assert check.passed
    assert check.required
    assert "functional=12" in check.detail
    assert "nonfunctional=4" in check.detail


def test_prd_traceability_preflight_fails_when_functional_requirement_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        preflight_module,
        "PRD_TRACEABILITY_MATRIX",
        tuple(
            item
            for item in preflight_module.PRD_TRACEABILITY_MATRIX
            if item.requirement_id != "FR-012"
        ),
    )

    check = preflight_module.check_prd_traceability(set(preflight_module.PREFLIGHT_NEXT_ACTIONS))

    assert not check.passed
    assert check.required
    assert "missing functional requirements: FR-012" in check.detail


def test_prd_traceability_preflight_fails_for_unknown_readiness_gate(monkeypatch) -> None:
    first_item = preflight_module.PRD_TRACEABILITY_MATRIX[0]
    replacement = preflight_module.PrdTraceabilityItem(
        first_item.requirement_id,
        first_item.title,
        first_item.implementation_artifacts,
        first_item.verification_artifacts,
        ("missing_gate",),
    )
    monkeypatch.setattr(
        preflight_module,
        "PRD_TRACEABILITY_MATRIX",
        (replacement,) + preflight_module.PRD_TRACEABILITY_MATRIX[1:],
    )

    check = preflight_module.check_prd_traceability(set(preflight_module.PREFLIGHT_NEXT_ACTIONS))

    assert not check.passed
    assert check.required
    assert "FR-001: unknown preflight checks: missing_gate" in check.detail


def test_prd_traceability_preflight_fails_for_missing_artifact(monkeypatch) -> None:
    first_item = preflight_module.PRD_TRACEABILITY_MATRIX[0]
    replacement = preflight_module.PrdTraceabilityItem(
        first_item.requirement_id,
        first_item.title,
        ("src/im_one_agent/not_a_real_module.py",),
        first_item.verification_artifacts,
        first_item.preflight_checks,
    )
    monkeypatch.setattr(
        preflight_module,
        "PRD_TRACEABILITY_MATRIX",
        (replacement,) + preflight_module.PRD_TRACEABILITY_MATRIX[1:],
    )

    check = preflight_module.check_prd_traceability(set(preflight_module.PREFLIGHT_NEXT_ACTIONS))

    assert not check.passed
    assert check.required
    assert "FR-001: implementation artifact missing: src/im_one_agent/not_a_real_module.py" in check.detail


def test_prd_traceability_preflight_fails_for_missing_prd_heading(tmp_path, monkeypatch) -> None:
    prd_path = tmp_path / "prd.md"
    prd_path.write_text(
        "\n".join(f"### FR-{index:03d} placeholder" for index in range(1, 12)),
        encoding="utf-8",
    )
    monkeypatch.setattr(preflight_module, "PRD_DOC_PATH", prd_path)

    check = preflight_module.check_prd_traceability(set(preflight_module.PREFLIGHT_NEXT_ACTIONS))

    assert not check.passed
    assert check.required
    assert "PRD document missing headings: FR-012" in check.detail


def test_preflight_report_summarizes_required_failures() -> None:
    checks = [
        preflight_module.PreflightCheck("database_access", True, True, "branches=10"),
        preflight_module.PreflightCheck("llm_configuration", False, True, "OPENAI_API_KEY is not configured."),
        preflight_module.PreflightCheck("sql_parser", False, False, "sqlglot is not installed."),
    ]

    report = build_preflight_report(checks, profile="pilot", db_path="data/im_one_demo.sqlite")

    assert report["profile"] == "pilot"
    assert report["db_path"] == "data/im_one_demo.sqlite"
    assert report["passed"] is False
    assert report["summary"]["required_failed"] == 1
    assert report["summary"]["optional_failed"] == 1
    assert report["summary"]["required_failed_names"] == ["llm_configuration"]
    assert report["checks"][1]["detail"] == "OPENAI_API_KEY is not configured."
    assert report["next_actions"] == [
        {
            "name": "llm_configuration",
            "required": True,
            "action": "Set an approved OPENAI_API_KEY and IM_ONE_LLM_MODEL, or configure a localhost LLM runtime with IM_ONE_LLM_ALLOW_LOCAL_NO_AUTH=1.",
            "detail": "OPENAI_API_KEY is not configured.",
        },
        {
            "name": "sql_parser",
            "required": False,
            "action": "Install sqlglot in the deployment environment before requiring strict parser readiness.",
            "detail": "sqlglot is not installed.",
        },
    ]


def test_preflight_llm_check_validates_generated_sql(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"

    def fake_generate_sql_with_llm(question, *args, **kwargs):
        if "신규 계좌" in question:
            sql = """
SELECT
    b.branch_name,
    STRFTIME('%Y-%m', a.opened_at) AS opened_month,
    COUNT(*) AS new_account_count
FROM accounts a
JOIN branches b ON a.branch_id = b.branch_id
WHERE a.opened_at >= DATE('2026-06-24', '-3 months')
GROUP BY b.branch_name, opened_month
ORDER BY opened_month, b.branch_name
LIMIT 50
""".strip()
        elif "고위험" in question:
            sql = """
SELECT
    b.branch_name,
    COUNT(*) AS high_risk_sale_count
FROM product_sales ps
JOIN branches b ON ps.branch_id = b.branch_id
WHERE ps.risk_grade >= 4
  AND ps.sold_at >= DATE('2026-06-24', 'start of month')
GROUP BY b.branch_name
ORDER BY high_risk_sale_count DESC, b.branch_name
LIMIT 20
""".strip()
        elif "VOC 유형별" in question:
            sql = """
SELECT
    v.case_type,
    v.status,
    COUNT(*) AS case_count
FROM voc_cases v
WHERE v.received_at >= DATE('2026-06-24', '-30 days')
GROUP BY v.case_type, v.status
ORDER BY case_count DESC, v.case_type
LIMIT 30
""".strip()
        elif "ELS 가입 금액" in question:
            sql = """
WITH els_sales AS (
    SELECT branch_id, SUM(amount) AS els_amount
    FROM product_sales
    WHERE product_type = 'ELS'
      AND sold_at >= DATE('2026-06-24', '-3 months')
    GROUP BY branch_id
),
voc_summary AS (
    SELECT branch_id, COUNT(*) AS voc_count
    FROM voc_cases
    WHERE received_at >= DATE('2026-06-24', '-3 months')
    GROUP BY branch_id
)
SELECT
    b.branch_name,
    COALESCE(e.els_amount, 0) AS els_amount,
    COALESCE(v.voc_count, 0) AS voc_count
FROM branches b
LEFT JOIN els_sales e ON b.branch_id = e.branch_id
LEFT JOIN voc_summary v ON b.branch_id = v.branch_id
ORDER BY els_amount DESC, voc_count DESC
LIMIT 20
""".strip()
        else:
            sql = """
SELECT
    b.branch_name,
    ir.status,
    COUNT(*) AS review_count
FROM investment_reviews ir
JOIN branches b ON ir.branch_id = b.branch_id
WHERE ir.created_at >= DATE('2026-06-24', '-60 days')
  AND ir.status != 'completed'
GROUP BY b.branch_name, ir.status
ORDER BY review_count DESC, b.branch_name
LIMIT 30
""".strip()
        return GeneratedSQL(
            sql=sql,
            reason="core demo 검증용 SQL입니다.",
            engine="llm",
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(preflight_module, "generate_sql_with_llm", fake_generate_sql_with_llm)

    checks = run_preflight(str(db_path), check_llm=True)
    by_name = {check.name: check for check in checks}

    assert by_name["llm_generation"].passed
    assert "core_demo_cases=5" in by_name["llm_generation"].detail
    assert "executed=5" in by_name["llm_generation"].detail


def test_preflight_llm_check_fails_unsafe_generated_sql(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"

    def fake_generate_sql_with_llm(*args, **kwargs):
        return GeneratedSQL(
            sql="SELECT * FROM voc_cases LIMIT 10",
            reason="원천 VOC 데이터를 조회합니다.",
            engine="llm",
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(preflight_module, "generate_sql_with_llm", fake_generate_sql_with_llm)

    checks = run_preflight(str(db_path), check_llm=True)
    by_name = {check.name: check for check in checks}

    assert not by_name["llm_generation"].passed
    assert "failed validation" in by_name["llm_generation"].detail


def test_preflight_llm_check_requires_llm_generation_metadata(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"

    def fake_generate_sql_with_llm(*args, **kwargs):
        return GeneratedSQL(
            sql="""
SELECT
    v.case_type,
    v.status,
    COUNT(*) AS case_count
FROM voc_cases v
GROUP BY v.case_type, v.status
ORDER BY case_count DESC
LIMIT 30
""".strip(),
            reason="검증용 SQL입니다.",
            engine="verified_fixture",
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(preflight_module, "generate_sql_with_llm", fake_generate_sql_with_llm)

    check = preflight_module.check_llm_generation(str(db_path))

    assert not check.passed
    assert "core-001: generation engine is not llm: verified_fixture" in check.detail


def test_preflight_llm_check_requires_current_prompt_version(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"

    def fake_generate_sql_with_llm(*args, **kwargs):
        return GeneratedSQL(
            sql="""
SELECT
    v.case_type,
    v.status,
    COUNT(*) AS case_count
FROM voc_cases v
GROUP BY v.case_type, v.status
ORDER BY case_count DESC
LIMIT 30
""".strip(),
            reason="검증용 SQL입니다.",
            engine="llm",
            prompt_version="old-prompt",
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(preflight_module, "generate_sql_with_llm", fake_generate_sql_with_llm)

    check = preflight_module.check_llm_generation(str(db_path))

    assert not check.passed
    assert "core-001: prompt_version mismatch: old-prompt" in check.detail


def test_live_llm_generation_samples_record_core_case_evidence(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)
    core_cases = {
        case.question: case
        for case in EVALUATION_CASES
        if case.case_id.startswith("core-")
    }

    def fake_generate_sql(question, context, **kwargs):
        case = core_cases[question]
        return GeneratedSQL(
            sql=gold_sql_for_case(case, role="sales_planning", branch_id=1),
            reason=f"{case.intent} gold SQL for live evidence test.",
            engine="llm",
            model=DEFAULT_LLM_MODEL,
            prompt_version=PROMPT_VERSION,
        )

    monkeypatch.setattr(evidence_module, "llm_endpoint_configured", lambda: True)
    monkeypatch.setattr(evidence_module, "generate_sql_with_llm", fake_generate_sql)

    artifact = evidence_module.build_live_llm_generation_samples_artifact(
        db_path=str(db_path),
        role="sales_planning",
        branch_id=1,
        live_checks=True,
    )

    assert artifact["status"] == "passed"
    assert artifact["live_checks_enabled"] is True
    assert artifact["case_total"] == 5
    assert artifact["passed_cases"] == 5
    assert artifact["failed_cases"] == 0
    assert {case["case_id"] for case in artifact["cases"]} == {
        "core-001",
        "core-002",
        "core-003",
        "core-004",
        "core-005",
    }
    assert all(case["generation_engine"] == "llm" for case in artifact["cases"])
    assert all(case["validation_allowed"] for case in artifact["cases"])
    assert all(case["row_count"] > 0 for case in artifact["cases"])
    assert all(case["execution"]["row_count"] == case["row_count"] for case in artifact["cases"])
    assert all(case["execution"]["column_metadata"] for case in artifact["cases"])
    assert all(case["execution"]["query_plan_summary"] for case in artifact["cases"])
    assert all(case["execution"]["pre_execution_row_count_status"] == "checked" for case in artifact["cases"])
    assert all(case["execution"]["error_issue"] is None for case in artifact["cases"])
    assert all(not case["missing_tables"] for case in artifact["cases"])
    assert all(not case["missing_columns"] for case in artifact["cases"])
    assert all(len(case["prompt_payload_sha256"]) == 64 for case in artifact["cases"])
    assert all(
        case["prompt_contract"]["response_contract_required"] == ["sql", "reason", "assumptions"]
        for case in artifact["cases"]
    )
    assert all(
        all(case["prompt_contract"]["response_contract_checks"].values())
        for case in artifact["cases"]
    )
    assert all(case["prompt_contract"]["selected_table_names"] for case in artifact["cases"])
    assert all(case["prompt_contract"]["matched_metric_names"] for case in artifact["cases"])


def test_live_llm_generation_samples_record_prompt_contract_when_endpoint_missing(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    initialize_demo_database(db_path)
    monkeypatch.setattr(evidence_module, "llm_endpoint_configured", lambda: False)

    artifact = evidence_module.build_live_llm_generation_samples_artifact(
        db_path=str(db_path),
        role="sales_planning",
        branch_id=1,
        live_checks=True,
    )

    assert artifact["status"] == "failed"
    assert artifact["failed_cases"] == 5
    assert all(case["error"] == "LLM endpoint is not configured." for case in artifact["cases"])
    assert all(len(case["prompt_payload_sha256"]) == 64 for case in artifact["cases"])
    assert all(
        case["prompt_contract"]["response_contract_required"] == ["sql", "reason", "assumptions"]
        for case in artifact["cases"]
    )
    assert all(
        all(case["prompt_contract"]["response_contract_checks"].values())
        for case in artifact["cases"]
    )


def test_preflight_llm_check_fails_when_required_core_table_is_missing(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    case_by_question = {case.question: case for case in EVALUATION_CASES}

    def fake_generate_sql_with_llm(question, *args, **kwargs):
        if "ELS 가입 금액" in question:
            sql = """
SELECT
    b.branch_name,
    SUM(ps.amount) AS els_amount,
    0 AS voc_count
FROM product_sales ps
JOIN branches b ON ps.branch_id = b.branch_id
WHERE ps.product_type = 'ELS'
GROUP BY b.branch_name
ORDER BY els_amount DESC
LIMIT 20
""".strip()
        else:
            sql = gold_sql_for_case(case_by_question[question], role="sales_planning", branch_id=1)
        return GeneratedSQL(sql=sql, reason="core demo 검증용 SQL입니다.", engine="llm")

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(preflight_module, "generate_sql_with_llm", fake_generate_sql_with_llm)

    checks = run_preflight(str(db_path), check_llm=True)
    by_name = {check.name: check for check in checks}

    assert not by_name["llm_generation"].passed
    assert "core-004: generated SQL missing required table(s): voc_cases" in by_name["llm_generation"].detail


def test_preflight_llm_check_fails_when_core_result_shape_is_missing(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    case_by_question = {case.question: case for case in EVALUATION_CASES}

    def fake_generate_sql_with_llm(question, *args, **kwargs):
        if "ELS 가입 금액" in question:
            sql = """
WITH els_sales AS (
    SELECT branch_id, SUM(amount) AS els_amount
    FROM product_sales
    WHERE product_type = 'ELS'
    GROUP BY branch_id
),
voc_summary AS (
    SELECT branch_id, COUNT(*) AS voc_count
    FROM voc_cases
    GROUP BY branch_id
)
SELECT
    b.branch_name,
    COALESCE(e.els_amount, 0) AS els_amount
FROM branches b
LEFT JOIN els_sales e ON b.branch_id = e.branch_id
LEFT JOIN voc_summary v ON b.branch_id = v.branch_id
ORDER BY els_amount DESC
LIMIT 20
""".strip()
        else:
            sql = gold_sql_for_case(case_by_question[question], role="sales_planning", branch_id=1)
        return GeneratedSQL(sql=sql, reason="core demo 검증용 SQL입니다.", engine="llm")

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(preflight_module, "generate_sql_with_llm", fake_generate_sql_with_llm)

    checks = run_preflight(str(db_path), check_llm=True)
    by_name = {check.name: check for check in checks}

    assert not by_name["llm_generation"].passed
    assert "core-004: SQL result missing expected column(s): voc_count" in by_name["llm_generation"].detail


def test_preflight_llm_check_fails_when_live_generation_exceeds_latency_target(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    case_by_question = {case.question: case for case in EVALUATION_CASES}
    timer_values = iter([0.0, 10.25])

    def fake_generate_sql_with_llm(question, *args, **kwargs):
        return GeneratedSQL(
            sql=gold_sql_for_case(case_by_question[question], role="sales_planning", branch_id=1),
            reason="core demo 검증용 SQL입니다.",
            engine="llm",
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(preflight_module, "generate_sql_with_llm", fake_generate_sql_with_llm)
    monkeypatch.setattr(preflight_module, "perf_counter", lambda: next(timer_values))

    check = preflight_module.check_llm_generation(str(db_path))

    assert not check.passed
    assert "core-001: live LLM workflow latency 10250.00ms exceeds 10000ms target." in check.detail


def test_preflight_accepts_explicit_local_llm_runtime_without_api_key(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("IM_ONE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("IM_ONE_LLM_MODEL", "local-nl2sql")
    monkeypatch.setenv("IM_ONE_LLM_ALLOW_LOCAL_NO_AUTH", "1")

    checks = run_preflight(str(db_path), require_llm=True)
    by_name = {check.name: check for check in checks}

    assert by_name["llm_configuration"].passed
    assert by_name["llm_configuration"].required
    assert "model=local-nl2sql" in by_name["llm_configuration"].detail
    assert "auth=local_no_auth" in by_name["llm_configuration"].detail


def test_preflight_rejects_no_auth_remote_llm_endpoint(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("IM_ONE_LLM_BASE_URL", "https://llm.internal.example/v1")
    monkeypatch.setenv("IM_ONE_LLM_ALLOW_LOCAL_NO_AUTH", "1")

    checks = run_preflight(str(db_path), require_llm=True)
    by_name = {check.name: check for check in checks}

    assert not by_name["llm_configuration"].passed
    assert by_name["llm_configuration"].required
    assert "local no-auth mode only applies to localhost" in by_name["llm_configuration"].detail


def test_preflight_reports_langgraph_runtime_and_sql_parser(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    monkeypatch.setattr(preflight_module, "sqlglot", object())

    checks = run_preflight(str(db_path))
    by_name = {check.name: check for check in checks}

    assert by_name["langgraph_runtime"].passed
    assert by_name["langgraph_runtime"].required
    assert "nodes=" in by_name["langgraph_runtime"].detail
    assert by_name["sql_parser"].passed
    assert by_name["sql_parser"].required


def test_preflight_requires_sql_parser_by_default(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    monkeypatch.setattr(preflight_module, "sqlglot", None)

    checks = run_preflight(str(db_path))
    by_name = {check.name: check for check in checks}

    assert not by_name["sql_parser"].passed
    assert by_name["sql_parser"].required
    assert "sqlglot is not installed" in by_name["sql_parser"].detail


def test_preflight_reports_embedding_configuration(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("IM_ONE_EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("IM_ONE_EMBEDDING_BASE_URL", "http://embedding.local/v1")

    check = preflight_module.check_embedding_configuration(required=True)

    assert check.passed
    assert check.required
    assert "text-embedding-3-small" in check.detail


def test_preflight_embedding_generation_requires_remote_retrieval_scoring(monkeypatch) -> None:
    monkeypatch.setattr(preflight_module, "remote_embedding", lambda text: (1.0, 0.0, 0.0))
    monkeypatch.setattr(
        preflight_module,
        "score_metric",
        lambda question, metric: RetrievalScore(
            metric_name=metric.name,
            keyword_hits=1,
            token_overlap=1,
            vector_similarity=1.0,
            total_score=8.2,
            embedding_source="remote",
        ),
    )

    check = preflight_module.check_embedding_generation()

    assert check.passed
    assert "dimensions=3" in check.detail
    assert "retrieval_source=remote" in check.detail


def test_preflight_embedding_generation_rejects_too_small_vector(monkeypatch) -> None:
    monkeypatch.setattr(preflight_module, "remote_embedding", lambda text: (1.0, 0.0))

    check = preflight_module.check_embedding_generation()

    assert not check.passed
    assert "dimensions=2 below minimum 3" in check.detail


def test_preflight_embedding_generation_rejects_local_retrieval_fallback(monkeypatch) -> None:
    monkeypatch.setattr(preflight_module, "remote_embedding", lambda text: (1.0, 0.0, 0.0))
    monkeypatch.setattr(
        preflight_module,
        "score_metric",
        lambda question, metric: RetrievalScore(
            metric_name=metric.name,
            keyword_hits=1,
            token_overlap=1,
            vector_similarity=1.0,
            total_score=8.2,
            embedding_source="local",
        ),
    )

    check = preflight_module.check_embedding_generation()

    assert not check.passed
    assert "schema retrieval scoring did not use remote embeddings" in check.detail


def test_preflight_accepts_explicit_local_embedding_runtime_without_api_key(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("IM_ONE_EMBEDDING_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("IM_ONE_EMBEDDING_MODEL", "local-embedding")
    monkeypatch.setenv("IM_ONE_EMBEDDING_ALLOW_LOCAL_NO_AUTH", "1")

    checks = run_preflight(str(db_path), require_embedding=True)
    by_name = {check.name: check for check in checks}

    assert by_name["embedding_configuration"].passed
    assert by_name["embedding_configuration"].required
    assert "model=local-embedding" in by_name["embedding_configuration"].detail
    assert "auth=local_no_auth" in by_name["embedding_configuration"].detail


def test_preflight_rejects_no_auth_remote_embedding_endpoint(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("IM_ONE_EMBEDDING_BASE_URL", "https://embedding.internal.example/v1")
    monkeypatch.setenv("IM_ONE_EMBEDDING_MODEL", "local-embedding")
    monkeypatch.setenv("IM_ONE_EMBEDDING_ALLOW_LOCAL_NO_AUTH", "1")

    checks = run_preflight(str(db_path), require_embedding=True)
    by_name = {check.name: check for check in checks}

    assert not by_name["embedding_configuration"].passed
    assert by_name["embedding_configuration"].required
    assert "local no-auth mode only applies to localhost" in by_name["embedding_configuration"].detail


def test_preflight_reports_trusted_header_auth(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    monkeypatch.setenv("IM_ONE_AUTH_MODE", "trusted_headers")
    monkeypatch.setenv("IM_ONE_TRUSTED_PROXY_TOKEN", "proxy-secret")

    checks = run_preflight(str(db_path), require_trusted_auth=True)
    by_name = {check.name: check for check in checks}

    assert by_name["trusted_header_auth"].passed
    assert by_name["trusted_header_auth"].required
    assert "IM_ONE_TRUSTED_PROXY_TOKEN configured" in by_name["trusted_header_auth"].detail


def test_preflight_can_require_trusted_proxy_token(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    monkeypatch.setenv("IM_ONE_AUTH_MODE", "trusted_headers")
    monkeypatch.delenv("IM_ONE_TRUSTED_PROXY_TOKEN", raising=False)

    checks = run_preflight(str(db_path), require_trusted_proxy_token=True)
    by_name = {check.name: check for check in checks}

    assert not by_name["trusted_header_auth"].passed
    assert by_name["trusted_header_auth"].required
    assert "IM_ONE_TRUSTED_PROXY_TOKEN not configured" in by_name["trusted_header_auth"].detail


def test_preflight_reports_feedback_store_readiness(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    feedback_path = tmp_path / "logs" / "feedback.jsonl"
    monkeypatch.setenv("IM_ONE_FEEDBACK_PATH", str(feedback_path))

    checks = run_preflight(str(db_path), require_feedback_store=True)
    by_name = {check.name: check for check in checks}

    assert by_name["feedback_store"].passed
    assert by_name["feedback_store"].required
    assert str(feedback_path) in by_name["feedback_store"].detail


def test_preflight_feedback_store_required_fails_when_unwritable(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    blocked_parent = tmp_path / "blocked_parent"
    blocked_parent.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("IM_ONE_FEEDBACK_PATH", str(blocked_parent / "feedback.jsonl"))

    optional_checks = run_preflight(str(db_path), require_feedback_store=False)
    required_checks = run_preflight(str(db_path), require_feedback_store=True)
    optional_by_name = {check.name: check for check in optional_checks}
    required_by_name = {check.name: check for check in required_checks}

    assert optional_by_name["feedback_store"].passed
    assert not optional_by_name["feedback_store"].required
    assert not required_by_name["feedback_store"].passed
    assert required_by_name["feedback_store"].required
