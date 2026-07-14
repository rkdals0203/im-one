from __future__ import annotations

from typing import Any


STRING_LIMITS = {
    "previous_question": 500,
    "previous_sql": 4000,
}
LIST_LIMITS = {
    "previous_columns": (30, 120),
    "previous_metrics": (10, 120),
    "previous_tables": (10, 120),
}
MAX_ROW_SAMPLE_ROWS = 5
MAX_ROW_SAMPLE_COLUMNS = 20
MAX_ROW_SAMPLE_VALUE_LENGTH = 200
MAX_ROW_COUNT = 1_000_000


def sanitize_conversation_context(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    sanitized: dict[str, Any] = {}
    for key, max_length in STRING_LIMITS.items():
        text = sanitized_string(value.get(key), max_length=max_length)
        if text:
            sanitized[key] = text

    for key, (max_items, max_length) in LIST_LIMITS.items():
        items = sanitized_string_list(
            value.get(key),
            max_items=max_items,
            max_length=max_length,
        )
        if items:
            sanitized[key] = items

    row_count = sanitized_non_negative_int(value.get("previous_row_count"), maximum=MAX_ROW_COUNT)
    if row_count is not None:
        sanitized["previous_row_count"] = row_count

    if isinstance(value.get("previous_validation_allowed"), bool):
        sanitized["previous_validation_allowed"] = value["previous_validation_allowed"]

    rows_sample = sanitized_rows_sample(value.get("previous_rows_sample"))
    if rows_sample:
        sanitized["previous_rows_sample"] = rows_sample

    return sanitized


def sanitized_string(value: object, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]


def sanitized_string_list(value: object, max_items: int, max_length: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []

    items: list[str] = []
    for item in value:
        text = sanitized_string(item, max_length=max_length)
        if text:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def sanitized_non_negative_int(value: object, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return min(number, maximum)


def sanitized_rows_sample(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []

    rows: list[dict[str, object]] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        sanitized_row: dict[str, object] = {}
        for raw_key, raw_value in row.items():
            key = sanitized_string(raw_key, max_length=120)
            if not key:
                continue
            sanitized_row[key] = sanitized_scalar(raw_value)
            if len(sanitized_row) >= MAX_ROW_SAMPLE_COLUMNS:
                break
        if sanitized_row:
            rows.append(sanitized_row)
        if len(rows) >= MAX_ROW_SAMPLE_ROWS:
            break
    return rows


def sanitized_scalar(value: object) -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    return sanitized_string(value, max_length=MAX_ROW_SAMPLE_VALUE_LENGTH)
