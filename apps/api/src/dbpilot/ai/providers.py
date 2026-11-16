"""Model providers behind one small interface, chosen by ``AI_PROVIDER``.

* ``mock``: the deterministic built-in model (``dbpilot.mockai``): no network, no key, the
  same answer every time. Tests, the demo and the e2e suite use it.
* ``openai``: OpenAI chat completions, also spoken by every compatible gateway (vLLM, LM
  Studio, Groq, Together, the Euron gateways) and by the mock AI service on port 5650.
* ``anthropic``: the Anthropic Messages API.
* ``none``: model calls are off; explanations and diagnoses come from the syntax tree and
  rules alone, and natural language to SQL answers that the assistant is turned off.

The key comes from ``AI_API_KEY`` (Secrets Manager on AWS) and lives only in this object.
Adding a provider means adding a class here and one line in ``from_settings``.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from dbpilot.core.config import Settings

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class ProviderError(Exception):
    def __init__(
        self, message: str, *, code: str = "ai_provider_error", retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


class AiProvider(Protocol):
    name: str
    model: str

    async def complete(self, messages: tuple[Message, ...], *, max_tokens: int) -> Completion: ...


def _system(messages: tuple[Message, ...]) -> str:
    return "\n".join(m.content for m in messages if m.role == "system")


class MockProvider:
    name = "mock"

    def __init__(self, model: str = "dbpilot-mock") -> None:
        self.model = model

    async def complete(self, messages: tuple[Message, ...], *, max_tokens: int) -> Completion:
        from dbpilot.mockai.responder import respond

        started = time.perf_counter()
        turns: list[tuple[str, str]] = [(m.role, m.content) for m in messages if m.role != "system"]
        text = respond(_system(messages), turns)
        chars = sum(len(m.content) for m in messages)
        return Completion(
            text=text,
            model=self.model,
            input_tokens=max(chars // 4, 1),
            output_tokens=max(len(text) // 4, 1),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )


class DisabledProvider:
    name = "none"
    model = ""

    async def complete(self, messages: tuple[Message, ...], *, max_tokens: int) -> Completion:
        raise ProviderError(
            "The AI assistant is turned off on this DBPilot (AI_PROVIDER=none)", code="ai_disabled"
        )


def _classify(status: int) -> tuple[bool, str]:
    if status == 429:
        return True, "ai_rate_limited"
    if status in (408, 409, 425) or status >= 500:
        return True, "ai_unavailable"
    if status in (401, 403):
        return False, "ai_auth"
    return False, "ai_rejected"


class _HttpProvider:
    name = "http"

    def __init__(
        self, http: httpx.AsyncClient, base_url: str, api_key: str, model: str, timeout_s: float
    ) -> None:
        self.http = http
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s

    async def _post(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                resp = await self.http.post(
                    url, json=payload, headers=headers, timeout=self.timeout_s
                )
            except httpx.TimeoutException as exc:
                error = ProviderError(
                    "The model provider did not answer in time", code="ai_timeout", retryable=True
                )
                cause: Exception = exc
            except httpx.HTTPError as exc:
                error = ProviderError(
                    f"Could not reach the model provider: {exc.__class__.__name__}",
                    code="ai_unreachable",
                    retryable=True,
                )
                cause = exc
            else:
                if resp.status_code < 400:
                    body = resp.json()
                    if not isinstance(body, dict):
                        raise ProviderError("The model provider answered with an unexpected shape")
                    return body
                retryable, code = _classify(resp.status_code)
                error = ProviderError(
                    f"The model provider answered {resp.status_code}: {resp.text[:300]}",
                    code=code,
                    retryable=retryable,
                )
                cause = error
            if not error.retryable or attempt >= 2:
                raise error from cause
            attempt += 1
            await asyncio.sleep(min(2.0**attempt, 8.0) * (0.75 + random.random() / 2))  # noqa: S311 jitter


class OpenAIProvider(_HttpProvider):
    name = "openai"

    async def complete(self, messages: tuple[Message, ...], *, max_tokens: int) -> Completion:
        started = time.perf_counter()
        body = await self._post(
            f"{self.base}/chat/completions",
            {
                "model": self.model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "max_tokens": max_tokens,
                "temperature": 0,
            },
            {"authorization": f"Bearer {self.api_key}"} if self.api_key else {},
        )
        choices = body.get("choices") or []
        if not choices:
            raise ProviderError("The model provider returned no answer", retryable=True)
        usage = body.get("usage") or {}
        return Completion(
            text=str((choices[0].get("message") or {}).get("content") or ""),
            model=str(body.get("model") or self.model),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )


class AnthropicProvider(_HttpProvider):
    name = "anthropic"

    async def complete(self, messages: tuple[Message, ...], *, max_tokens: int) -> Completion:
        started = time.perf_counter()
        body = await self._post(
            f"{self.base}/v1/messages",
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": _system(messages),
                "messages": [
                    {"role": m.role, "content": m.content} for m in messages if m.role != "system"
                ],
            },
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
        )
        text = "".join(
            str(b.get("text", "")) for b in body.get("content") or [] if b.get("type") == "text"
        )
        usage = body.get("usage") or {}
        return Completion(
            text=text,
            model=str(body.get("model") or self.model),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )


DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}


def from_settings(settings: Settings, http: httpx.AsyncClient) -> AiProvider:
    kind = settings.ai_provider
    if kind == "mock":
        return MockProvider(settings.ai_model or "dbpilot-mock")
    if kind == "none":
        return DisabledProvider()
    base = settings.ai_base_url or DEFAULT_BASE_URLS[kind]
    cls = OpenAIProvider if kind == "openai" else AnthropicProvider
    return cls(http, base, settings.ai_api_key, settings.ai_model, settings.ai_timeout_s)
