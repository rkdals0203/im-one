from __future__ import annotations

import os
import random
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from im_one_agent.domain import AS_OF_DATE, TABLES

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None


@dataclass(frozen=True)
class BranchProfile:
    branch_id: int
    branch_name: str
    region: str
    branch_type: str
    opened_date: str
    account_base: int
    account_growth: float
    mobile_bias: float
    els_bias: float
    voc_bias: float


BRANCH_PROFILES: tuple[BranchProfile, ...] = (
    BranchProfile(1, "합성서울WM-01", "서울", "WM센터", "2016-03-02", 18, 1.3, 0.48, 0.70, 1.20),
    BranchProfile(2, "합성대구BR-02", "대구", "일반지점", "2018-05-14", 13, 0.8, 0.54, 0.42, 1.05),
    BranchProfile(3, "합성부산BR-03", "부산", "일반지점", "2017-07-03", 12, 0.6, 0.44, 0.50, 1.15),
    BranchProfile(4, "합성광주BR-04", "광주", "일반지점", "2019-04-22", 11, 0.7, 0.57, 0.36, 0.95),
    BranchProfile(5, "합성경기WM-05", "경기", "WM센터", "2020-02-17", 16, 1.1, 0.62, 0.64, 1.10),
    BranchProfile(6, "합성인천DL-06", "인천", "디지털라운지", "2021-09-06", 14, 1.4, 0.73, 0.38, 1.25),
    BranchProfile(7, "합성대전BR-07", "대전", "일반지점", "2015-11-09", 10, 0.4, 0.49, 0.34, 0.88),
    BranchProfile(8, "합성울산BR-08", "울산", "일반지점", "2018-08-20", 9, 0.5, 0.47, 0.40, 0.92),
    BranchProfile(9, "합성서울WM-09", "서울", "WM센터", "2014-01-13", 15, 0.9, 0.41, 0.66, 1.05),
    BranchProfile(10, "합성제주DL-10", "제주", "디지털라운지", "2022-06-27", 8, 0.9, 0.68, 0.30, 0.86),
)

MONTHS: tuple[tuple[int, int], ...] = (
    (2025, 7),
    (2025, 8),
    (2025, 9),
    (2025, 10),
    (2025, 11),
    (2025, 12),
    (2026, 1),
    (2026, 2),
    (2026, 3),
    (2026, 4),
    (2026, 5),
    (2026, 6),
)

CHANNELS = ("mobile", "branch", "web", "call_center")
CUSTOMER_SEGMENTS = ("retail", "vip", "corporate")
AGE_BANDS = ("20s", "30s", "40s", "50s", "60s+")
RISK_PROFILE_BANDS = ("conservative", "balanced", "aggressive", "unknown")
PRODUCT_TYPES = ("ELS", "Fund", "Bond", "RP", "ISA", "Pension")
VOC_TYPES = ("상품설명", "전산/앱", "수수료", "주문/체결", "계좌개설", "기타")
VOC_STATUSES = ("open", "in_progress", "resolved", "escalated")
REVIEW_TYPES = ("투자성향", "적합성", "설명의무", "고령투자자")
REVIEW_STATUSES = ("pending", "in_progress", "completed", "overdue")
SYNTHETIC_DATASET_NOTICE_KO = "완전 합성 POC 데이터이며 실제 고객, 계좌, 직원, 지점 실적을 포함하지 않습니다."
SYNTHETIC_BRANCH_NAME_MARKER = "합성"
REQUIRED_DATASET_METADATA: dict[str, str] = {
    "dataset_classification": "synthetic_poc",
    "source": "fixed_seed_generator",
    "as_of_date": AS_OF_DATE,
    "contains_real_customer_data": "false",
    "contains_real_account_numbers": "false",
    "contains_real_employee_data": "false",
    "contains_real_branch_performance": "false",
    "notice_ko": SYNTHETIC_DATASET_NOTICE_KO,
}
REQUIRED_TABLES = (
    "demo_dataset_metadata",
    "branches",
    "accounts",
    "product_sales",
    "voc_cases",
    "investment_reviews",
    "branch_targets",
    "query_audit_log",
)
REQUIRED_AUDIT_TRIGGERS = (
    "query_audit_log_no_update",
    "query_audit_log_no_delete",
)
_DATABASE_INIT_THREAD_LOCK = threading.Lock()


def is_read_only_database() -> bool:
    return os.getenv("IM_ONE_DB_READONLY", "").strip().lower() in {"1", "true", "yes"}


def connect_database(db_path: str | Path, read_only: bool | None = None) -> sqlite3.Connection:
    if read_only is None:
        read_only = is_read_only_database()

    path = Path(db_path)
    if str(path) == ":memory:":
        connection = sqlite3.connect(path, timeout=30)
    elif read_only:
        uri = f"file:{quote(str(path.resolve()))}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=30)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_demo_database(db_path: str | Path, reset: bool = True) -> None:
    with database_initialization_lock(db_path):
        connection = connect_database(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            if not reset and database_has_required_schema(connection):
                connection.commit()
                return
            create_schema(connection)
            seed_data(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


@contextmanager
def database_initialization_lock(db_path: str | Path):
    with _DATABASE_INIT_THREAD_LOCK:
        if str(db_path) == ":memory:" or fcntl is None:
            yield
            return

        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".init.lock")
        with lock_path.open("w") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def ensure_demo_database(db_path: str | Path) -> None:
    if str(db_path) == ":memory:":
        initialize_demo_database(db_path)
        return

    path = Path(db_path)
    if not path.exists():
        if is_read_only_database():
            raise FileNotFoundError(f"Read-only database does not exist: {path}")
        initialize_demo_database(db_path, reset=False)
        return

    connection = connect_database(db_path)
    try:
        schema_ready = database_has_required_schema(connection)
    except sqlite3.Error:
        connection.close()
        if is_read_only_database():
            raise
        initialize_demo_database(db_path, reset=False)
    else:
        connection.close()
        if not schema_ready:
            if is_read_only_database():
                raise RuntimeError(f"Read-only database is missing required demo schema/data: {path}")
            initialize_demo_database(db_path, reset=False)


def database_has_required_schema(connection: sqlite3.Connection) -> bool:
    existing_tables = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if not set(REQUIRED_TABLES).issubset(existing_tables):
        return False
    if not database_has_required_columns(connection):
        return False
    if not database_has_required_audit_triggers(connection):
        return False
    if not database_has_required_dataset_metadata(connection):
        return False
    if not database_has_required_synthetic_branch_names(connection):
        return False

    required_counts = {
        "branches": (8, 12),
        "accounts": (1000, 5000),
        "product_sales": (1000, 3000),
        "voc_cases": (300, 800),
        "investment_reviews": (500, 1500),
        "branch_targets": (len(BRANCH_PROFILES) * len(MONTHS) * 3, len(BRANCH_PROFILES) * len(MONTHS) * 3),
    }
    for table_name, (minimum, maximum) in required_counts.items():
        row_count = connection.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()["count"]
        if row_count < minimum or row_count > maximum:
            return False
    return True


def database_has_required_dataset_metadata(connection: sqlite3.Connection) -> bool:
    try:
        rows = connection.execute("SELECT metadata_key, metadata_value FROM demo_dataset_metadata").fetchall()
    except sqlite3.Error:
        return False

    metadata = {str(row["metadata_key"]): str(row["metadata_value"]) for row in rows}
    return all(metadata.get(key) == value for key, value in REQUIRED_DATASET_METADATA.items())


def database_has_required_synthetic_branch_names(connection: sqlite3.Connection) -> bool:
    try:
        rows = connection.execute("SELECT branch_name FROM branches").fetchall()
    except sqlite3.Error:
        return False
    return bool(rows) and all(SYNTHETIC_BRANCH_NAME_MARKER in str(row["branch_name"]) for row in rows)


def database_has_required_audit_triggers(connection: sqlite3.Connection) -> bool:
    existing_triggers = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
    }
    return set(REQUIRED_AUDIT_TRIGGERS).issubset(existing_triggers)


def database_has_required_columns(connection: sqlite3.Connection) -> bool:
    for table_name in REQUIRED_TABLES:
        expected_columns = set(TABLES[table_name].columns)
        existing_columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if not expected_columns.issubset(existing_columns):
            return False
    return True


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        DROP TABLE IF EXISTS query_audit_log;
        DROP TABLE IF EXISTS branch_targets;
        DROP TABLE IF EXISTS investment_reviews;
        DROP TABLE IF EXISTS voc_cases;
        DROP TABLE IF EXISTS product_sales;
        DROP TABLE IF EXISTS accounts;
        DROP TABLE IF EXISTS branches;
        DROP TABLE IF EXISTS demo_dataset_metadata;

        CREATE TABLE demo_dataset_metadata (
            metadata_key TEXT PRIMARY KEY,
            metadata_value TEXT NOT NULL
        );

        CREATE TABLE branches (
            branch_id INTEGER PRIMARY KEY,
            branch_name TEXT NOT NULL,
            region TEXT NOT NULL,
            branch_type TEXT NOT NULL,
            opened_date TEXT NOT NULL,
            active_flag INTEGER NOT NULL
        );

        CREATE TABLE accounts (
            account_id INTEGER PRIMARY KEY,
            branch_id INTEGER NOT NULL,
            opened_at TEXT NOT NULL,
            channel TEXT NOT NULL,
            customer_segment TEXT NOT NULL,
            age_band TEXT NOT NULL,
            risk_profile_band TEXT NOT NULL,
            is_first_account INTEGER NOT NULL,
            FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
        );

        CREATE TABLE product_sales (
            sale_id INTEGER PRIMARY KEY,
            branch_id INTEGER NOT NULL,
            customer_segment TEXT NOT NULL,
            product_type TEXT NOT NULL,
            risk_grade INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            sold_at TEXT NOT NULL,
            channel TEXT NOT NULL,
            suitability_checked INTEGER NOT NULL,
            cooling_off_eligible INTEGER NOT NULL,
            FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
        );

        CREATE TABLE voc_cases (
            case_id INTEGER PRIMARY KEY,
            branch_id INTEGER NOT NULL,
            case_type TEXT NOT NULL,
            status TEXT NOT NULL,
            received_at TEXT NOT NULL,
            resolved_at TEXT,
            severity TEXT NOT NULL,
            product_type TEXT,
            sla_due_at TEXT NOT NULL,
            FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
        );

        CREATE TABLE investment_reviews (
            review_id INTEGER PRIMARY KEY,
            branch_id INTEGER NOT NULL,
            review_type TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            due_at TEXT NOT NULL,
            product_type TEXT,
            risk_grade INTEGER,
            FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
        );

        CREATE TABLE branch_targets (
            target_id INTEGER PRIMARY KEY,
            branch_id INTEGER NOT NULL,
            target_month TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            target_value INTEGER NOT NULL,
            FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
        );

        CREATE TABLE query_audit_log (
            audit_id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            user_id TEXT NOT NULL,
            auth_mode TEXT NOT NULL,
            user_role TEXT NOT NULL,
            original_question TEXT NOT NULL,
            question TEXT NOT NULL,
            selected_semantic_metrics TEXT NOT NULL,
            semantic_metrics TEXT NOT NULL,
            generated_sql TEXT NOT NULL,
            llm_generated_sql TEXT NOT NULL,
            policy_applied_sql TEXT NOT NULL,
            validated_sql TEXT NOT NULL,
            sql_policy_transformations TEXT NOT NULL DEFAULT '',
            generation_engine TEXT NOT NULL,
            llm_model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            validation_status TEXT NOT NULL,
            execution_status TEXT NOT NULL,
            validation_issues TEXT NOT NULL DEFAULT '',
            referenced_tables TEXT NOT NULL DEFAULT '',
            row_count INTEGER NOT NULL,
            pre_execution_row_count INTEGER,
            pre_execution_row_count_status TEXT NOT NULL DEFAULT '',
            pre_execution_check_ms REAL,
            query_plan_summary TEXT NOT NULL DEFAULT '',
            execution_ms REAL,
            blocked_reason TEXT
        );

        CREATE TRIGGER query_audit_log_no_update
        BEFORE UPDATE ON query_audit_log
        BEGIN
            SELECT RAISE(ABORT, 'query_audit_log is append-only');
        END;

        CREATE TRIGGER query_audit_log_no_delete
        BEFORE DELETE ON query_audit_log
        BEGIN
            SELECT RAISE(ABORT, 'query_audit_log is append-only');
        END;
        """
    )


def seed_data(connection: sqlite3.Connection, seed: int = 20260624) -> None:
    rng = random.Random(seed)
    seed_dataset_metadata(connection)
    seed_branches(connection)
    seed_accounts(connection, rng)
    seed_product_sales(connection, rng)
    seed_voc_cases(connection, rng)
    seed_investment_reviews(connection, rng)
    seed_branch_targets(connection, rng)


def seed_dataset_metadata(connection: sqlite3.Connection) -> None:
    rows = sorted(REQUIRED_DATASET_METADATA.items())
    connection.executemany("INSERT INTO demo_dataset_metadata VALUES (?, ?)", rows)


def seed_branches(connection: sqlite3.Connection) -> None:
    rows = [
        (
            profile.branch_id,
            profile.branch_name,
            profile.region,
            profile.branch_type,
            profile.opened_date,
            1,
        )
        for profile in BRANCH_PROFILES
    ]
    connection.executemany("INSERT INTO branches VALUES (?, ?, ?, ?, ?, ?)", rows)


def seed_accounts(connection: sqlite3.Connection, rng: random.Random) -> None:
    rows: list[tuple[object, ...]] = []
    account_id = 1
    for month_index, (year, month) in enumerate(MONTHS):
        for profile in BRANCH_PROFILES:
            month_cap = 24 if (year, month) == (2026, 6) else 28
            count = int(profile.account_base + profile.account_growth * month_index + rng.randint(-2, 4))
            count = max(6, count)
            for _ in range(count):
                opened = date(year, month, rng.randint(1, month_cap))
                channel = weighted_choice(
                    rng,
                    (
                        ("mobile", profile.mobile_bias),
                        ("branch", 0.24),
                        ("web", 0.18),
                        ("call_center", 0.10),
                    ),
                )
                segment = weighted_choice(rng, (("retail", 0.70), ("vip", 0.22), ("corporate", 0.08)))
                age_band = weighted_choice(
                    rng,
                    (("20s", 0.16), ("30s", 0.25), ("40s", 0.25), ("50s", 0.22), ("60s+", 0.12)),
                )
                risk_profile = weighted_choice(
                    rng,
                    (("conservative", 0.26), ("balanced", 0.42), ("aggressive", 0.24), ("unknown", 0.08)),
                )
                rows.append(
                    (
                        account_id,
                        profile.branch_id,
                        opened.isoformat(),
                        channel,
                        segment,
                        age_band,
                        risk_profile,
                        1 if rng.random() < 0.72 else 0,
                    )
                )
                account_id += 1

    connection.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)


def seed_product_sales(connection: sqlite3.Connection, rng: random.Random) -> None:
    rows: list[tuple[object, ...]] = []
    sale_id = 1
    for month_index, (year, month) in enumerate(MONTHS):
        for profile in BRANCH_PROFILES:
            month_cap = 24 if (year, month) == (2026, 6) else 28
            count = max(7, int(9 + profile.els_bias * 8 + month_index * 0.35 + rng.randint(-2, 4)))
            for _ in range(count):
                product_type = weighted_choice(
                    rng,
                    (
                        ("ELS", profile.els_bias),
                        ("Fund", 0.34),
                        ("Bond", 0.22),
                        ("RP", 0.18),
                        ("ISA", 0.16),
                        ("Pension", 0.13),
                    ),
                )
                risk_grade = risk_grade_for(product_type, rng)
                sold = date(year, month, rng.randint(1, month_cap))
                segment = weighted_choice(rng, (("retail", 0.62), ("vip", 0.30), ("corporate", 0.08)))
                channel = weighted_choice(
                    rng,
                    (("mobile", profile.mobile_bias), ("branch", 0.30), ("web", 0.16), ("call_center", 0.08)),
                )
                rows.append(
                    (
                        sale_id,
                        profile.branch_id,
                        segment,
                        product_type,
                        risk_grade,
                        amount_for(product_type, segment, rng),
                        sold.isoformat(),
                        channel,
                        1 if rng.random() > 0.05 else 0,
                        1 if risk_grade >= 4 and rng.random() < 0.45 else 0,
                    )
                )
                sale_id += 1

    connection.executemany("INSERT INTO product_sales VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def seed_voc_cases(connection: sqlite3.Connection, rng: random.Random) -> None:
    rows: list[tuple[object, ...]] = []
    case_id = 1
    for month_index, (year, month) in enumerate(MONTHS):
        for profile in BRANCH_PROFILES:
            month_cap = 24 if (year, month) == (2026, 6) else 28
            base = 2.2 + profile.voc_bias * 2.5
            june_campaign_lift = 1.5 if (year, month) in {(2026, 5), (2026, 6)} and profile.els_bias > 0.55 else 0
            count = max(1, int(base + june_campaign_lift + rng.randint(0, 3)))
            for _ in range(count):
                received = date(year, month, rng.randint(1, month_cap))
                case_type = weighted_choice(
                    rng,
                    (
                        ("상품설명", 0.24 + profile.els_bias * 0.16),
                        ("전산/앱", 0.16 + profile.mobile_bias * 0.20),
                        ("수수료", 0.16),
                        ("주문/체결", 0.14),
                        ("계좌개설", 0.15),
                        ("기타", 0.08),
                    ),
                )
                status = weighted_choice(
                    rng,
                    (("resolved", 0.58), ("in_progress", 0.20), ("open", 0.16), ("escalated", 0.06)),
                )
                severity = weighted_choice(rng, (("low", 0.52), ("medium", 0.36), ("high", 0.12)))
                resolved_at = None
                if status == "resolved":
                    resolved_at = (received + timedelta(days=rng.randint(1, 5))).isoformat()
                product_type = "ELS" if case_type == "상품설명" and rng.random() < 0.62 else None
                rows.append(
                    (
                        case_id,
                        profile.branch_id,
                        case_type,
                        status,
                        received.isoformat(),
                        resolved_at,
                        severity,
                        product_type,
                        (received + timedelta(days=5)).isoformat(),
                    )
                )
                case_id += 1

    connection.executemany("INSERT INTO voc_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def seed_investment_reviews(connection: sqlite3.Connection, rng: random.Random) -> None:
    rows: list[tuple[object, ...]] = []
    review_id = 1
    for month_index, (year, month) in enumerate(MONTHS):
        for profile in BRANCH_PROFILES:
            month_cap = 24 if (year, month) == (2026, 6) else 28
            count = max(4, int(5 + profile.els_bias * 5 + month_index * 0.2 + rng.randint(0, 3)))
            for _ in range(count):
                created = date(year, month, rng.randint(1, month_cap))
                review_type = weighted_choice(
                    rng,
                    (("투자성향", 0.32), ("적합성", 0.30), ("설명의무", 0.25), ("고령투자자", 0.13)),
                )
                risk_grade = rng.choice((3, 4, 5)) if review_type in {"적합성", "설명의무"} else rng.choice((1, 2, 3, 4))
                status = weighted_choice(
                    rng,
                    (("completed", 0.66), ("pending", 0.15), ("in_progress", 0.13), ("overdue", 0.06)),
                )
                if month == 6 and profile.els_bias > 0.60 and rng.random() < 0.18:
                    status = weighted_choice(rng, (("pending", 0.45), ("overdue", 0.35), ("in_progress", 0.20)))
                rows.append(
                    (
                        review_id,
                        profile.branch_id,
                        review_type,
                        status,
                        created.isoformat(),
                        (created + timedelta(days=14)).isoformat(),
                        "ELS" if risk_grade >= 4 and rng.random() < 0.58 else None,
                        risk_grade,
                    )
                )
                review_id += 1

    connection.executemany("INSERT INTO investment_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)


def seed_branch_targets(connection: sqlite3.Connection, rng: random.Random) -> None:
    rows: list[tuple[object, ...]] = []
    target_id = 1
    for month_index, (year, month) in enumerate(MONTHS):
        for profile in BRANCH_PROFILES:
            target_month = f"{year:04d}-{month:02d}"
            targets = {
                "new_accounts": int(profile.account_base + profile.account_growth * month_index + 5),
                "els_amount": int((60_000_000 + profile.els_bias * 90_000_000) * (1 + month_index / 40)),
                "voc_resolution_rate": 82 + rng.randint(-3, 5),
            }
            for metric_name, target_value in targets.items():
                rows.append((target_id, profile.branch_id, target_month, metric_name, target_value))
                target_id += 1

    connection.executemany("INSERT INTO branch_targets VALUES (?, ?, ?, ?, ?)", rows)


def weighted_choice(rng: random.Random, weighted_items: tuple[tuple[str, float], ...]) -> str:
    total = sum(weight for _, weight in weighted_items)
    point = rng.random() * total
    cumulative = 0.0
    for item, weight in weighted_items:
        cumulative += weight
        if point <= cumulative:
            return item
    return weighted_items[-1][0]


def risk_grade_for(product_type: str, rng: random.Random) -> int:
    if product_type == "ELS":
        return weighted_int(rng, ((4, 0.34), (5, 0.56), (3, 0.10)))
    if product_type == "Fund":
        return weighted_int(rng, ((2, 0.14), (3, 0.34), (4, 0.34), (5, 0.18)))
    if product_type == "Bond":
        return weighted_int(rng, ((1, 0.26), (2, 0.52), (3, 0.22)))
    if product_type == "RP":
        return weighted_int(rng, ((1, 0.58), (2, 0.32), (3, 0.10)))
    if product_type == "ISA":
        return weighted_int(rng, ((2, 0.28), (3, 0.48), (4, 0.24)))
    return weighted_int(rng, ((2, 0.36), (3, 0.46), (4, 0.18)))


def weighted_int(rng: random.Random, weighted_items: tuple[tuple[int, float], ...]) -> int:
    return int(weighted_choice(rng, tuple((str(item), weight) for item, weight in weighted_items)))


def amount_for(product_type: str, segment: str, rng: random.Random) -> int:
    segment_multiplier = {"retail": 1.0, "vip": 2.4, "corporate": 3.1}[segment]
    base_amount = {
        "ELS": 38_000_000,
        "Fund": 18_000_000,
        "Bond": 25_000_000,
        "RP": 12_000_000,
        "ISA": 9_000_000,
        "Pension": 11_000_000,
    }[product_type]
    noise = rng.uniform(0.65, 1.55)
    rounded = int(base_amount * segment_multiplier * noise / 1_000_000) * 1_000_000
    return max(1_000_000, rounded)


def as_of_date() -> date:
    year, month, day = (int(part) for part in AS_OF_DATE.split("-"))
    return date(year, month, day)
