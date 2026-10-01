"""OpenAI-compatible chat-completions discovery adapter.

Shared base for providers that expose an OpenAI-style ``/chat/completions``
endpoint (Groq, Mistral, OpenRouter). Subclasses only set the endpoint and a
static candidate list (or rely on the OpenRouter catalog scan for dynamic
zero-price discovery).

Adapted from the digest_agent ``OpenAICompatAdapter``, async + httpx.
"""

from __future__ import annotations

import logging
import time

import httpx

from knowledge_engine.core.discovery import (
    CompletionError,
    FreeModel,
    LLMDiscoveryPort,
    ModelId,
    TestResult,
)
from knowledge_engine.infrastructure.discovery.text_utils import strip_reasoning_prefix

logger = logging.getLogger(__name__)

TEST_PROMPT = (
    "You are a helpful assistant. Respond with a brief, well-formed sentence "
    "about the topic. Do not repeat the prompt, do not include error messages, "
    "and make at least 5 words."
)
_CACHE_TTL_SECONDS = 30 * 60  # 30 minutes


class OpenAICompatDiscovery(LLMDiscoveryPort):
    """OpenAI-compatible chat completions provider with cached candidate list."""

    endpoint: str = ""
    candidates: list[FreeModel] = []

    def __init__(self, api_key: str, *, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self._cache: list[FreeModel] | None = None
        self._cache_time = 0.0

    # -- live calls ----------------------------------------------------------
    async def _chat(
        self, client: httpx.AsyncClient, model_id: ModelId, messages: list[dict], *, timeout: float
    ) -> str:
        resp = await client.post(
            f"{self.endpoint}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": model_id, "messages": messages},
            timeout=timeout,
        )
        if resp.status_code == 429:
            raise CompletionError("rate_limited")
        resp.raise_for_status()
        body = resp.json()
        choices = body.get("choices") or []
        if not choices:
            raise CompletionError("no_choices")
        return choices[0].get("message", {}).get("content", "")

    async def _with_client(self, fn):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await fn(client)

    # -- LLMDiscoveryPort ----------------------------------------------------
    async def list_free_models(self) -> list[FreeModel]:
        if self._cache is None or time.monotonic() - self._cache_time > _CACHE_TTL_SECONDS:
            self._cache = list(self.candidates)
            self._cache_time = time.monotonic()
        return list(self._cache)

    async def test(self, model_id: ModelId) -> TestResult:
        try:
            content = await self._with_client(
                lambda c: self._chat(c, model_id, [{"role": "user", "content": TEST_PROMPT}], timeout=30.0)
            )
            content = strip_reasoning_prefix(content).strip()
            if not content:
                return TestResult(model_id, False, "empty_response")
            if len(content) < 10:
                return TestResult(model_id, False, "response_too_short")
            if content.lower().startswith("error"):
                return TestResult(model_id, False, "error_masked")
            return TestResult(model_id, True, "ok")
        except CompletionError as exc:
            reason = "rate_limited" if "rate" in str(exc) or "429" in str(exc) else "no_choices"
            return TestResult(model_id, False, reason)
        except httpx.TimeoutException:
            return TestResult(model_id, False, "timeout")
        except Exception as exc:  # noqa: BLE001 - any provider error = fail
            reason = "rate_limited" if ("429" in str(exc) or "rate limit" in str(exc).lower()) else "error"
            return TestResult(model_id, False, reason)

    async def complete(self, model_id: ModelId, prompt: str) -> str:
        try:
            content = await self._with_client(
                lambda c: self._chat(c, model_id, [{"role": "user", "content": prompt}], timeout=60.0)
            )
            return strip_reasoning_prefix(content)
        except httpx.TimeoutException as exc:
            raise CompletionError("Timeout during completion") from exc
        except CompletionError:
            raise
        except Exception as exc:
            raise CompletionError(f"Completion failed: {exc}") from exc


__all__ = ["OpenAICompatDiscovery", "_CACHE_TTL_SECONDS"]
