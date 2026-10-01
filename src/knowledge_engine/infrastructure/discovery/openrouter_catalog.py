"""OpenRouter catalog scanner — dynamically discover free models (SOTA).

Queries ``https://openrouter.ai/api/v1/models`` and keeps model IDs whose both
prompt and completion prices are exactly ``0``, filters to text-capable
modalities, and ranks by context length (largest first). Cached 30 minutes so
every pipeline run doesn't re-hit the catalog.

Adapted from digest_agent's ``OpenRouterAdapter``.
"""

from __future__ import annotations

import logging
import time

import httpx

from knowledge_engine.core.discovery import FreeModel, ModelId
from knowledge_engine.infrastructure.discovery.openai_compat import (
    _CACHE_TTL_SECONDS,
    OpenAICompatDiscovery,
)

logger = logging.getLogger(__name__)

_MODELS_ENDPOINT = "https://openrouter.ai/api/v1/models"
_CHAT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterCatalog(OpenAICompatDiscovery):
    """Dynamic zero-price catalog scan + OpenAI-compatible completion."""

    endpoint = "https://openrouter.ai/api/v1"
    candidates: list[FreeModel] = []  # overridden by the catalog scan

    def __init__(self, api_key: str, *, timeout: float = 15.0) -> None:
        super().__init__(api_key, timeout=timeout)
        self._catalog_cache: list[FreeModel] | None = None
        self._catalog_cache_time = 0.0

    async def _fetch_free_models(self) -> list[FreeModel]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(_MODELS_ENDPOINT)
            resp.raise_for_status()
            raw = resp.json().get("data", [])

        free: list[FreeModel] = []
        for m in raw:
            pricing = m.get("pricing", {})
            try:
                prompt_price = float(pricing.get("prompt", "1"))
                completion_price = float(pricing.get("completion", "1"))
            except (TypeError, ValueError):
                continue
            if prompt_price != 0 or completion_price != 0:
                continue
            modality = m.get("architecture", {}).get("modality", "")
            if modality and "text->text" not in modality:
                continue
            try:
                context = int(m.get("context_length") or 0)
            except (TypeError, ValueError):
                context = 0
            free.append(
                FreeModel(
                    model_id=m["id"],
                    provider="OpenRouter",
                    context_length=context,
                    pricing_prompt=prompt_price,
                    pricing_completion=completion_price,
                    modality=modality,
                )
            )
        free.sort()  # FreeModel.__lt__ = larger context first
        return free

    async def list_free_models(self) -> list[FreeModel]:
        if self._catalog_cache is None or time.monotonic() - self._catalog_cache_time > _CACHE_TTL_SECONDS:
            try:
                self._catalog_cache = await self._fetch_free_models()
            except Exception as exc:  # noqa: BLE001 - degrade to static candidates
                logger.warning("openrouter catalog fetch failed: %s; using static candidates", exc)
                self._catalog_cache = list(self.candidates)
            self._catalog_cache_time = time.monotonic()
        return list(self._catalog_cache)

    async def complete(self, model_id: ModelId, prompt: str) -> str:
        """Complete via the catalog's chat endpoint (inherited path works too)."""
        return await OpenAICompatDiscovery.complete(self, model_id, prompt)


__all__ = ["OpenRouterCatalog"]
