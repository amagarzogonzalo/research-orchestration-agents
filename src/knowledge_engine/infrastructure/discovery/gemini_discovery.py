"""Gemini discovery adapter (Google AI Studio, REST, no SDK).

Adapted from digest_agent's ``GeminiAdapter``: talks to the ``generateContent``
endpoint directly via httpx so the ``providers`` extra (which pulls the Google
SDK) stays optional. Free candidate models are ordered best-first.
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

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_CACHE_TTL_SECONDS = 30 * 60


class GeminiDiscovery(LLMDiscoveryPort):
    """Model discovery + completion for Google Gemini (free tier)."""

    def __init__(
        self,
        api_key: str,
        candidate_models: list[ModelId] | None = None,
        *,
        timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.candidates = candidate_models or ["gemini-flash-lite-latest", "gemini-2.0-flash"]
        self._cache: list[FreeModel] | None = None
        self._cache_time = 0.0

    async def _generate(self, model_id: ModelId, prompt: str, max_tokens: int, temperature: float = 0.4) -> str:
        if not self.api_key:
            raise CompletionError("GEMINI_API_KEY is not set")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{_BASE}/{model_id}:generateContent",
                params={"key": self.api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
                },
            )
        if resp.status_code == 429:
            raise CompletionError("quota_exceeded")
        if resp.status_code != 200:
            raise CompletionError(f"Gemini API error {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            reason = body.get("promptFeedback", {}).get("blockReason", "empty")
            raise CompletionError(f"Gemini returned no content ({reason})") from None
        return text

    async def list_free_models(self) -> list[FreeModel]:
        if self._cache is None or time.monotonic() - self._cache_time > _CACHE_TTL_SECONDS:
            self._cache = [FreeModel(model_id=m, provider="Gemini", context_length=128_000) for m in self.candidates]
            self._cache_time = time.monotonic()
        return list(self._cache)

    async def test(self, model_id: ModelId) -> TestResult:
        try:
            content = strip_reasoning_prefix(
                await self._generate(model_id, "Reply with a short, well-formed sentence (at least 5 words).", 40, 0.0)
            ).strip()
            if not content:
                return TestResult(model_id, False, "empty_response")
            if len(content) < 10:
                return TestResult(model_id, False, "response_too_short")
            return TestResult(model_id, True, "ok")
        except CompletionError as exc:
            return TestResult(model_id, False, "rate_limited" if "quota" in str(exc) else "error")
        except httpx.TimeoutException:
            return TestResult(model_id, False, "timeout")
        except Exception:  # noqa: BLE001
            return TestResult(model_id, False, "error")

    async def complete(self, model_id: ModelId, prompt: str) -> str:
        try:
            return strip_reasoning_prefix(await self._generate(model_id, prompt, 3000, 0.4))
        except CompletionError:
            raise
        except httpx.TimeoutException as exc:
            raise CompletionError("Gemini timeout") from exc


__all__ = ["GeminiDiscovery"]
