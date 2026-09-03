"""Letta (MemGPT) memory controller adapter (ADR 0001 MemoryPort).

Letta 0.16.x gives an agent the agency to page/self-edit memory blocks. This
adapter is a thin, dependency-guarded client; enable via the `memory` extra.
"""

from __future__ import annotations

from knowledge_engine.core.ports import ProviderPort

try:  # pragma: no cover - optional dependency
    from letta_client import Client  # type: ignore

    _LETTA_AVAILABLE = True
except ImportError:  # pragma: no cover
    Client = None  # type: ignore
    _LETTA_AVAILABLE = False


class LettaMemoryAdapter:
    def __init__(self, base_url: str, token: str, *, agent_id: str | None = None) -> None:
        if Client is None:
            raise ImportError("letta_client not installed; add the 'memory' extra")
        self.client = Client(base_url=base_url, token=token)
        self.agent_id = agent_id

    async def write(self, block: dict) -> None:
        # placeholder: agent.memory.create_block(name=..., value=...)
        self._ensure_agent()

    async def read(self, name: str) -> str:
        self._ensure_agent()
        return ""

    async def forget(self, name: str) -> None:
        self._ensure_agent()

    def _ensure_agent(self) -> None:
        if not self.agent_id:
            raise RuntimeError("LettaMemoryAdapter requires agent_id to page memory")