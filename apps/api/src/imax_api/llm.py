from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMReply:
    content: str
    model: str


class LLMClient:
    def configured(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY", "").strip()) or self._local_no_auth_allowed()

    @staticmethod
    def _local_no_auth_allowed() -> bool:
        base_url = os.getenv("IM_ONE_LLM_BASE_URL", "").strip().lower()
        allowed = os.getenv("IM_ONE_LLM_ALLOW_LOCAL_NO_AUTH", "").strip().lower() in {"1", "true", "yes"}
        return allowed and ("127.0.0.1" in base_url or "localhost" in base_url)

    def complete(self, messages: list[dict[str, str]], json_mode: bool = False) -> LLMReply:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key and not self._local_no_auth_allowed():
            raise LLMUnavailable("LLM is not configured")

        base_url = os.getenv("IM_ONE_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        model = os.getenv("IM_ONE_LLM_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna"
        try:
            timeout = float(os.getenv("IM_ONE_LLM_TIMEOUT", "10"))
        except ValueError:
            timeout = 10.0

        payload: dict[str, Any] = {"model": model, "messages": messages}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=max(timeout, 1.0)) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise LLMUnavailable("LLM request failed") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailable("LLM response was incomplete") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMUnavailable("LLM response was empty")
        return LLMReply(content=content.strip(), model=model)

    def complete_json(self, messages: list[dict[str, str]]) -> tuple[dict[str, Any], str]:
        reply = self.complete(messages, json_mode=True)
        try:
            payload = json.loads(reply.content)
        except json.JSONDecodeError as exc:
            raise LLMUnavailable("LLM response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise LLMUnavailable("LLM JSON response must be an object")
        return payload, reply.model
