from __future__ import annotations

import json

from im_one_agent.generate_demo_data import export_csv_snapshots, write_gold_snapshots
from im_one_agent.sample_data import REQUIRED_TABLES, connect_database, initialize_demo_database


def test_demo_data_generator_exports_csv_and_gold_snapshots(tmp_path) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    csv_dir = tmp_path / "csv"
    gold_path = tmp_path / "gold_snapshots.json"

    initialize_demo_database(db_path)
    csv_files = export_csv_snapshots(db_path, csv_dir)
    write_gold_snapshots(db_path, gold_path, role="sales_planning")

    assert len(csv_files) == len(REQUIRED_TABLES)
    assert {path.stem for path in csv_files} == set(REQUIRED_TABLES)
    assert "branch_id,branch_name" in (csv_dir / "branches.csv").read_text(encoding="utf-8-sig")
    metadata_csv = (csv_dir / "demo_dataset_metadata.csv").read_text(encoding="utf-8-sig")
    assert "dataset_classification,synthetic_poc" in metadata_csv
    assert "contains_real_customer_data,false" in metadata_csv

    payload = json.loads(gold_path.read_text(encoding="utf-8"))
    assert payload["role"] == "sales_planning"
    assert payload["cases"]
    non_blocked = [case for case in payload["cases"] if not case["should_block"]]
    assert non_blocked
    assert all(case["sql"] for case in non_blocked)
    assert any(case["rows"] for case in non_blocked)


def test_demo_data_generator_neutralizes_formula_like_csv_values(tmp_path) -> None:
    db_path = tmp_path / "im_one_demo.sqlite"
    csv_dir = tmp_path / "csv"

    initialize_demo_database(db_path)
    connection = connect_database(db_path)
    try:
        connection.execute("UPDATE branches SET branch_name = '=demo-branch' WHERE branch_id = 1")
        connection.commit()
    finally:
        connection.close()

    export_csv_snapshots(db_path, csv_dir)

    assert "'=demo-branch" in (csv_dir / "branches.csv").read_text(encoding="utf-8-sig")
