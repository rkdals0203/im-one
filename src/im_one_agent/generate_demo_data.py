from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from im_one_agent.evaluation import EVALUATION_CASES, build_gold_snapshot, gold_sql_for_case
from im_one_agent.export_utils import sanitize_csv_cell
from im_one_agent.sample_data import REQUIRED_TABLES, connect_database, initialize_demo_database


def export_csv_snapshots(db_path: str | Path, output_dir: str | Path) -> tuple[Path, ...]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    written_files: list[Path] = []

    connection = connect_database(db_path)
    try:
        for table_name in REQUIRED_TABLES:
            cursor = connection.execute(f"SELECT * FROM {table_name}")
            columns = [description[0] for description in cursor.description or []]
            table_path = output_path / f"{table_name}.csv"
            with table_path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.writer(file)
                writer.writerow([sanitize_csv_cell(column) for column in columns])
                for row in cursor.fetchall():
                    writer.writerow([sanitize_csv_cell(row[column]) for column in columns])
            written_files.append(table_path)
    finally:
        connection.close()

    return tuple(written_files)


def build_gold_snapshot_payload(
    db_path: str | Path,
    role: str = "sales_planning",
    branch_id: int = 1,
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for case in EVALUATION_CASES:
        sql = gold_sql_for_case(case, role=role, branch_id=branch_id)
        if case.should_block or not sql:
            cases.append(
                {
                    "case_id": case.case_id,
                    "question": case.question,
                    "intent": case.intent,
                    "should_block": case.should_block,
                    "sql": sql,
                    "columns": [],
                    "rows": [],
                }
            )
            continue

        snapshot = build_gold_snapshot(case, str(db_path), role=role, branch_id=branch_id)
        cases.append(
            {
                "case_id": case.case_id,
                "question": case.question,
                "intent": case.intent,
                "should_block": case.should_block,
                "sql": snapshot.sql,
                "columns": list(snapshot.columns),
                "rows": [dict(row) for row in snapshot.rows],
            }
        )

    return {
        "role": role,
        "branch_id": branch_id,
        "cases": cases,
    }


def write_gold_snapshots(
    db_path: str | Path,
    output_path: str | Path,
    role: str = "sales_planning",
    branch_id: int = 1,
) -> Path:
    payload = build_gold_snapshot_payload(db_path, role=role, branch_id=branch_id)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the fixed-seed iM One synthetic demo mart.")
    parser.add_argument("--db-path", default="data/im_one_demo.sqlite")
    parser.add_argument("--no-reset", action="store_true", help="Keep an existing valid demo database.")
    parser.add_argument("--csv-dir", help="Optional directory for table-level CSV snapshots.")
    parser.add_argument("--gold-output", help="Optional JSON path for evaluation gold result snapshots.")
    parser.add_argument("--role", default="sales_planning", help="Role used for gold result snapshots.")
    parser.add_argument("--branch-id", type=int, default=1, help="Branch scope used for branch_manager gold snapshots.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    initialize_demo_database(args.db_path, reset=not args.no_reset)
    print(f"SQLite demo database: {args.db_path}")

    if args.csv_dir:
        csv_files = export_csv_snapshots(args.db_path, args.csv_dir)
        print(f"CSV snapshots: {args.csv_dir} ({len(csv_files)} files)")

    if args.gold_output:
        write_gold_snapshots(args.db_path, args.gold_output, role=args.role, branch_id=args.branch_id)
        print(f"Gold snapshots: {args.gold_output}")


if __name__ == "__main__":
    main()
