from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_database(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_demo_database(db_path: str | Path) -> None:
    connection = connect_database(db_path)
    try:
        create_schema(connection)
        seed_data(connection)
        connection.commit()
    finally:
        connection.close()


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        DROP TABLE IF EXISTS investment_reviews;
        DROP TABLE IF EXISTS voc_cases;
        DROP TABLE IF EXISTS product_sales;
        DROP TABLE IF EXISTS accounts;
        DROP TABLE IF EXISTS branches;

        CREATE TABLE branches (
            branch_id INTEGER PRIMARY KEY,
            branch_name TEXT NOT NULL,
            region TEXT NOT NULL
        );

        CREATE TABLE accounts (
            account_id INTEGER PRIMARY KEY,
            branch_id INTEGER NOT NULL,
            opened_at TEXT NOT NULL,
            channel TEXT NOT NULL,
            customer_segment TEXT NOT NULL,
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
            FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
        );

        CREATE TABLE voc_cases (
            case_id INTEGER PRIMARY KEY,
            branch_id INTEGER NOT NULL,
            case_type TEXT NOT NULL,
            status TEXT NOT NULL,
            received_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
        );

        CREATE TABLE investment_reviews (
            review_id INTEGER PRIMARY KEY,
            branch_id INTEGER NOT NULL,
            review_type TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
        );
        """
    )


def seed_data(connection: sqlite3.Connection) -> None:
    branches = [
        (1, "서울중앙WM센터", "서울"),
        (2, "대구중앙지점", "대구"),
        (3, "부산해운대지점", "부산"),
        (4, "광주상무지점", "광주"),
    ]
    connection.executemany("INSERT INTO branches VALUES (?, ?, ?)", branches)

    accounts = [
        (1, 1, "2026-04-03", "mobile", "retail"),
        (2, 1, "2026-04-10", "branch", "vip"),
        (3, 2, "2026-04-12", "mobile", "retail"),
        (4, 3, "2026-04-18", "branch", "retail"),
        (5, 4, "2026-04-21", "mobile", "retail"),
        (6, 1, "2026-05-02", "mobile", "vip"),
        (7, 1, "2026-05-14", "branch", "retail"),
        (8, 2, "2026-05-16", "mobile", "retail"),
        (9, 2, "2026-05-24", "branch", "corporate"),
        (10, 3, "2026-05-26", "mobile", "retail"),
        (11, 4, "2026-05-28", "branch", "vip"),
        (12, 1, "2026-06-01", "mobile", "retail"),
        (13, 1, "2026-06-08", "branch", "vip"),
        (14, 1, "2026-06-11", "mobile", "retail"),
        (15, 2, "2026-06-03", "mobile", "retail"),
        (16, 2, "2026-06-15", "branch", "retail"),
        (17, 3, "2026-06-06", "mobile", "retail"),
        (18, 3, "2026-06-13", "branch", "corporate"),
        (19, 4, "2026-06-04", "mobile", "retail"),
        (20, 4, "2026-06-18", "branch", "vip"),
    ]
    connection.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)", accounts)

    product_sales = [
        (1, 1, "vip", "ELS", 5, 120000000, "2026-04-05"),
        (2, 1, "retail", "Bond", 2, 30000000, "2026-04-08"),
        (3, 2, "retail", "Fund", 3, 25000000, "2026-04-09"),
        (4, 2, "vip", "ELS", 5, 80000000, "2026-04-22"),
        (5, 3, "retail", "Fund", 4, 15000000, "2026-05-07"),
        (6, 3, "vip", "ELS", 5, 95000000, "2026-05-12"),
        (7, 4, "retail", "Bond", 2, 18000000, "2026-05-15"),
        (8, 4, "vip", "ELS", 4, 70000000, "2026-05-30"),
        (9, 1, "vip", "ELS", 5, 150000000, "2026-06-03"),
        (10, 1, "retail", "Fund", 4, 22000000, "2026-06-05"),
        (11, 2, "retail", "ELS", 4, 40000000, "2026-06-07"),
        (12, 2, "vip", "Fund", 5, 55000000, "2026-06-10"),
        (13, 3, "retail", "Fund", 3, 17000000, "2026-06-14"),
        (14, 3, "vip", "ELS", 5, 110000000, "2026-06-17"),
        (15, 4, "retail", "ELS", 4, 45000000, "2026-06-19"),
        (16, 4, "vip", "Fund", 5, 62000000, "2026-06-20"),
    ]
    connection.executemany("INSERT INTO product_sales VALUES (?, ?, ?, ?, ?, ?, ?)", product_sales)

    voc_cases = [
        (1, 1, "상품설명", "resolved", "2026-05-28", "2026-05-30"),
        (2, 1, "전산/앱", "resolved", "2026-06-01", "2026-06-02"),
        (3, 1, "상품설명", "open", "2026-06-05", None),
        (4, 2, "수수료", "resolved", "2026-06-04", "2026-06-05"),
        (5, 2, "상품설명", "in_progress", "2026-06-09", None),
        (6, 2, "전산/앱", "resolved", "2026-06-11", "2026-06-12"),
        (7, 3, "상품설명", "resolved", "2026-06-02", "2026-06-04"),
        (8, 3, "수수료", "open", "2026-06-12", None),
        (9, 4, "전산/앱", "resolved", "2026-06-03", "2026-06-04"),
        (10, 4, "상품설명", "in_progress", "2026-06-18", None),
    ]
    connection.executemany("INSERT INTO voc_cases VALUES (?, ?, ?, ?, ?, ?)", voc_cases)

    reviews = [
        (1, 1, "투자성향", "completed", "2026-05-01"),
        (2, 1, "적합성", "pending", "2026-06-03"),
        (3, 2, "투자성향", "completed", "2026-05-17"),
        (4, 2, "적합성", "pending", "2026-06-06"),
        (5, 3, "투자성향", "completed", "2026-05-25"),
        (6, 3, "적합성", "completed", "2026-06-08"),
        (7, 4, "투자성향", "pending", "2026-06-10"),
        (8, 4, "적합성", "in_progress", "2026-06-16"),
    ]
    connection.executemany("INSERT INTO investment_reviews VALUES (?, ?, ?, ?, ?)", reviews)
