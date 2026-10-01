"""Free-LLM discovery contracts (adapted from the digest_agent pattern).

The core capability: given no hardcoded vendor assumptions, *discover* which
free models are currently available, health-check them cheaply, and select one
that actually works — the professional "discover free LLMs" service.

Port surfaces:

* :meth:`LLMDiscoveryPort.list_free_models` — ranked (largest context first)
  zero-cost model IDs.
* :meth:`LLMDiscoveryPort.test` — cheap fixed-prompt health check (no quota burn).
* :meth:`LLMDiscoveryPort.complete` — real completion against a tested model.

Pure selection policy lives in :mod:`knowledge_engine.infrastructure.discovery`.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

ModelId = str


@dataclass(frozen=True)
class FreeModel:
    """A zero-cost model instance exposed by a provider."""

    model_id: ModelId
    provider: str
    context_length: int = 0
    pricing_prompt: float = 0.0
    pricing_completion: float = 0.0
    modality: str = "text->text"
    extra: dict[str, Any] = field(default_factory=dict)

    def __lt__(self, other: object) -> bool:
        """Sort best-first: larger context first."""
        if not isinstance(other, FreeModel):
            return NotImplemented
        return self.context_length > other.context_length


@dataclass(frozen=True)
class TestResult:
    """Result of a cheap health check for one model."""

    model_id: ModelId
    passed: bool
    reason: str = ""  # ok | timeout | rate_limited | empty_response | response_too_short | error_masked | error

    __test__ = False  # pytest must not treat this as a test class


class CompletionError(RuntimeError):
    """Raised when a discovery provider fails to complete a prompt."""


class LLMDiscoveryPort(abc.ABC):
    """Port for discovering + testing + completing against free models."""

    @abc.abstractmethod
    async def list_free_models(self) -> list[FreeModel]:
        """Return currently-free models, best (largest context) first.

        Must never include paid models. Network calls are allowed but should be
        cached (e.g. 30 min) to conserve free-tier quota.
        """
        ...

    @abc.abstractmethod
    async def test(self, model_id: ModelId) -> TestResult:
        """Cheap health check; a lightweight prompt, minimal output tokens."""
        ...

    @abc.abstractmethod
    async def complete(self, model_id: ModelId, prompt: str) -> str:
        """Complete ``prompt`` with ``model_id``; raise :class:`CompletionError`."""
        ...


__all__ = ["CompletionError", "FreeModel", "LLMDiscoveryPort", "ModelId", "TestResult"]
