#!/usr/bin/env python3
"""Regenerate the golden-master prompt fixtures (tests/fixtures/golden/*.json).

Run ONLY when a prompt-byte change is DELIBERATE — and then also follow the
documented eval re-record loop (wipe -> record -> replay bit-identical ->
--accept, human-reviewed)::

    uv run python bin/golden_fixtures.py

Cassette keys hash prompt bytes (``evals/lib/cassette.py``), so these fixtures
are the tripwire that keeps synthesize evidence formatting replay-neutral:
``tests/unit/test_prompt_golden_master.py`` fails here first, not as a late
``CassetteMiss``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from knowledge_engine.orchestration.nodes.synthesize import SynthesizeNode
from knowledge_engine.schemas.source import SearchSource

FIXTURES_DIR = Path("tests/fixtures/golden")

_QUERY = "How does retrieval-augmented generation reduce hallucinations"

_LONG_MARKDOWN = "# Deep paper body\n" + ("Evidence paragraph with quantitative claim 42%. " * 90)

_SOURCES_RAW: list[dict[str, Any]] = [
    {
        "id": "openalex:W1",
        "doi": "10.1234/rag-survey",
        "title": "Retrieval-Augmented Generation Survey",
        "source": SearchSource.OPENALEX.value,
        "authors": ["A. Author", "B. Author"],
        "year": 2026,
        "abstract": "A survey of grounding mechanisms for reducing hallucination in LLMs. " * 6,
        "venue": "Journal of AI Research",
        "references": ["10.5678/hyper-rag"],
    },
    {
        "id": "openalex:W2",
        "doi": "10.5678/hyper-rag",
        "title": "Hyper-RAG: combating hallucinations",
        "source": SearchSource.OPENALEX.value,
        "authors": ["C. Researcher"],
        "year": 2026,
        "abstract": "Hypergraph-driven retrieval combatting hallucination in production systems. " * 6,
        "venue": "Nature Communications",
        "references": [],
    },
]

_BODIES_RAW: list[dict[str, Any]] = [
    {
        "url": "https://arxiv.org/abs/2404.08189",
        "title": "Reducing hallucination in structured outputs via RAG",
        "markdown": _LONG_MARKDOWN,
        "char_count": len(_LONG_MARKDOWN),
        "crawl_method": "trafilatura",
        "captured_at": None,
    },
    {
        "url": "https://www.nature.com/articles/s41467-026-71411-1",
        "title": "Hyper-RAG full text",
        "markdown": "# Hyper-RAG\nMechanism body with a named benchmark result: 37% reduction.\n",
        "char_count": 76,
        "crawl_method": "trafilatura",
        "captured_at": None,
    },
]


def _bodies_state() -> dict[str, Any]:
    """State with bodies + raw sources (test reconstructs objects).

    Returns:
        dict[str, Any]: WorkflowState-shaped mapping.
    """
    return {
        "query": _QUERY,
        "sources": _SOURCES_RAW,
        "filtered": _SOURCES_RAW,
        "bodies": _BODIES_RAW,
    }


def _abstracts_state() -> dict[str, Any]:
    """State without bodies: ledger over abstracts + in-set citations.

    Returns:
        dict[str, Any]: WorkflowState-shaped mapping.
    """
    return {
        "query": _QUERY,
        "sources": _SOURCES_RAW,
        "filtered": [],
        "bodies": [],
    }


def _dict_snapshot_state() -> dict[str, Any]:
    """State whose filtered set is dict snapshots (labeling skipped path).

    Returns:
        dict[str, Any]: WorkflowState-shaped mapping.
    """
    return {
        "query": _QUERY,
        "sources": _SOURCES_RAW,
        "filtered": [dict(record) for record in _SOURCES_RAW],
        "bodies": [dict(body) for body in _BODIES_RAW],
    }


def _fixtures() -> list[dict[str, Any]]:
    """Build every fixture: name, raw state, flags, and the current prompt bytes.

    Returns:
        list[dict[str, Any]]: Fixture mappings ready for JSON serialization.
    """
    specs: list[tuple[str, dict[str, Any], str | None, bool]] = [
        ("bodies_with_object_sources", _bodies_state(), None, True),
        ("abstracts_only", _abstracts_state(), None, True),
        ("dict_snapshots_with_critique", _dict_snapshot_state(), "Section 2 lacks citations.", False),
    ]
    out: list[dict[str, Any]] = []
    for name, state, critique, use_object_sources in specs:
        expected = SynthesizeNode._build_prompt(state, critique)  # type: ignore[arg-type]
        out.append(
            {
                "name": name,
                "query": _QUERY,
                "critique": critique,
                "use_object_sources": use_object_sources,
                "state": state,
                "expected_prompt": expected,
            }
        )
    return out


def main() -> int:
    """Regenerate golden fixtures from the current SynthesizeNode formatting.

    Returns:
        int: Exit code.
    """
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for fixture in _fixtures():
        path = FIXTURES_DIR / f"{fixture['name']}.json"
        path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
