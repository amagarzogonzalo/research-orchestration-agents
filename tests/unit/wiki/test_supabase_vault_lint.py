"""Unit tests: SupabaseVaultLinter + cloud cache coherence (fake client)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fakes import fake_supabase_client  # noqa: E402

from knowledge_engine.infrastructure.wiki import SupabaseVaultLinter, SupabaseWikiStore  # noqa: E402


async def test_cache_coherence_across_store_instances() -> None:
    """Two stores on one bucket see each other's filings (shared cache)."""
    client = fake_supabase_client({})
    first = SupabaseWikiStore(client, bucket="wiki")
    await first.ingest("t1", "rag topic", _tmp_article("rag bytes"))
    # A different process would construct a fresh store; the cloud cache
    # object must make the duplicate visible to it.
    second = SupabaseWikiStore(client, bucket="wiki")
    result = await second.ingest("t2", "other topic", _tmp_article("rag bytes"))
    assert result.verdict.value == "no_material"
    assert second._load_cloud_cache()["t1"]["sha"] == first._load_cloud_cache()["t1"]["sha"]


def _tmp_article(text: str) -> str:
    """Write a scratch article and return its path.

    Args:
        text: Article content.

    Returns:
        str: Path.
    """
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(text)
        return fh.name


async def test_cloud_linter_reports_findings() -> None:
    """The cloud linter surfaces missing frontmatter + orphans from pages."""
    client = fake_supabase_client(
        {
            "pages/orphan-page.md": "no frontmatter here at all, just body bytes " * 5,
        }
    )
    store = SupabaseWikiStore(client, bucket="wiki")
    report = await SupabaseVaultLinter(store).lint()
    assert report.pages == 1
    assert report.missing_frontmatter == ["orphan-page"]
    assert not report.ok


async def test_cloud_linter_ok_on_clean_bucket() -> None:
    """A well-formed indexed page reports ok."""
    page = "---\ntype: source\ntitle: RAG\ndate: 2026-10-09\n---\n\n" + "solid content " * 40
    index = "# Index\n- [RAG](pages/rag.md) — fine\n"
    client = fake_supabase_client({"pages/rag.md": page, "index.md": index})
    store = SupabaseWikiStore(client, bucket="wiki")
    report = await SupabaseVaultLinter(store).lint()
    assert report.ok
    assert report.pages == 1


async def test_cloud_linter_counts_queries_area() -> None:
    """Filed answers under queries/ are enumerated (regression: live probe)."""
    answer = "---\ntype: query\ntitle: Answer\ndate: 2026-10-09\n---\n\n" + "compiled answer text " * 40
    client = fake_supabase_client(
        {"queries/rag-answer.md": answer, "index.md": "# Index\n- [Answer](queries/rag-answer.md) — fine\n"}
    )
    store = SupabaseWikiStore(client, bucket="wiki")
    report = await SupabaseVaultLinter(store).lint()
    assert report.pages == 1
    assert report.ok
