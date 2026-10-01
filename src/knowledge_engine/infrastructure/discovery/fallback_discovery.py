"""FallbackDiscovery — chains providers and routes each model to its owner.

Adapted from digest_agent's ``FallbackLLMProvider``. Aggregates the free-model
pool across providers (deduped, provider order preserved), then routes
``test``/``complete`` to whichever provider catalog owns the model — so the
selection loop can try Groq first, then Mistral, then OpenRouter, then Gemini,
and pick the first that actually works.
"""

from __future__ import annotations

import logging
import time

from knowledge_engine.core.discovery import (
    CompletionError,
    FreeModel,
    LLMDiscoveryPort,
    ModelId,
    TestResult,
)

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30 * 60


class FallbackDiscovery(LLMDiscoveryPort):
    """Composite discovery over an ordered list of provider adapters."""

    def __init__(self, providers: list[LLMDiscoveryPort]) -> None:
        """Create a FallbackDiscovery.

        Args:
            providers: Discovery providers.
        """
        self.providers = providers
        self._owner: dict[ModelId, LLMDiscoveryPort] = {}
        self._pool: list[FreeModel] | None = None
        self._pool_time = 0.0

    async def list_free_models(self) -> list[FreeModel]:
        """Return the deduped free pool, providers in the order given.

        Returns:
            list[FreeModel]: Available free models.

        """
        if self._pool is None or time.monotonic() - self._pool_time > _CACHE_TTL_SECONDS:
            pool: list[FreeModel] = []
            seen: set[ModelId] = set()
            for provider in self.providers:
                try:
                    for model in await provider.list_free_models():
                        if model.model_id in seen:
                            continue
                        seen.add(model.model_id)
                        pool.append(model)
                        self._owner.setdefault(model.model_id, provider)
                except Exception as exc:  # noqa: BLE001 - one provider must not sink the loop
                    logger.warning("discovery provider %s failed: %s", type(provider).__name__, exc)
                pool.sort()  # FreeModel.__lt__ = larger context first
            self._pool = pool
            self._pool_time = time.monotonic()
        return list(self._pool)

    async def _provider_for(self, model_id: ModelId) -> LLMDiscoveryPort:
        """Provider for.

        Args:
            model_id: Model identifier.

        Returns:
            LLMDiscoveryPort: Discovery provider.

        Raises:
            CompletionError: When the operation fails.

        """
        if model_id in self._owner:
            return self._owner[model_id]
        for provider in self.providers:
            try:
                if model_id in {m.model_id for m in await provider.list_free_models()}:
                    self._owner[model_id] = provider
                    return provider
            except Exception:  # noqa: BLE001 - a broken provider is skipped
                continue
        raise CompletionError(f"No configured provider exposes model {model_id}")

    async def test(self, model_id: ModelId) -> TestResult:
        """Test a model.

        Args:
            model_id: Model identifier.

        Returns:
            TestResult: Health-check outcome.

        """
        return await (await self._provider_for(model_id)).test(model_id)

    async def complete(self, model_id: ModelId, prompt: str) -> str:
        """Complete a request.

        Args:
            model_id: Model identifier.
            prompt: Prompt text.

        Returns:
            str: Result text.

        """
        return await (await self._provider_for(model_id)).complete(model_id, prompt)


__all__ = ["FallbackDiscovery"]
