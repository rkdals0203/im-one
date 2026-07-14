from __future__ import annotations

import argparse

from im_one_agent.env import load_project_env
from im_one_agent.evaluation import (
    EVALUATION_CASES,
    PRD_EVALUATION_THRESHOLDS,
    EvaluationCase,
    build_evaluation_summary,
    evaluation_case_group,
    evaluation_threshold_failures,
    run_evaluation,
    write_evaluation_report,
    write_evaluation_markdown_summary,
    write_verified_question_manifest,
)

load_project_env()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the iM One NL2SQL evaluation set.")
    parser.add_argument("--db-path", default="data/im_one_demo.sqlite")
    parser.add_argument("--audit-path", default="logs/evaluation_audit.jsonl")
    parser.add_argument("--output", default="logs/evaluation_report.json")
    parser.add_argument("--markdown-output", help="Write a Markdown summary for review or presentation.")
    parser.add_argument("--verified-output", help="Write the verified question manifest JSON.")
    parser.add_argument("--role", default="branch_manager")
    parser.add_argument("--branch-id", type=int, default=1)
    parser.add_argument(
        "--case-group",
        action="append",
        choices=sorted({evaluation_case_group(case) for case in EVALUATION_CASES}),
        help="Run only cases in this group. Repeatable.",
    )
    parser.add_argument("--case-id", action="append", help="Run only a specific case id. Repeatable.")
    parser.add_argument("--blocked-only", action="store_true", help="Run only safety cases that should be blocked.")
    parser.add_argument("--non-blocked-only", action="store_true", help="Run only executable non-blocked cases.")
    parser.add_argument("--strict-prd", action="store_true", help="Fail unless PRD POC success thresholds are met.")
    parser.add_argument("--min-total-cases", type=int)
    parser.add_argument("--min-core-demo-total", type=int)
    parser.add_argument("--min-non-blocked-total", type=int)
    parser.add_argument("--min-blocked-total", type=int)
    parser.add_argument("--min-gold-compared-total", type=int)
    parser.add_argument("--min-core-demo-success-rate", type=float)
    parser.add_argument("--min-non-blocked-execution-success-rate", type=float)
    parser.add_argument("--min-blocked-rejection-rate", type=float)
    parser.add_argument("--min-pass-rate", type=float)
    parser.add_argument("--min-latency-success-rate", type=float)
    return parser.parse_args()


def select_cases(args: argparse.Namespace) -> tuple[EvaluationCase, ...]:
    cases = tuple(EVALUATION_CASES)
    if args.case_group:
        selected_groups = set(args.case_group)
        cases = tuple(case for case in cases if evaluation_case_group(case) in selected_groups)
    if args.case_id:
        selected_ids = set(args.case_id)
        known_ids = {case.case_id for case in EVALUATION_CASES}
        unknown_ids = sorted(selected_ids - known_ids)
        if unknown_ids:
            raise SystemExit("Unknown evaluation case id(s): " + ", ".join(unknown_ids))
        cases = tuple(case for case in cases if case.case_id in selected_ids)
    if args.blocked_only and args.non_blocked_only:
        raise SystemExit("--blocked-only and --non-blocked-only cannot be used together.")
    if args.blocked_only:
        cases = tuple(case for case in cases if case.should_block)
    if args.non_blocked_only:
        cases = tuple(case for case in cases if not case.should_block)
    if not cases:
        raise SystemExit("No evaluation cases matched the selected filters.")
    return cases


def main() -> None:
    args = parse_args()
    cases = select_cases(args)
    results = run_evaluation(
        db_path=args.db_path,
        audit_path=args.audit_path,
        cases=cases,
        role=args.role,
        branch_id=args.branch_id,
    )
    write_evaluation_report(results, args.output, cases=cases)
    if args.markdown_output:
        write_evaluation_markdown_summary(results, args.markdown_output, cases=cases)
    if args.verified_output:
        write_verified_question_manifest(args.verified_output, role=args.role, branch_id=args.branch_id)
    passed = sum(1 for result in results if result.passed)
    print(f"Evaluation: {passed}/{len(results)} passed")
    print(f"Report: {args.output}")
    if args.markdown_output:
        print(f"Markdown: {args.markdown_output}")

    summary = build_evaluation_summary(results, cases=cases)
    min_total_cases = args.min_total_cases
    min_core_demo_total = args.min_core_demo_total
    min_non_blocked_total = args.min_non_blocked_total
    min_blocked_total = args.min_blocked_total
    min_gold_compared_total = args.min_gold_compared_total
    min_core_demo_success_rate = args.min_core_demo_success_rate
    min_non_blocked_execution_success_rate = args.min_non_blocked_execution_success_rate
    min_blocked_rejection_rate = args.min_blocked_rejection_rate
    min_latency_success_rate = args.min_latency_success_rate
    if args.strict_prd:
        min_total_cases = (
            PRD_EVALUATION_THRESHOLDS["min_total_cases"]
            if min_total_cases is None
            else min_total_cases
        )
        min_core_demo_total = (
            PRD_EVALUATION_THRESHOLDS["min_core_demo_total"]
            if min_core_demo_total is None
            else min_core_demo_total
        )
        min_non_blocked_total = (
            PRD_EVALUATION_THRESHOLDS["min_non_blocked_total"]
            if min_non_blocked_total is None
            else min_non_blocked_total
        )
        min_blocked_total = (
            PRD_EVALUATION_THRESHOLDS["min_blocked_total"]
            if min_blocked_total is None
            else min_blocked_total
        )
        min_gold_compared_total = (
            PRD_EVALUATION_THRESHOLDS["min_gold_compared_total"]
            if min_gold_compared_total is None
            else min_gold_compared_total
        )
        min_core_demo_success_rate = (
            PRD_EVALUATION_THRESHOLDS["min_core_demo_success_rate"]
            if min_core_demo_success_rate is None
            else min_core_demo_success_rate
        )
        min_non_blocked_execution_success_rate = (
            PRD_EVALUATION_THRESHOLDS["min_non_blocked_execution_success_rate"]
            if min_non_blocked_execution_success_rate is None
            else min_non_blocked_execution_success_rate
        )
        min_blocked_rejection_rate = (
            PRD_EVALUATION_THRESHOLDS["min_blocked_rejection_rate"]
            if min_blocked_rejection_rate is None
            else min_blocked_rejection_rate
        )
        min_latency_success_rate = (
            PRD_EVALUATION_THRESHOLDS["min_latency_success_rate"]
            if min_latency_success_rate is None
            else min_latency_success_rate
        )

    failures = evaluation_threshold_failures(
        summary,
        min_total_cases=min_total_cases,
        min_core_demo_total=min_core_demo_total,
        min_non_blocked_total=min_non_blocked_total,
        min_blocked_total=min_blocked_total,
        min_gold_compared_total=min_gold_compared_total,
        min_core_demo_success_rate=min_core_demo_success_rate,
        min_non_blocked_execution_success_rate=min_non_blocked_execution_success_rate,
        min_blocked_rejection_rate=min_blocked_rejection_rate,
        min_pass_rate=args.min_pass_rate,
        min_latency_success_rate=min_latency_success_rate,
    )
    if failures:
        print("Evaluation thresholds failed:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
