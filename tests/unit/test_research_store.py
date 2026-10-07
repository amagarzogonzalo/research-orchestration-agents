"""Unit tests: ResearchStore files-only persistence layer."""

from __future__ import annotations

import json

from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.source import SearchSource, SourceRecord
from knowledge_engine.store.research_store import ResearchStore


def test_write_and_read_metadata(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    store.write_metadata("t1", {"query": "agents", "status": "done"})
    assert store.read_metadata("t1") == {"query": "agents", "status": "done"}


def test_append_and_read_events(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    store.append_event("t1", {"event": "started"})
    store.append_event("t1", {"event": "completed"})
    events = store.read_events("t1")
    assert [e["event"] for e in events] == ["started", "completed"]
    assert all("ts" in e for e in events)


def test_write_read_sources(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    src = SourceRecord(id="openalex:W1", title="T", source=SearchSource.OPENALEX)
    store.write_sources("t1", [src])
    loaded = store.read_sources("t1")
    assert loaded == [src]


def test_write_article_creates_markdown(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    art = KnowledgeArtifact(
        id="t1",
        title="Research",
        body_markdown="# Notes",
        frontmatter_yaml="type: KnowledgeBaseArticle\n",
    )
    store.write_article(art, "t1")
    text = store.article_path("t1").read_text(encoding="utf-8")
    assert "type: KnowledgeBaseArticle" in text
    assert "# Notes" in text


def test_missing_files_default_gracefully(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    assert store.read_metadata("nope") == {}
    assert store.read_events("nope") == []
    assert store.read_sources("nope") == []
    assert store.exists("nope") is False


def test_run_dir_created(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    assert store.run_dir("t9").exists()


def test_write_metadata_preserves_query(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    store.write_metadata("t2", {"query": "q1"})
    store.write_metadata("t2", {"query": "q1", "status": "done"})
    meta = store.read_metadata("t2")
    assert meta["query"] == "q1"
    # Json field order is preserved on round-trip
    assert json.loads(store.metadata_path("t2").read_text(encoding="utf-8"))["query"] == "q1"
