from __future__ import annotations

import argparse
from pathlib import Path

from im_one_agent.domain import normalize_question_text
from im_one_agent.env import load_project_env
from im_one_agent.graph import build_agent
from im_one_agent.sample_data import ensure_demo_database

load_project_env()

DEMO_QUESTIONS = [
    "지난 3개월간 지점별 신규 계좌 수 추이는?",
    "이번 달 고위험 상품 가입 건수가 많은 지점은?",
    "최근 30일 VOC 유형별 처리 현황 알려줘.",
    "영업점별 ELS 가입 금액과 민원 건수를 비교해줘.",
    "최근 투자성향 점검 미완료 건수가 많은 지점은?",
]


class CliExecutionError(RuntimeError):
    def __init__(self, answer: str, exit_code: int = 2):
        super().__init__(answer)
        self.answer = answer
        self.exit_code = exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the iM One NL2SQL agent POC.")
    parser.add_argument("--question", help="Korean business question to run.")
    parser.add_argument("--demo", action="store_true", help="Run bundled demo questions.")
    parser.add_argument("--role", default="branch_manager", help="Demo user role.")
    parser.add_argument("--branch-id", type=int, default=1, help="Branch scope for branch_manager role.")
    parser.add_argument("--db-path", default="data/im_one_demo.sqlite", help="SQLite demo DB path.")
    parser.add_argument("--audit-path", default="logs/audit.jsonl", help="Audit log JSONL path.")
    return parser.parse_args()


def is_llm_generation_failure(result: dict[str, object]) -> bool:
    generated = result.get("generated")
    validation = result.get("validation")
    if getattr(generated, "engine", "") == "llm" and getattr(generated, "error", None):
        return True
    issues = getattr(validation, "issues", ())
    return any("LLM SQL 생성 실패" in str(issue) for issue in issues)


def run_question(question: str, role: str, branch_id: int, db_path: str, audit_path: str) -> str:
    ensure_demo_database(db_path)
    agent = build_agent()
    result = agent.invoke(
        {
            "question": question,
            "user_role": role,
            "branch_id": branch_id,
            "db_path": db_path,
            "audit_path": audit_path,
        }
    )
    answer = result["answer"]
    if is_llm_generation_failure(result):
        raise CliExecutionError(answer)
    return answer


def main() -> None:
    args = parse_args()
    if args.demo:
        questions = DEMO_QUESTIONS
    elif args.question is None:
        raise SystemExit("--question 또는 --demo 중 하나를 입력해주세요.")
    else:
        try:
            questions = [normalize_question_text(args.question)]
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    Path(args.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.audit_path).parent.mkdir(parents=True, exist_ok=True)

    exit_code = 0
    for index, question in enumerate(questions, start=1):
        print("=" * 80)
        print(f"Demo {index}: {question}")
        print("=" * 80)
        try:
            print(run_question(question, args.role, args.branch_id, args.db_path, args.audit_path))
        except CliExecutionError as exc:
            exit_code = max(exit_code, exc.exit_code)
            print(exc.answer)
        print()

    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
