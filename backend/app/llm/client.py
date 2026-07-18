from __future__ import annotations
import json
import re
from typing import Iterator, Protocol
import httpx
from app.config import get_settings


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, schema_hint: str = "") -> dict: ...
    def complete_text(self, system: str, user: str) -> str: ...
    def stream_text(self, system: str, user: str) -> Iterator[str]: ...


class DeepSeekClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 60.0, max_tokens: int = 4096):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    def _post(self, messages: list[dict]) -> str:
        # NOTE: this endpoint (FPT Cloud, DeepSeek-V4-Flash reasoning model) returns
        # content=None when response_format={"type":"json_object"} is sent, so we do NOT
        # use it — the prompt requests JSON and _extract_json robustly parses it. Reasoning
        # models also emit a separate reasoning_content field we intentionally ignore.
        payload = {"model": self.model, "messages": messages,
                   "temperature": 0.2, "max_tokens": self.max_tokens}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"].get("content")
            if content is None:
                raise ValueError(
                    "LLM trả về content rỗng (null). Kiểm tra endpoint/model, "
                    "hoặc tăng max_tokens nếu reasoning model dùng hết token.")
            return content

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
        sys = system + ("\n\nCHỈ trả về một object JSON hợp lệ, không kèm giải thích hay văn bản thừa."
                        + (f" Schema:\n{schema_hint}" if schema_hint else ""))
        return self._extract_json(self._post(
            [{"role": "system", "content": sys}, {"role": "user", "content": user}]))

    def complete_text(self, system: str, user: str) -> str:
        return self._post(
            [{"role": "system", "content": system}, {"role": "user", "content": user}])

    def stream_text(self, system: str, user: str) -> Iterator[str]:
        # SSE stream (stream:true). Yields only `content` tokens — reasoning models
        # also emit `reasoning_content` deltas, which we intentionally skip.
        payload = {"model": self.model,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                   "temperature": 0.2, "max_tokens": self.max_tokens, "stream": True}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout) as c:
            with c.stream("POST", f"{self.base_url}/chat/completions",
                          json=payload, headers=headers) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[len("data: "):].strip()
                    if data == "[DONE]":
                        break
                    choices = json.loads(data).get("choices") or []
                    if not choices:
                        continue
                    token = (choices[0].get("delta") or {}).get("content")
                    if token:
                        yield token


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

    def stream_text(self, system: str, user: str) -> Iterator[str]:
        # Yields the canned text in small slices to exercise line-buffering in callers.
        text = self.complete_text(system, user)
        for i in range(0, len(text), 10):
            yield text[i:i + 10]


def get_llm() -> LLMClient:
    s = get_settings()
    return DeepSeekClient(s.llm_base_url, s.llm_api_key, s.llm_model)
