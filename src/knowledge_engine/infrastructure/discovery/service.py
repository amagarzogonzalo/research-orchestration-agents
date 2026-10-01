"""Free-LLM discovery service — lean, deterministic, repeat-safe.

Key design rules (per the project directive):

* **No "more powerful" decisions** — model selection is pure logic over cheap
  health-check results (:class:`ModelSelectionPolicy`); no LLM is used to judge
  a candidate, so discovery costs only minimal tokens.
* **Repeat-safe** — the free-model catalog is cached (30 min) and a
  *known-good* model is remembered with a TTL, so calling ``discover()`` or
  ``complete()`` many times reuses one working model instead of re-testing the
  whole pool each call (no quota burn, no rate-limit saturation).
* **Cooldowns** — a provider that just rate-limited or timed out is skipped for
  a window before being re-tested, so bursts degrade gracefully.

Providers tried in capability order: Groq → Mistral → OpenRouter catalog →
Gemini (only those with a key are wired).
"""

from __future__ import annotations

import logging
import time

from knowledge_engine.core.discovery import (
    FreeModel,
    LLMDiscoveryPort,
    ModelId,
    TestResult,
)
from knowledge_engine.infrastructure.discovery.fallback_discovery import FallbackDiscovery
from knowledge_engine.infrastructure.discovery.gemini_discovery import GeminiDiscovery
from knowledge_engine.infrastructure.discovery.model_selection import (
    ACTION_BUDGET,
    ACTION_CONTINUE,
    ACTION_EXHAUSTED,
    ACTION_PASS,
    ModelSelectionPolicy,
    SelectionState,
)
from knowledge_engine.infrastructure.discovery.openrouter_catalog import OpenRouterCatalog
from knowledge_engine.infrastructure.discovery.providers import GroqDiscovery, MistralDiscovery

logger = logging.getLogger(__name__)

#: How long a "known-good" model is trusted without re-testing.
KNOWN_GOOD_TTL_SECONDS = 5 * 60
#: How long to skip a provider/model that just rate-limited or timed out.
FAILURE_COOLDOWN_SECONDS = 60
#: Catalog TTL (providers also cache internally; this is the fallback).
CATALOG_TTL_SECONDS = 30 * 60


class ModelDiscoverer:
    """Discover + validate + remember a working free model (lean + repeat-safe)."""

    def __init__(self, provider: LLMDiscoveryPort | None = None, *, max_attempts: int = 5) -> None:
        self.provider = provider or FallbackDiscovery([])
        self.max_attempts = max_attempts
        self._pool: list[FreeModel] = []
        self._pool_at: float = 0.0
        self._known_good: ModelId | None = None
        self._known_good_at: float = 0.0
        self._failure_times: dict[ModelId, float] = {}

    @classmethod
    def from_settings(
        cls,
        *,
        groq_key: str | None = None,
        mistral_key: str | None = None,
        openrouter_key: str | None = None,
        gemini_key: str | None = None,
    ) -> ModelDiscoverer:
        """Build a discoverer from a fixed provider chain (skips empty keys)."""
        chain: list[LLMDiscoveryPort] = []
        if groq_key:
            chain.append(GroqDiscovery(groq_key))
        if mistral_key:
            chain.append(MistralDiscovery(mistral_key))
        if openrouter_key:
            chain.append(OpenRouterCatalog(openrouter_key))
        if gemini_key:
            chain.append(GeminiDiscovery(gemini_key))
        return cls(FallbackDiscovery(chain))

    async def free_models(self) -> list[FreeModel]:
        """Return the deduped free-model pool, best (largest context) first."""
        if not self._pool or time.monotonic() - self._pool_at > CATALOG_TTL_SECONDS:
            self._pool = await self.provider.list_free_models()
            self._pool_at = time.monotonic()
        return list(self._pool)

    async def select(self, *, preferred: ModelId | None = None) -> ModelId:
        """Return a working free model, reusing a recent known-good one.

        Args:
            preferred: A model id to try first (e.g. a previously selected one).

        Returns:
            ModelId: A model that passed a cheap health check.

        Raises:
            ModelSelectionError: If every candidate fails or the budget is used.
        """
        # Fast path: a recently-validated model is reused without re-testing —
        # this is what makes repeated calls cheap (no quota burn).
        if self._known_good and time.monotonic() - self._known_good_at < KNOWN_GOOD_TTL_SECONDS:
            return self._known_good

        pool = await self.free_models()
        candidates = [m.model_id for m in pool if not self._in_cooldown(m.model_id)]
        if preferred:
            candidates = [preferred] + [m for m in candidates if m != preferred]
        candidates = list(dict.fromkeys(candidates))

        state = SelectionState(candidates=candidates or self._candidate_ids(pool), max_attempts=self.max_attempts)
        decision = ModelSelectionPolicy.initial(state)
        if decision.action != ACTION_CONTINUE:
            raise ModelSelectionError(decision.reason)

        while True:
            if state.exhausted:
                raise ModelSelectionError(f"tried all {len(state.candidates)} free candidates")
            candidate = state.untried[0]
            result = await self._safe_test(candidate)
            decision = ModelSelectionPolicy.on_test_result(state, result)
            if decision.action == ACTION_PASS:
                self._remember_good(candidate)
                return candidate
            if decision.action in (ACTION_EXHAUSTED, ACTION_BUDGET):
                raise ModelSelectionError(decision.reason)
            self._mark_failure(candidate)

    async def complete(self, prompt: str) -> str:
        """Discover a working free model and complete ``prompt`` with it.

        Args:
            prompt: The prompt to send.

        Returns:
            str: The model's completion text.

        Raises:
            ModelSelectionError: If no free model works.
        """
        model = await self.select()
        try:
            return await self.provider.complete(model, prompt)
        except Exception:
            # Model failed on a real call → forget it, pick the next, retry once.
            self._forget_good()
            self._mark_failure(model)
            model = await self.select()
            return await self.provider.complete(model, prompt)

    # -- internals ------------------------------------------------------------
    def _candidate_ids(self, pool: list[FreeModel]) -> list[ModelId]:
        return [m.model_id for m in pool]

    def _in_cooldown(self, model: ModelId) -> bool:
        return time.monotonic() - self._failure_times.get(model, 0.0) < FAILURE_COOLDOWN_SECONDS

    def _mark_failure(self, model: ModelId) -> None:
        self._failure_times[model] = time.monotonic()

    def _remember_good(self, model: ModelId) -> None:
        self._known_good = model
        self._known_good_at = time.monotonic()

    def _forget_good(self) -> None:
        self._known_good = None
        self._known_good_at = 0.0

    async def _safe_test(self, model: ModelId) -> TestResult:
        try:
            return await self.provider.test(model)
        except Exception as exc:  # noqa: BLE001 - a broken provider never aborts selection
            logger.warning("test failed for %s: %s", model, exc)
            return TestResult(model, False, "error")


class ModelSelectionError(RuntimeError):
    """Raised when no free model passes the health-check loop."""


__all__ = ["ModelDiscoverer", "ModelSelectionError"]
