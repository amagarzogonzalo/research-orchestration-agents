"""Unit tests: deterministic offline embeddings for the pgvector tier."""

from __future__ import annotations

import math

from knowledge_engine.infrastructure.wiki.embedding import EMBEDDING_DIM, cosine, embed_text


def test_dimension_and_determinism() -> None:
    """Embeddings have the configured dimension and are deterministic."""
    a = embed_text("retrieval augmented generation")
    b = embed_text("retrieval augmented generation")
    assert len(a) == EMBEDDING_DIM
    assert a == b


def test_unit_norm_for_non_empty() -> None:
    """Non-empty text yields a unit vector; empty text is all zeros."""
    vector = embed_text("alpha beta gamma")
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)
    assert embed_text("") == [0.0] * EMBEDDING_DIM


def test_shared_terms_rank_closer_than_unrelated() -> None:
    """Cosine similarity is higher for texts sharing terms."""
    query = embed_text("retrieval augmented generation")
    related = embed_text("augmented retrieval systems for generation")
    unrelated = embed_text("marine acoustics seismic surveying")
    assert cosine(query, related) > cosine(query, unrelated)
    assert math.isclose(cosine(query, query), 1.0, rel_tol=1e-9)
