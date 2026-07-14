from __future__ import annotations

import argparse
import json
import stat
from types import SimpleNamespace

import pytest

import im_one_agent.cli as cli
import im_one_agent.evaluate as evaluate_cli
import im_one_agent.evidence as evidence_cli
from im_one_agent.domain import MAX_QUESTION_LENGTH
from im_one_agent.sql_generator import GeneratedSQL
from im_one_agent.sql_safety import ValidationResult


def make_args(tmp_path, question: str | None, demo: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        question=question,
        demo=demo,
        role="branch_manager",
        branch_id=1,
        db_path=str(tmp_path / "demo.sqlite"),
        audit_path=str(tmp_path / "audit.jsonl"),
    )


def make_evaluate_args(tmp_path, **overrides) -> argparse.Namespace:
    values = {
        "db_path": str(tmp_path / "eval.sqlite"),
        "audit_path": str(tmp_path / "eval_audit.jsonl"),
        "output": str(tmp_path / "evaluation_report.json"),
        "markdown_output": str(tmp_path / "evaluation_summary.md"),
        "verified_output": None,
        "role": "branch_manager",
        "branch_id": 1,
        "case_group": None,
        "case_id": None,
        "blocked_only": False,
        "non_blocked_only": False,
        "strict_prd": False,
        "min_total_cases": None,
        "min_core_demo_total": None,
        "min_non_blocked_total": None,
        "min_blocked_total": None,
        "min_gold_compared_total": None,
        "min_core_demo_success_rate": None,
        "min_non_blocked_execution_success_rate": None,
        "min_blocked_rejection_rate": None,
        "min_pass_rate": None,
        "min_latency_success_rate": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def make_evidence_args(tmp_path, **overrides) -> argparse.Namespace:
    values = {
        "output_dir": str(tmp_path / "evidence_pack"),
        "db_path": str(tmp_path / "evidence.sqlite"),
        "audit_path": str(tmp_path / "evidence_audit.jsonl"),
        "role": "sales_planning",
        "branch_id": 1,
        "profile": "poc",
        "live_checks": False,
        "strict": False,
        "case_group": None,
        "case_id": None,
        "blocked_only": True,
        "non_blocked_only": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_cli_rejects_empty_question_before_agent_execution(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "parse_args", lambda: make_args(tmp_path, "   "))
    monkeypatch.setattr(
        cli,
        "run_question",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("agent should not run")),
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert "질문을 입력해주세요" in str(exc.value)


def test_cli_rejects_overlong_question_before_agent_execution(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "parse_args", lambda: make_args(tmp_path, "가" * (MAX_QUESTION_LENGTH + 1)))
    monkeypatch.setattr(
        cli,
        "run_question",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("agent should not run")),
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert f"{MAX_QUESTION_LENGTH}자 이하" in str(exc.value)


def test_cli_trims_question_before_agent_execution(tmp_path, monkeypatch, capsys) -> None:
    seen_questions: list[str] = []

    def fake_run_question(question: str, *args, **kwargs) -> str:
        seen_questions.append(question)
        return "ok"

    monkeypatch.setattr(cli, "parse_args", lambda: make_args(tmp_path, "  민원 현황 알려줘.  "))
    monkeypatch.setattr(cli, "run_question", fake_run_question)

    cli.main()

    assert seen_questions == ["민원 현황 알려줘."]
    assert "ok" in capsys.readouterr().out


def test_cli_detects_llm_generation_failure_result() -> None:
    assert cli.is_llm_generation_failure(
        {
            "generated": GeneratedSQL(
                sql="",
                reason="LLM SQL 생성 단계에서 실패했습니다.",
                engine="llm",
                error="credentials missing",
            ),
            "validation": ValidationResult(False, "", ("LLM SQL 생성 실패: credentials missing",), ()),
        }
    )
    assert not cli.is_llm_generation_failure(
        {
            "generated": GeneratedSQL(
                sql="",
                reason="질문 의도 검증 단계에서 차단되었습니다.",
                engine="intent_guard",
                error="unsafe request",
            ),
            "validation": ValidationResult(False, "", ("위험 요청 차단: unsafe request",), ()),
        }
    )


def test_evaluate_cli_selects_case_groups_and_ids(tmp_path) -> None:
    group_args = make_evaluate_args(tmp_path, case_group=["block"])
    id_args = make_evaluate_args(tmp_path, case_id=["core-001", "block-001"])

    assert all(case.should_block for case in evaluate_cli.select_cases(group_args))
    assert {case.case_id for case in evaluate_cli.select_cases(id_args)} == {"core-001", "block-001"}


def test_evaluate_cli_rejects_invalid_case_filters(tmp_path) -> None:
    with pytest.raises(SystemExit, match="Unknown evaluation case id"):
        evaluate_cli.select_cases(make_evaluate_args(tmp_path, case_id=["missing-case"]))
    with pytest.raises(SystemExit, match="cannot be used together"):
        evaluate_cli.select_cases(make_evaluate_args(tmp_path, blocked_only=True, non_blocked_only=True))
    with pytest.raises(SystemExit, match="No evaluation cases"):
        evaluate_cli.select_cases(make_evaluate_args(tmp_path, case_group=["core"], blocked_only=True))


def test_evaluate_cli_blocked_only_writes_report_without_llm(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    args = make_evaluate_args(tmp_path, blocked_only=True, min_blocked_rejection_rate=1.0)
    monkeypatch.setattr(evaluate_cli, "parse_args", lambda: args)

    evaluate_cli.main()
    output = capsys.readouterr().out

    assert "Evaluation: 7/7 passed" in output
    assert "Markdown:" in output
    assert "blocked_rejection_rate" in (tmp_path / "evaluation_report.json").read_text(encoding="utf-8")
    assert "Blocked rejection | 100.0%" in (tmp_path / "evaluation_summary.md").read_text(encoding="utf-8")


def test_evaluate_cli_strict_prd_rejects_partial_core_runs(tmp_path, monkeypatch, capsys) -> None:
    args = make_evaluate_args(tmp_path, strict_prd=True, case_group=["core"], markdown_output=None)
    monkeypatch.setattr(evaluate_cli, "parse_args", lambda: args)
    monkeypatch.setattr(evaluate_cli, "write_evaluation_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(evaluate_cli, "write_evaluation_markdown_summary", lambda *args, **kwargs: None)

    def fake_run_evaluation(*args, cases, **kwargs):
        return [
            SimpleNamespace(
                case_id=case.case_id,
                passed=True,
                allowed=True,
                gold_match=True,
                elapsed_ms=1.0,
            )
            for case in cases
        ]

    monkeypatch.setattr(evaluate_cli, "run_evaluation", fake_run_evaluation)

    with pytest.raises(SystemExit) as exc:
        evaluate_cli.main()

    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert "Evaluation: 5/5 passed" in output
    assert "total_cases=5 below required 30" in output
    assert "blocked_total=0 below required 2" in output
    assert "gold_compared_total=5 below required 30" in output


def test_evidence_pack_writes_review_bundle_without_live_checks(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    args = make_evidence_args(tmp_path)
    monkeypatch.setattr(evidence_cli, "parse_args", lambda: args)

    evidence_cli.main()
    output = capsys.readouterr().out
    output_dir = tmp_path / "evidence_pack"

    assert "Evidence pack:" in output
    assert "Evidence gate: failed" in output
    assert (output_dir / "readiness.json").exists()
    assert (output_dir / "external_readiness.json").exists()
    assert (output_dir / "prd_traceability.json").exists()
    assert (output_dir / "completion_audit.json").exists()
    assert (output_dir / "audit_log.jsonl").exists()
    assert (output_dir / "audit_summary.json").exists()
    assert (output_dir / "database_audit_log.json").exists()
    assert (output_dir / "sql_validation_probes.json").exists()
    assert (output_dir / "schema_retrieval_probes.json").exists()
    assert (output_dir / "query_execution_samples.json").exists()
    assert (output_dir / "role_policy_matrix.json").exists()
    assert (output_dir / "llm_prompt_contract.json").exists()
    assert (output_dir / "live_llm_generation_samples.json").exists()
    assert (output_dir / "llm_evaluation_diagnostics.json").exists()
    assert (output_dir / "result_explanation_samples.json").exists()
    assert (output_dir / "ui_layout_contract.json").exists()
    assert (output_dir / "evaluation_report.json").exists()
    assert (output_dir / "evaluation_diff_summary.json").exists()
    assert (output_dir / "evaluation_summary.md").exists()
    assert (output_dir / "verified_questions.json").exists()
    assert (output_dir / "catalog_governance.json").exists()
    command_script = output_dir / "external_readiness_commands.sh"
    assert command_script.exists()
    assert command_script.stat().st_mode & stat.S_IXUSR
    assert (output_dir / "manifest.json").exists()
    index = (output_dir / "README.md").read_text(encoding="utf-8")
    assert "# iM One NL2SQL Evidence Pack" in index
    assert "## Readiness Next Actions" in index
    assert "## Evidence Gate" in index
    assert "## External Readiness" in index
    assert "## PRD Traceability" in index
    assert "## PRD Evaluation Gate" in index
    assert "## Completion Audit" in index
    assert "## Audit Log Evidence" in index
    assert "Events: 7" in index
    assert "Summary: `audit_summary.json`" in index
    assert "Database audit events: 7" in index
    assert "Database artifact: `database_audit_log.json`" in index
    assert "## SQL Validation Evidence" in index
    assert "Probes: 19" in index
    assert "Failed probes: 0" in index
    assert "## Schema Retrieval Evidence" in index
    assert "Probes: 6" in index
    assert "## Query Execution Evidence" in index
    assert "Backend: `sqlite`" in index
    assert "Samples: 3" in index
    assert "Failed samples: 0" in index
    assert "## Role Policy Evidence" in index
    assert "Roles: 3" in index
    assert "## LLM Prompt Contract Evidence" in index
    assert "Prompt version: `im-one-nl2sql-v1`" in index
    assert "Selected tables: `branches`, `product_sales`, `voc_cases`" in index
    assert "Core demo prompt contracts: 5" in index
    assert "Sanitization passed: yes" in index
    assert "## Live LLM Generation Evidence" in index
    assert "Status: not_run" in index
    assert "Live checks: disabled" in index
    assert "## LLM Evaluation Diagnostics" in index
    assert "Endpoint configured: no" in index
    assert "Next commands: 1" in index
    assert "## Result Explanation Evidence" in index
    assert "Allowed sample validation: passed" in index
    assert "Blocked sample validation: blocked" in index
    assert "## Evaluation Diff Evidence" in index
    assert "Diff cases: 0" in index
    assert "Gold mismatches: 0" in index
    assert "## UI Layout Contract Evidence" in index
    assert "Status: passed" in index
    assert "Contracts passed: 9/9" in index
    assert "PRD requirements passed:" in index
    assert "core_demo_success_rate=0.0 below required 1.0" in index
    assert "`llm_configuration` (required)" in index
    assert "`llm_generation` (required)" in index
    assert "Missing external items |" not in index
    assert "Missing external items: 1" in index
    assert "`approved_llm_gateway`" in index
    assert "Missing verification commands:" in index
    assert "`python -m im_one_agent.evidence --profile poc --live-checks --strict`" in index
    assert "Environment keys: `OPENAI_API_KEY`, `IM_ONE_LLM_MODEL`" in index
    manifest = (output_dir / "manifest.json").read_text(encoding="utf-8")
    assert '"evaluation_total_cases": 7' in manifest
    assert '"blocked_rejection_rate": 1.0' in manifest
    assert '"readiness_next_actions"' in manifest
    assert '"evidence_gate"' in manifest
    assert '"status": "failed"' in manifest
    assert '"name": "readiness_required_failures"' in manifest
    assert '"name": "external_readiness_missing"' in manifest
    assert '"name": "prd_evaluation_gate_failed"' in manifest
    assert '"external_readiness"' in manifest
    assert '"completion_audit"' in manifest
    assert '"completion_audit": "completion_audit.json"' in manifest
    assert '"audit_log": "audit_log.jsonl"' in manifest
    assert '"audit_summary": "audit_summary.json"' in manifest
    assert '"database_audit_log": "database_audit_log.json"' in manifest
    assert '"sql_validation_probes": "sql_validation_probes.json"' in manifest
    assert '"schema_retrieval_probes": "schema_retrieval_probes.json"' in manifest
    assert '"query_execution_samples": "query_execution_samples.json"' in manifest
    assert '"role_policy_matrix": "role_policy_matrix.json"' in manifest
    assert '"llm_prompt_contract": "llm_prompt_contract.json"' in manifest
    assert '"live_llm_generation_samples": "live_llm_generation_samples.json"' in manifest
    assert '"llm_evaluation_diagnostics": "llm_evaluation_diagnostics.json"' in manifest
    assert '"result_explanation_samples": "result_explanation_samples.json"' in manifest
    assert '"ui_layout_contract": "ui_layout_contract.json"' in manifest
    assert '"evaluation_diff_summary": "evaluation_diff_summary.json"' in manifest
    assert '"event_count": 7' in manifest
    assert '"audit_summary"' in manifest
    assert '"blocked_count": 7' in manifest
    assert '"by_execution_status": {\n        "blocked": 7\n      }' in manifest
    assert '"missing_names": [\n        "approved_llm_gateway"\n      ]' in manifest
    assert '"missing_verification_commands"' in manifest
    assert '"command": "python -m im_one_agent.evidence --profile poc --live-checks --strict"' in manifest
    assert '"OPENAI_API_KEY"' in manifest
    assert '"name": "llm_configuration"' in manifest
    assert '"name": "llm_generation"' in manifest
    assert '"prd_traceability"' in manifest
    assert '"prd_thresholds"' in manifest
    assert '"passed": false' in manifest
    assert '"core_demo_success_rate=0.0 below required 1.0"' in manifest
    audit_log = (output_dir / "audit_log.jsonl").read_text(encoding="utf-8")
    assert audit_log.count("\n") >= 7
    assert '"original_question": "branches 테이블 삭제해줘."' in audit_log
    assert '"validation_status": "blocked"' in audit_log
    assert '"execution_status": "blocked"' in audit_log
    audit_summary = json.loads((output_dir / "audit_summary.json").read_text(encoding="utf-8"))
    assert audit_summary["total"] == 7
    assert audit_summary["blocked_count"] == 7
    assert audit_summary["executed_count"] == 0
    assert audit_summary["by_execution_status"] == {"blocked": 7}
    assert audit_summary["by_validation_status"] == {"blocked": 7}
    assert audit_summary["by_engine"]["intent_guard"] == 7
    assert audit_summary["recent"][0]["execution_status"] == "blocked"
    database_audit_log = json.loads((output_dir / "database_audit_log.json").read_text(encoding="utf-8"))
    assert len(database_audit_log) == 7
    assert database_audit_log[0]["audit_source"] == "query_audit_log"
    assert database_audit_log[0]["original_question"] == "branches 테이블 삭제해줘."
    assert database_audit_log[0]["validation_status"] == "blocked"
    assert database_audit_log[0]["execution_status"] == "blocked"
    assert "llm_generated_sql" in database_audit_log[0]
    assert "policy_applied_sql" in database_audit_log[0]
    assert "validated_sql" in database_audit_log[0]
    assert "sql_policy_transformations" in database_audit_log[0]
    sql_validation_probes = json.loads((output_dir / "sql_validation_probes.json").read_text(encoding="utf-8"))
    assert sql_validation_probes["passed"] is True
    assert sql_validation_probes["probe_total"] == 19
    assert sql_validation_probes["failed_total"] == 0
    probe_by_name = {probe["name"]: probe for probe in sql_validation_probes["probes"]}
    assert probe_by_name["safe_aggregate"]["actual_allowed"] is True
    assert probe_by_name["dml_block"]["actual_allowed"] is False
    assert probe_by_name["select_star_block"]["actual_allowed"] is False
    assert probe_by_name["unknown_column_block"]["actual_allowed"] is False
    assert probe_by_name["branch_scope_block"]["branch_scope_branch_id"] == 1
    schema_retrieval_probes = json.loads((output_dir / "schema_retrieval_probes.json").read_text(encoding="utf-8"))
    assert schema_retrieval_probes["passed"] is True
    assert schema_retrieval_probes["probe_total"] == 6
    assert schema_retrieval_probes["failed_total"] == 0
    retrieval_probe_by_name = {probe["name"]: probe for probe in schema_retrieval_probes["probes"]}
    assert "voc_cases" in retrieval_probe_by_name["voc_status"]["selected_tables"]
    assert retrieval_probe_by_name["voc_status"]["retrieval_confidence"] == "high"
    assert retrieval_probe_by_name["ambiguous_question"]["retrieval_confidence"] == "low"
    assert retrieval_probe_by_name["ambiguous_question"]["clarification_options"]
    assert {"accounts", "branches", "voc_cases"}.issubset(
        set(retrieval_probe_by_name["follow_up_context"]["selected_tables"])
    )
    assert retrieval_probe_by_name["follow_up_context"]["previous_metrics"] == ["new_accounts"]
    query_execution_samples = json.loads((output_dir / "query_execution_samples.json").read_text(encoding="utf-8"))
    assert query_execution_samples["passed"] is True
    assert query_execution_samples["backend"] == "sqlite"
    assert query_execution_samples["sample_total"] == 3
    sample_by_name = {sample["name"]: sample for sample in query_execution_samples["samples"]}
    assert sample_by_name["aggregate_result"]["validation_allowed"] is True
    assert sample_by_name["aggregate_result"]["execution"]["row_count"] > 0
    assert sample_by_name["aggregate_result"]["execution"]["column_metadata"]
    assert sample_by_name["aggregate_result"]["execution"]["query_plan_summary"]
    assert sample_by_name["empty_result"]["validation_allowed"] is True
    assert sample_by_name["empty_result"]["execution"]["row_count"] == 0
    assert sample_by_name["empty_result"]["execution"]["columns"] == ["branch_name", "matching_voc_count"]
    assert sample_by_name["branch_scoped_result"]["role"] == "branch_manager"
    assert sample_by_name["branch_scoped_result"]["branch_id"] == 1
    assert sample_by_name["branch_scoped_result"]["execution"]["pre_execution_row_count_status"] == "checked"
    role_policy_matrix = json.loads((output_dir / "role_policy_matrix.json").read_text(encoding="utf-8"))
    assert role_policy_matrix["passed"] is True
    assert role_policy_matrix["role_total"] == 3
    assert role_policy_matrix["operational_tables_excluded_from_roles"] is True
    roles_by_name = {role["role"]: role for role in role_policy_matrix["roles"]}
    assert "accounts" in roles_by_name["sales_planning"]["allowed_tables"]
    assert "accounts" not in roles_by_name["compliance"]["allowed_tables"]
    probe_by_name = {probe["name"]: probe for probe in role_policy_matrix["probes"]}
    assert probe_by_name["sales_planning_allows_target_aggregate"]["actual_allowed"] is True
    assert probe_by_name["compliance_blocks_accounts"]["actual_allowed"] is False
    assert probe_by_name["branch_manager_requires_branch_scope"]["actual_allowed"] is False
    assert probe_by_name["branch_manager_allows_scoped_aggregate"]["actual_allowed"] is True
    assert "accounts" not in probe_by_name["schema_retrieval_excludes_role_disallowed_tables"]["retrieved_tables"]
    assert probe_by_name["operational_audit_table_blocked"]["actual_allowed"] is False
    llm_prompt_contract = json.loads((output_dir / "llm_prompt_contract.json").read_text(encoding="utf-8"))
    assert llm_prompt_contract["prompt_version"] == "im-one-nl2sql-v1"
    assert llm_prompt_contract["response_format"] == {"type": "json_object"}
    assert all(llm_prompt_contract["system_prompt_checks"].values())
    assert all(llm_prompt_contract["sanitization_checks"].values())
    assert all(llm_prompt_contract["response_contract_checks"].values())
    assert llm_prompt_contract["response_contract"]["required"] == ["sql", "reason", "assumptions"]
    assert llm_prompt_contract["response_contract"]["properties"]["assumptions"]["items"]["type"] == "string"
    assert llm_prompt_contract["user_context"]["role_policy"]["selected_tables_only"] is True
    assert llm_prompt_contract["user_context"]["selected_table_names"] == [
        "branches",
        "product_sales",
        "voc_cases",
    ]
    assert {
        metric["name"]
        for metric in llm_prompt_contract["user_context"]["matched_metrics"]
    } == {"els_sales_vs_voc"}
    assert "previous_rows_sample" not in llm_prompt_contract["user_context"]["conversation_context"]
    assert "ignore_policy" not in llm_prompt_contract["user_context"]["conversation_context"]
    assert len(llm_prompt_contract["core_demo_contracts"]) == 5
    assert all(
        contract["selected_schema_only"]
        and contract["matched_metric_names"]
        and contract["dataset_classification"] == "synthetic_poc"
        and contract["operational_tables_removed"]
        for contract in llm_prompt_contract["core_demo_contracts"]
    )
    assert {
        contract["intent"]
        for contract in llm_prompt_contract["core_demo_contracts"]
    } == {
        "new_accounts",
        "high_risk_product_sales",
        "voc_status",
        "els_sales_vs_voc",
        "investment_review_status",
    }
    live_llm_generation_samples = json.loads(
        (output_dir / "live_llm_generation_samples.json").read_text(encoding="utf-8")
    )
    assert live_llm_generation_samples["status"] == "not_run"
    assert live_llm_generation_samples["live_checks_enabled"] is False
    assert live_llm_generation_samples["case_total"] == 5
    assert live_llm_generation_samples["passed_cases"] == 0
    assert live_llm_generation_samples["cases"] == []
    llm_evaluation_diagnostics = json.loads(
        (output_dir / "llm_evaluation_diagnostics.json").read_text(encoding="utf-8")
    )
    assert llm_evaluation_diagnostics["status"] == "action_required"
    assert llm_evaluation_diagnostics["llm_endpoint"]["configured"] is False
    assert llm_evaluation_diagnostics["llm_endpoint"]["api_key_configured"] is False
    assert llm_evaluation_diagnostics["live_llm_generation"]["status"] == "not_run"
    assert llm_evaluation_diagnostics["evaluation_gate"]["passed"] is False
    assert llm_evaluation_diagnostics["failure_analysis"]["llm_generation_failure_total"] == 0
    assert llm_evaluation_diagnostics["remaining_gates"]["external_missing_names"] == ["approved_llm_gateway"]
    assert llm_evaluation_diagnostics["next_commands"][0]["name"] == "approved_llm_gateway"
    assert llm_evaluation_diagnostics["next_commands"][0]["command"] == (
        "python -m im_one_agent.evidence --profile poc --live-checks --strict"
    )
    result_explanation_samples = json.loads(
        (output_dir / "result_explanation_samples.json").read_text(encoding="utf-8")
    )
    assert result_explanation_samples["allowed_sample"]["validation_allowed"] is True
    assert result_explanation_samples["blocked_sample"]["validation_allowed"] is False
    assert all(result_explanation_samples["coverage_checks"].values())
    assert "지표 정의:" in result_explanation_samples["allowed_sample"]["explanation"]
    assert "검증 근거:" in result_explanation_samples["allowed_sample"]["explanation"]
    assert "SQL Validation Layer에서 실행 전 차단되었습니다." in result_explanation_samples["blocked_sample"]["explanation"]
    ui_layout_contract = json.loads((output_dir / "ui_layout_contract.json").read_text(encoding="utf-8"))
    assert ui_layout_contract["passed"] is True
    assert ui_layout_contract["missing_files"] == []
    assert ui_layout_contract["contracts"]["home_entry"]["passed"] is True
    assert ui_layout_contract["contracts"]["desktop_workbench"]["passed"] is True
    assert ui_layout_contract["contracts"]["table_scroll_safety"]["passed"] is True
    assert ui_layout_contract["contracts"]["chat_trace_panel"]["passed"] is True
    assert ui_layout_contract["contracts"]["responsive_breakpoints"]["passed"] is True
    assert ui_layout_contract["contracts"]["dynamic_result_height"]["passed"] is True
    evaluation_diff_summary = json.loads((output_dir / "evaluation_diff_summary.json").read_text(encoding="utf-8"))
    assert evaluation_diff_summary["total_cases"] == 7
    assert evaluation_diff_summary["diff_case_total"] == 0
    assert evaluation_diff_summary["gold_mismatched_total"] == 0
    assert evaluation_diff_summary["llm_generation_failure_total"] == 0
    assert evaluation_diff_summary["cases"] == []
    traceability = (output_dir / "prd_traceability.json").read_text(encoding="utf-8")
    assert '"requirement_id": "FR-001"' in traceability
    assert '"requirement_id": "FR-012"' in traceability
    assert '"category": "nonfunctional"' in traceability
    assert '"name": "llm_generation"' in traceability
    assert '"status": "not_run"' in traceability
    completion_audit = json.loads((output_dir / "completion_audit.json").read_text(encoding="utf-8"))
    assert completion_audit["status"] == "failed"
    assert completion_audit["passed"] is False
    assert completion_audit["summary"]["requirement_total"] >= 16
    assert completion_audit["summary"]["requirement_status_counts"]["failed"] >= 2
    assert completion_audit["summary"]["requirement_incomplete_total"] >= 2
    assert completion_audit["summary"]["blocking_condition_count"] == 4
    assert completion_audit["prd_evaluation_gate"]["metrics"]["total_cases"] == 7
    assert completion_audit["prd_evaluation_gate"]["metrics"]["core_demo_total"] == 0
    assert completion_audit["prd_evaluation_gate"]["metrics"]["non_blocked_total"] == 0
    assert completion_audit["prd_evaluation_gate"]["metrics"]["blocked_total"] == 7
    assert completion_audit["prd_evaluation_gate"]["metrics"]["gold_compared_total"] == 0
    assert {item["name"] for item in completion_audit["blocking_conditions"]} == {
        "readiness_required_failures",
        "external_readiness_missing",
        "prd_evaluation_gate_failed",
        "requirement_completion_incomplete",
    }
    requirement_completion_block = next(
        item
        for item in completion_audit["blocking_conditions"]
        if item["name"] == "requirement_completion_incomplete"
    )
    assert any(
        detail["requirement_id"] == "FR-012"
        and "prd_evaluation_gate" in detail["checks_requiring_attention"]
        for detail in requirement_completion_block["details"]
    )
    assert any(
        item["name"] == "approved_llm_gateway"
        for item in completion_audit["external_readiness"]["items"]
    )
    assert any(
        requirement["requirement_id"] == "FR-001"
        for requirement in completion_audit["requirements"]
    )
    assert any(
        requirement["requirement_id"] == "FR-004"
        and requirement["status"] == "failed"
        and any(check["name"] == "llm_generation" for check in requirement["checks_requiring_attention"])
        for requirement in completion_audit["requirements"]
    )
    assert any(
        requirement["requirement_id"] == "FR-012"
        and requirement["status"] == "failed"
        and any(
            check["name"] == "prd_evaluation_gate"
            and check["status"] == "failed"
            and "core_demo_success_rate=0.0 below required 1.0" in check["detail"]
            for check in requirement["checks_requiring_attention"]
        )
        for requirement in completion_audit["requirements"]
    )
    external_readiness = (output_dir / "external_readiness.json").read_text(encoding="utf-8")
    assert '"name": "approved_llm_gateway"' in external_readiness
    assert '"status": "missing_evidence"' in external_readiness
    assert '"environment_keys"' in external_readiness
    assert '"verification_command": "python -m im_one_agent.evidence --profile poc --live-checks --strict"' in external_readiness
    assert '"evidence_required": "Five core demo questions must be generated by the approved LLM gateway' in external_readiness
    assert '"name": "llm_generation"' in external_readiness
    assert '"status": "not_run"' in external_readiness
    script = command_script.read_text(encoding="utf-8")
    assert script.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in script
    assert "==> approved_llm_gateway" in script
    assert "# Environment keys: OPENAI_API_KEY, IM_ONE_LLM_MODEL" in script
    assert "python -m im_one_agent.evidence --profile poc --live-checks --strict" in script
    assert f"--output-dir {args.output_dir}" in script
    assert f"--db-path {args.db_path}" in script
    assert f"--audit-path {args.audit_path}" in script
    assert "--role sales_planning" in script
    assert "--branch-id 1" in script
    assert "--blocked-only" not in script


def test_external_readiness_commands_follow_selected_profile() -> None:
    readiness = {
        "checks": [
            {
                "name": "llm_configuration",
                "passed": False,
                "required": True,
                "detail": "OPENAI_API_KEY is not configured.",
            },
            {
                "name": "embedding_configuration",
                "passed": False,
                "required": True,
                "detail": "IM_ONE_EMBEDDING_MODEL is not configured.",
            },
        ]
    }

    payload = evidence_cli.build_external_readiness_payload(
        readiness,
        profile="pilot",
        live_checks=False,
    )

    commands_by_name = {
        item["name"]: item["command"]
        for item in payload["missing_verification_commands"]
    }
    assert commands_by_name["approved_llm_gateway"] == (
        "python -m im_one_agent.evidence --profile pilot --live-checks --strict"
    )
    assert commands_by_name["approved_embedding_gateway"] == (
        "python -m im_one_agent.evidence --profile pilot --live-checks --strict"
    )
    live_actions = evidence_cli.build_live_check_next_actions(profile="pilot", live_checks=False)
    assert any(
        action["name"] == "llm_generation"
        and "prove the pilot LLM path" in action["action"]
        for action in live_actions
    )
    script = evidence_cli.build_external_readiness_command_script(
        payload,
        context={
            "output_dir": "/tmp/pilot evidence",
            "db_path": "/tmp/pilot.sqlite",
            "audit_path": "/tmp/pilot audit.jsonl",
            "role": "compliance",
            "branch_id": 3,
        },
    )
    assert "python -m im_one_agent.evidence --profile pilot --live-checks --strict" in script
    assert script.count("python -m im_one_agent.evidence --profile pilot --live-checks --strict") == 1
    assert "--output-dir '/tmp/pilot evidence'" in script
    assert "--db-path /tmp/pilot.sqlite" in script
    assert "--audit-path '/tmp/pilot audit.jsonl'" in script
    assert "--role compliance" in script
    assert "--branch-id 3" in script
    assert "python -m im_one_agent.preflight --require-api-token --db-path /tmp/pilot.sqlite" in script
    assert "Skipped duplicate command already emitted above" in script


def test_evidence_cli_strict_exits_nonzero_on_failed_gates(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    args = make_evidence_args(tmp_path, strict=True)
    monkeypatch.setattr(evidence_cli, "parse_args", lambda: args)

    with pytest.raises(SystemExit) as exc:
        evidence_cli.main()

    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert "Readiness required failures: 1" in output
    assert "Evidence gate: failed" in output
    assert (tmp_path / "evidence_pack" / "manifest.json").exists()


def test_cli_exits_nonzero_after_printing_llm_generation_failure(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "parse_args", lambda: make_args(tmp_path, "최근 30일 VOC 유형별 처리 현황 알려줘."))

    def fake_run_question(*args, **kwargs) -> str:
        raise cli.CliExecutionError("LLM SQL 생성 실패\n재시도 안내", exit_code=2)

    monkeypatch.setattr(cli, "run_question", fake_run_question)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert "LLM SQL 생성 실패" in output
    assert "재시도 안내" in output
