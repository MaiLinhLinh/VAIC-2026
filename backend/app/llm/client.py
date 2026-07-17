from __future__ import annotations
import json
import re
from typing import Protocol
import httpx
from app.config import get_settings


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, schema_hint: str = "") -> dict: ...
    def complete_text(self, system: str, user: str) -> str: ...


class DeepSeekClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _post(self, messages: list[dict], json_mode: bool) -> str:
        payload = {"model": self.model, "messages": messages, "temperature": 0.2}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    @staticmethod
    def _extract_json(raw: str) -> dict:
        raw = raw.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if fence:
            raw = fence.group(1)
        else:
            brace = re.search(r"\{.*\}", raw, re.DOTALL)
            if brace:
                raw = brace.group(0)
        return json.loads(raw)

    def complete_json(self, system: str, user: str, schema_hint: str = "") -> dict:
        sys = system + ("\n\nTrả về JSON hợp lệ." + (f" Schema:\n{schema_hint}" if schema_hint else ""))
        return self._extract_json(self._post(
            [{"role": "system", "content": sys}, {"role": "user", "content": user}], json_mode=True))

    def complete_text(self, system: str, user: str) -> str:
        return self._post(
            [{"role": "system", "content": system}, {"role": "user", "content": user}], json_mode=False)


class FakeLLM:
    def __init__(self, json_responses: list[dict] | None = None, text_responses: list[str] | None = None):
        self._json = list(json_responses or [])
        self._text = list(text_responses or [])
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str, schema_hint: str = "") -> dict:
        self.calls.append((system, user))
        return self._json.pop(0) if self._json else {}

    def complete_text(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self._text.pop(0) if self._text else ""


def get_llm() -> LLMClient:
    s = get_settings()
    return DeepSeekClient(s.llm_base_url, s.llm_api_key, s.llm_model)
