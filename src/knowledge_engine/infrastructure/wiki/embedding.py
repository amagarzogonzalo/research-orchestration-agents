"""Deterministic, dependency-free text embeddings for the optional pgvector tier.

This is **not** a semantic model: it is a hashed bag-of-words vector, L2
normalized, so the pgvector path is fully testable offline and works inside the
zero-cost tier. Swap :func:`embed_text` for a real embedding provider (e.g. a
free Gemini embedding model) behind the same signature for true semantics — the
SQL function :func:`wiki.search_pages_semantic` (migration
``20261014120000_wiki_v2_pgvector.sql``) only needs a ``vector(1024)``.
"""

from __future__ import annotations

import hashlib
import math

from knowledge_engine.infrastructure.wiki import common

EMBEDDING_DIM = 1024


def embed_text(text: str, *, dim: int = EMBEDDING_DIM) -> list[float]:
    """Return a deterministic L2-normalized hashed bag-of-words embedding.

    Args:
        text: Text to embed.
        dim: Embedding dimension (must match the SQL ``vector(dim)``).

    Returns:
        list[float]: Unit vector of length ``dim`` (all zeros for empty text).
    """
    vector = [0.0] * dim
    for term in common.terms(text):
        digest = int.from_bytes(hashlib.sha256(term.encode("utf-8")).digest()[:8], "big")
        vector[digest % dim] += 1.0 if (digest >> 63) & 1 == 0 else -1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        float: Cosine similarity in ``[-1, 1]`` (0.0 when either is zero).
    """
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


__all__ = ["EMBEDDING_DIM", "cosine", "embed_text"]
