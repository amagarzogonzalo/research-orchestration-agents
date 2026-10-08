"""BodyStoragePort contract (W4, ADR 0010): deterministic refs, roundtrip, traversal guard.

The port is the seam that lets WorkflowState carry lightweight BodyRef pointers
instead of full DocumentBody markdown (10-50KB per body) — checkpoint blobs
then stay small enough for a 512MB RAM budget.
"""

from __future__ import annotations

import pytest

from knowledge_engine.core.exceptions import KnowledgeEngineError
from knowledge_engine.schemas.source import DocumentBody, FetchMethod
from knowledge_engine.store.body_file_store import LocalBodyStorage


async def test_save_returns_deterministic_ref(tmp_path) -> None:
    """The same content saved twice lands on the same ref (idempotent upsert)."""
    store = LocalBodyStorage(root=tmp_path / "bodies")
    body = DocumentBody(url="https://arxiv.org/abs/2404.08189", title="T", markdown="# Body")
    ref1 = await store.save(body)
    ref2 = await store.save(body)
    assert ref1.path == ref2.path
    assert ref1.url == "https://arxiv.org/abs/2404.08189"
    assert ref1.title == "T"
    assert ref1.char_count == len("# Body")


async def test_load_roundtrip_preserves_bytes(tmp_path) -> None:
    """Save then load is byte-exact."""
    store = LocalBodyStorage(root=tmp_path / "bodies")
    markdown = "# Title\n\nParagraph with **bold** and quantitative claim 42%.\n"
    ref = await store.save(DocumentBody(url="https://example.com/x", title="X", markdown=markdown))
    assert await store.load(ref) == markdown


async def test_distinct_urls_get_distinct_refs(tmp_path) -> None:
    """100 distinct URLs produce 100 distinct refs (no hash collisions at this scale)."""
    store = LocalBodyStorage(root=tmp_path / "bodies")
    refs = [
        await store.save(DocumentBody(url=f"https://example.com/{i}", title=f"T{i}", markdown="m" * (i + 1)))
        for i in range(100)
    ]
    assert len({ref.path for ref in refs}) == 100


async def test_load_rejects_refs_outside_root(tmp_path) -> None:
    """A ref pointing outside the storage root is refused (path traversal)."""
    store = LocalBodyStorage(root=tmp_path / "bodies")
    outside = tmp_path / "secret.md"
    outside.write_text("top secret", encoding="utf-8")
    from knowledge_engine.schemas.source import BodyRef

    with pytest.raises(KnowledgeEngineError):
        await store.load(BodyRef(url="https://x", title="X", path=str(outside)))


async def test_load_rejects_missing_ref(tmp_path) -> None:
    """A ref whose file was never saved raises instead of returning empty."""
    store = LocalBodyStorage(root=tmp_path / "bodies")
    from knowledge_engine.schemas.source import BodyRef

    with pytest.raises(KnowledgeEngineError):
        await store.load(BodyRef(url="https://x", title="X", path=str(tmp_path / "bodies" / "nope.md")))


async def test_crawl_method_roundtrips(tmp_path) -> None:
    """FetchMethod metadata survives the ref."""
    store = LocalBodyStorage(root=tmp_path / "bodies")
    ref = await store.save(
        DocumentBody(url="https://example.com", title="T", markdown="m", crawl_method=FetchMethod.TRAFILATURA)
    )
    assert ref.crawl_method == FetchMethod.TRAFILATURA
