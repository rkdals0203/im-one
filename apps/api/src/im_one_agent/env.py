from __future__ import annotations

import os
import re
from pathlib import Path


ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
_LOADED_ENV_FILES: set[Path] = set()


def configured_env_path() -> Path:
    raw_path = os.getenv("IM_ONE_ENV_FILE")
    if raw_path:
        return Path(raw_path).expanduser().resolve()
    return DEFAULT_ENV_PATH


def load_project_env(path: str | os.PathLike[str] | None = None, override: bool = False) -> dict[str, str]:
    env_path = Path(path).expanduser().resolve() if path is not None else configured_env_path()
    if env_path in _LOADED_ENV_FILES and not override:
        return {}
    if not env_path.exists():
        _LOADED_ENV_FILES.add(env_path)
        return {}

    loaded: dict[str, str] = {}
    for line_number, raw_line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), start=1):
        parsed = parse_env_line(raw_line, line_number=line_number)
        if parsed is None:
            continue
        key, value = parsed
        if override or key not in os.environ:
            os.environ[key] = value
            loaded[key] = value

    _LOADED_ENV_FILES.add(env_path)
    return loaded


def parse_env_line(raw_line: str, line_number: int = 0) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export ") :].strip()
    if "=" not in line:
        raise ValueError(f"Invalid .env line {line_number}: missing '='.")

    raw_key, raw_value = line.split("=", 1)
    key = raw_key.strip()
    if not ENV_KEY_PATTERN.match(key):
        raise ValueError(f"Invalid .env line {line_number}: invalid key '{key}'.")
    return key, strip_env_value(raw_value.strip())


def strip_env_value(value: str) -> str:
    if not value:
        return ""
    if value[0] == value[-1:] and value[0] in {"'", '"'}:
        return value[1:-1]
    if " #" in value:
        return value.split(" #", 1)[0].rstrip()
    return value
