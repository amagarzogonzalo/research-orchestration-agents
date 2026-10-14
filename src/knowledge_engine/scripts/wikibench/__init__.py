"""WikiBench — a deterministic benchmark harness for the LLM wiki.

Ten suites (A–J) are described in issue #33; this package implements the
Tier 0 (offline, no-LLM) core plus the workflow coverage used by the research
and deep-research use cases. Nothing here calls the network:

* :mod:`knowledge_engine.scripts.wikibench.corpus` — planted fictional data.
* :mod:`knowledge_engine.scripts.wikibench.metrics` — pure metric math.
* :mod:`knowledge_engine.scripts.wikibench.suites` — one module per suite.
* :mod:`knowledge_engine.scripts.wikibench.runner` — CLI, report, gate.
"""

from __future__ import annotations

__all__: list[str] = []
