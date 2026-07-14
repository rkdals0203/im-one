from __future__ import annotations

from typing import Any


def sanitize_csv_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if value and value[0] in {"=", "+", "-", "@", "\t", "\r", "\n"}:
        return f"'{value}"
    return value
