from __future__ import annotations

import os

import pytest

import im_one_agent.env as env_module
from im_one_agent.env import load_project_env, parse_env_line
from im_one_agent.sql_generator import configured_llm_model, llm_timeout_seconds

TRACKED_ENV_KEYS = (
    "OPENAI_API_KEY",
    "IM_ONE_ENV_FILE",
    "IM_ONE_LLM_MODEL",
    "IM_ONE_LLM_TIMEOUT",
)


def reset_env_loader() -> None:
    env_module._LOADED_ENV_FILES.clear()


@pytest.fixture(autouse=True)
def restore_tracked_environment():
    previous = {key: os.environ.get(key) for key in TRACKED_ENV_KEYS}
    reset_env_loader()
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    reset_env_loader()


def test_parse_env_line_accepts_export_quotes_and_inline_comment() -> None:
    assert parse_env_line("export IM_ONE_LLM_MODEL='gpt-test'") == ("IM_ONE_LLM_MODEL", "gpt-test")
    assert parse_env_line('IM_ONE_LLM_BASE_URL="http://127.0.0.1:11434/v1"') == (
        "IM_ONE_LLM_BASE_URL",
        "http://127.0.0.1:11434/v1",
    )
    assert parse_env_line("IM_ONE_LLM_TIMEOUT=7 # seconds") == ("IM_ONE_LLM_TIMEOUT", "7")
    assert parse_env_line("# comment") is None


def test_load_project_env_preserves_existing_environment(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=file-secret",
                "IM_ONE_LLM_MODEL=file-model",
                "export IM_ONE_LLM_TIMEOUT=7",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "shell-secret")
    monkeypatch.delenv("IM_ONE_LLM_MODEL", raising=False)
    monkeypatch.delenv("IM_ONE_LLM_TIMEOUT", raising=False)
    reset_env_loader()

    loaded = load_project_env(env_path)

    assert os.environ["OPENAI_API_KEY"] == "shell-secret"
    assert os.environ["IM_ONE_LLM_MODEL"] == "file-model"
    assert os.environ["IM_ONE_LLM_TIMEOUT"] == "7"
    assert loaded == {
        "IM_ONE_LLM_MODEL": "file-model",
        "IM_ONE_LLM_TIMEOUT": "7",
    }


def test_llm_configuration_reads_project_env_file(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "IM_ONE_LLM_MODEL=env-file-model",
                "IM_ONE_LLM_TIMEOUT=6",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("IM_ONE_ENV_FILE", str(env_path))
    monkeypatch.delenv("IM_ONE_LLM_MODEL", raising=False)
    monkeypatch.delenv("IM_ONE_LLM_TIMEOUT", raising=False)
    reset_env_loader()

    load_project_env()

    assert configured_llm_model() == "env-file-model"
    assert llm_timeout_seconds() == 6.0
