"""WikiBench — a deterministic benchmark harness for the LLM wiki.

The benchmark is a first-class subsystem, not a script: this package holds the
contracts, metrics, corpus, gate and the per-use-case suites; only the printing
CLI lives under ``knowledge_engine.scripts`` (so the library stays free of
``print`` and is importable by tests and other tools).

Nothing here calls the network. Layout:

* :mod:`knowledge_engine.wikibench.schemas` — contracts (cases, results, report).
* :mod:`knowledge_engine.wikibench.metrics` — pure metric math.
* :mod:`knowledge_engine.wikibench.corpus` — planted fictional data.
* :mod:`knowledge_engine.wikibench.thresholds` — hard floors + regression gate.
* :mod:`knowledge_engine.wikibench.runner` — collect/evaluate/render/persist.
* :mod:`knowledge_engine.wikibench.use_cases` — suites grouped by use case.
"""

from __future__ import annotations

__all__: list[str] = []
