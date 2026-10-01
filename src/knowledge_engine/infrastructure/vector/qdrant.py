"""Qdrant vector-store adapter (ADR 0001 VectorPort)."""

from __future__ import annotations

from knowledge_engine.core.ports import ProviderPort

# Thin facade; kept separate so a schema change in the vector layer never leaks
# into the core. Requires the `vector` extra (qdrant-client>=1.19).
try:  # pragma: no cover - optional dependency
    from qdrant_client import AsyncQdrantClient  # type: ignore

    _QDRANT_AVAILABLE = True
except ImportError:  # pragma: no cover
    AsyncQdrantClient = None  # type: ignore
    _QDRANT_AVAILABLE = False


class QdrantAdapter:
    def __init__(
        self, url: str, collection: str = "sources", *, embedding_provider: ProviderPort | None = None
    ) -> None:
        if AsyncQdrantClient is None:
            raise ImportError("qdrant-client not installed; add the 'vector' extra")
        self.client = AsyncQdrantClient(url=url)
        self.collection = collection
        self.embedding_provider = embedding_provider

    async def upsert(self, records: list) -> None:
        # placeholder: would embed + upsert DocumentBody vectors
        await self.client.upsert(self.collection, points=[])

    async def query(self, text: str, *, top_k: int = 5) -> list:
        # placeholder: embed text and search
        return []

    async def close(self) -> None:
        await self.client.close()
