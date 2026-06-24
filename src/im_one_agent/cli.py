from __future__ import annotations

import argparse
from pathlib import Path

from im_one_agent.graph import build_agent
from im_one_agent.sample_data import initialize_demo_database


DEMO_QUESTIONS = [
    "지난 3개월간 지점별 신규 계좌 수 추이는?",
    "이번 달 고위험 상품 가입 건수가 많은 지점은?",
    "최근 30일 VOC 유형별 처리 현황 알려줘.",
    "영업점별 ELS 가입 금액과 민원 건수를 비교해줘.",
    "최근 투자성향 점검 미완료 건수가 많은 지점은?",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the iM One NL2SQL agent POC.")
    parser.add_argument("--question", help="Korean business question to run.")
    parser.add_argument("--demo", action="store_true", help="Run bundled demo questions.")
    parser.add_argument("--role", default="branch_manager", help="Demo user role.")
    parser.add_argument("--db-path", default="data/im_one_demo.sqlite", help="SQLite demo DB path.")
    parser.add_argument("--audit-path", default="logs/audit.jsonl", help="Audit log JSONL path.")
    return parser.parse_args()


def run_question(question: str, role: str, db_path: str, audit_path: str) -> str:
    initialize_demo_database(db_path)
    agent = build_agent()
    result = agent.invoke(
        {
            "question": question,
            "user_role": role,
            "db_path": db_path,
            "audit_path": audit_path,
        }
    )
    return result["answer"]


def main() -> None:
    args = parse_args()
    questions = DEMO_QUESTIONS if args.demo else [args.question]

    if not questions or questions == [None]:
        raise SystemExit("--question 또는 --demo 중 하나를 입력해주세요.")

    Path(args.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.audit_path).parent.mkdir(parents=True, exist_ok=True)

    for index, question in enumerate(questions, start=1):
        print("=" * 80)
        print(f"Demo {index}: {question}")
        print("=" * 80)
        print(run_question(question, args.role, args.db_path, args.audit_path))
        print()


if __name__ == "__main__":
    main()
