"""Deterministic fictional corpus + query generation for WikiBench.

Planted facts only: every entity is invented ("Vorlund protocol", "Kestrel-9
battery") so no model's training knowledge can answer for the wiki. The same
``(seed, n_pages)`` always yields the same corpus and queries, so a run replays
byte-identically.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.infrastructure.wiki.sidecar import WikiSidecar
from knowledge_engine.scripts.wikibench.schemas import CorpusPage, QueryCase, QueryKind

_ADJ = (
    "Vorlund",
    "Kestrel",
    "Marrow",
    "Tessellate",
    "Halcyon",
    "Nimbus",
    "Cinder",
    "Quartz",
    "Lattice",
    "Obsidian",
    "Verdigris",
    "Sable",
    "Ravel",
    "Onyx",
    "Cinnabar",
    "Trellis",
)
_NOUN = (
    "protocol",
    "battery",
    "index",
    "array",
    "beacon",
    "cascade",
    "ledger",
    "prism",
    "shard",
    "vein",
    "lattice",
    "cipher",
    "conduit",
    "harness",
    "reliquary",
    "monolith",
)
_DOMAINS = (
    "orbital telemetry",
    "cold-chain logistics",
    "adaptive optics",
    "marine acoustics",
    "quantum sensing",
    "grid balancing",
    "seismic surveying",
    "bio-reactor control",
)
#: Distinct marker syllables so a different seed changes every anchor token.
_MARKER_SYLLABLES = ("sigil", "glyph", "rune", "token", "cipher", "sigil", "sigil", "sigil")

#: Query terms that share no substring with any corpus token (a miss is correct).
_UNANSWERABLE = (
    "zqvw plumbiferous xenodocheion",
    "quixotic zephyr blorptastic",
    "ninth aetherial quonk",
)

_FILLER = "This placeholder passage pads the body to a realistic length. "


@dataclass(frozen=True)
class Corpus:
    """A deterministic corpus plus its query cases.

    Attributes:
        seed: RNG seed the corpus was generated with.
        pages: Seeded pages (unique slugs and titles).
        queries: Query cases with gold slugs.
    """

    seed: int
    pages: tuple[CorpusPage, ...]
    queries: tuple[QueryCase, ...]


def build_corpus(n_pages: int, seed: int = 7) -> Corpus:
    """Build the fictional corpus and its queries deterministically.

    Args:
        n_pages: Number of source pages to seed (>= 1).
        seed: RNG seed; changes marker tokens (not slugs).

    Returns:
        Corpus: Pages and query cases, identical for a given seed.

    Raises:
        ValueError: When ``n_pages`` is less than 1.
    """
    if n_pages < 1:
        raise ValueError("n_pages must be >= 1")
    syllable = _MARKER_SYLLABLES[seed % len(_MARKER_SYLLABLES)]
    pages: list[CorpusPage] = []
    for i in range(n_pages):
        adj = _ADJ[i % len(_ADJ)]
        noun = _NOUN[(i // len(_ADJ)) % len(_NOUN)]
        domain = _DOMAINS[i % len(_DOMAINS)]
        title = f"{adj} {noun} {i:04d}"
        slug = f"{adj}-{noun}-{i}".lower()
        marker = f"{syllable}{i:04d}"
        body = (
            f"# {title}\n\n"
            f"The {title} is a {noun} used in {domain}. Its unique anchor is {marker}. "
            f"Engineers deploy the {adj} family for {domain} work; the {noun} records every event.\n\n"
            f"{_FILLER * 3}\n"
        )
        pages.append(CorpusPage(slug=slug, title=title, marker=marker, body=body))
    return Corpus(seed=seed, pages=tuple(pages), queries=tuple(_build_queries(pages)))


def _build_queries(pages: list[CorpusPage]) -> list[QueryCase]:
    """Derive the five query kinds from the seeded pages.

    Args:
        pages: Seeded pages.

    Returns:
        list[QueryCase]: Query cases in a stable order.
    """
    queries: list[QueryCase] = []
    for i, page in enumerate(pages):
        adj, noun = page.title.split()[0], page.title.split()[1]
        queries.append(
            QueryCase(
                id=f"exact-{i:04d}",
                suite="a_retrieval",
                kind=QueryKind.EXACT,
                query=page.title,
                gold_slugs=[page.slug],
            )
        )
        queries.append(
            QueryCase(
                id=f"para-{i:04d}",
                suite="a_retrieval",
                kind=QueryKind.PARAPHRASE,
                query=f"{page.marker} {noun} {adj}",
                gold_slugs=[page.slug],
            )
        )
        queries.append(
            QueryCase(
                id=f"alias-{i:04d}",
                suite="a_retrieval",
                kind=QueryKind.ALIAS,
                query=page.marker,
                gold_slugs=[page.slug],
            )
        )
    for i in range(0, max(0, len(pages) - 1), 10):
        first, second = pages[i], pages[i + 1]
        queries.append(
            QueryCase(
                id=f"multi-{i:04d}",
                suite="a_retrieval",
                kind=QueryKind.MULTI_PAGE,
                query=f"{first.marker} {second.marker}",
                gold_slugs=[first.slug, second.slug],
            )
        )
    for j, text in enumerate(_UNANSWERABLE):
        queries.append(QueryCase(id=f"miss-{j}", suite="b_miss", kind=QueryKind.UNANSWERABLE, query=text))
    return queries


def seed_vault(root: Path, corpus: Corpus, *, sidecar: bool = False) -> None:
    """Write the corpus as a markdown vault (index + pages), no ingest cost.

    Seeding bypasses the store's ingest pipeline on purpose: suites that
    isolate retrieval quality (A) or lint (I) must not pay ingest's O(N)
    index/overview rewrites.

    Args:
        root: Vault root (created on demand).
        corpus: Corpus to materialise.
        sidecar: When True, build the FTS5 sidecar index as well.
    """
    sources = root / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    entries: list[str] = []
    for page in corpus.pages:
        fm = {
            "type": "source",
            "title": page.title,
            "date": "2026-01-01",
            "query_sha256": common.sha256_text(page.title),
            "tldr": common.tldr(page.body),
        }
        text = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + page.body
        (sources / f"{page.slug}.md").write_text(text, encoding="utf-8")
        entries.append(f"- [{page.title}](sources/{page.slug}.md) — {fm['tldr']}")
    (root / common.INDEX_NAME).write_text(
        "# Knowledge Base Index\n\n## Sources\n" + "\n".join(entries) + "\n", encoding="utf-8"
    )
    (root / common.LOG_NAME).write_text("# Wiki Log\n", encoding="utf-8")
    (root / common.OVERVIEW_NAME).write_text("# Overview\n", encoding="utf-8")
    if sidecar:
        WikiSidecar(root).build()


__all__ = ["Corpus", "build_corpus", "seed_vault"]
