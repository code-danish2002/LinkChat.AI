"""Streaming client for OpenAI-compatible chat completion endpoints.

Groq, Cerebras, OpenRouter, Together, Hugging Face and Ollama all speak this
protocol, so one adapter covers the whole pool.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence

import httpx

from app.domain.models import ChatMessage
from app.providers.llm.health import FailureKind
from app.providers.llm.registry import ResolvedProvider


class ProviderError(Exception):
    def __init__(self, message: str, kind: FailureKind, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.status_code = status_code


#: Phrases that mean "you have used your allowance for now", as opposed to
#: "this one request is bigger than I accept". Providers mix the two freely:
#: Groq reports a per-minute input cap as "Request too large ... (ITPM)".
_PER_MINUTE_MARKERS = ("per minute", "per-minute", "tpm", "itpm", "rate limit", "try again in")


def _is_per_minute(body: str) -> bool:
    lowered = body.lower()
    return any(marker in lowered for marker in _PER_MINUTE_MARKERS)


#: Phrases that mean this one request was bigger than the model accepts.
_OVERSIZED_MARKERS = ("too large", "too long", "reduce the length", "exceeds", "maximum context")


def _is_oversized(body: str) -> bool:
    lowered = body.lower()
    return any(marker in lowered for marker in _OVERSIZED_MARKERS)


def classify_status(status_code: int, body: str) -> FailureKind:
    # 402 means the model needs a paid plan, which no amount of retrying fixes.
    if status_code in (401, 402, 403, 404):
        return FailureKind.HARD
    # A per-minute allowance clears on its own, so the model is cooled down.
    # A genuinely oversized payload does not, and the model stays healthy.
    # The status gives the default; the wording overrides it, because providers
    # use both codes for both meanings.
    if status_code == 413:
        return FailureKind.RATE_LIMITED if _is_per_minute(body) else FailureKind.TOO_LARGE
    if status_code == 429:
        if _is_oversized(body) and not _is_per_minute(body):
            return FailureKind.TOO_LARGE
        return FailureKind.RATE_LIMITED
    if status_code == 400 and "model" in body.lower() and (
        "not" in body.lower() or "invalid" in body.lower()
    ):
        return FailureKind.HARD
    return FailureKind.TRANSIENT


class OpenAICompatibleClient:
    def __init__(self, http: httpx.AsyncClient, *, timeout_seconds: float = 60.0):
        self._http = http
        self._timeout = httpx.Timeout(timeout_seconds, connect=10.0)

    async def stream_chat(
        self,
        provider: ResolvedProvider,
        model: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        """Yield content deltas. Raises ProviderError on any protocol or transport failure."""
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **provider.extra_headers,
        }
        url = f"{provider.base_url}/chat/completions"

        try:
            async with self._http.stream("POST", url, json=payload, headers=headers, timeout=self._timeout) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise ProviderError(
                        f"HTTP {response.status_code} from {provider.name}: {_short_error(body)}",
                        classify_status(response.status_code, body),
                        response.status_code,
                    )
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if "error" in event and event["error"]:
                        raise ProviderError(
                            f"{provider.name} stream error: {_short_error(json.dumps(event['error']))}",
                            FailureKind.TRANSIENT,
                        )
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{provider.name} timed out", FailureKind.TRANSIENT) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"{provider.name} transport error: {exc.__class__.__name__}", FailureKind.TRANSIENT
            ) from exc


def _short_error(body: str) -> str:
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            err = parsed.get("error", parsed)
            if isinstance(err, dict):
                return str(err.get("message") or err)[:200]
            return str(err)[:200]
    except (json.JSONDecodeError, TypeError):
        pass
    return " ".join(body.split())[:200]
