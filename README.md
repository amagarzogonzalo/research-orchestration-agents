# Knowledge Engine

Autonomous, **zero-cost** single-user research-curation library: a Hexagonal core
orchestrated by LangGraph with files-only persistence under `research_store/`.
Built strictly on free-tier LLMs (Gemini Flash / Groq LPU / OpenRouter / Mistral)
with OpenTelemetry observability — no paid LLM tiers, no paid SaaS monitoring,
no broker, no database server.

Design rationale lives in [docs/plans](docs/plans/0000-overview.md) as ADRs.

## Layout

```
src/knowledge_engine/
  core/            Hexagonal ports, exceptions, local pricing table
  schemas/         Pydantic domain contracts (the cross-port API)
  infrastructure/  adapters: rate-limit, circuit, router, search, scrape, providers
  orchestration/   LangGraph graphs + SOLID node classes (ADR 0008)
  observability/   OpenTelemetry GenAI (ADR 0006)
  store/           files-only persistence: ResearchStore + FileCheckpointer
  engine.py        public facade (ResearchEngine)
```

The runtime is a **single-process LangGraph workflow**: nodes read/write typed
state, durability comes from `FileCheckpointer`, artifacts land as plain files.
See `docs/plans/0008-langgraph-orchestrator.md` (0007's event shell was removed
for the single-user design).

## Install

```bash
python -m venv .venv && .venv\Scripts\activate   # win32
pip install -e ".[dev, providers]"               # + scrape / vector / memory extras as needed
cp .env.example .env                             # fill free-tier LLM keys
```

Runtime floor: **Python 3.11 or 3.12** (`requires-python` enforces this).

## Run

```python
import asyncio
from knowledge_engine.engine import ResearchEngine

async def main() -> None:
    engine = ResearchEngine()
    result = await engine.research("state of the art in multi-agent orchestration")
    print(result.state.output_path)

asyncio.run(main())
```

## Test

```bash
pytest
```

The rate limiter + fallback router are unit-tested against fakes (no network, no Redis),
so the resilience core is verifiable offline.

## Zero-cost discipline

- OpenAlex: single DOI/ID lookups are **free & un-metered** — prefer them over search.
- Semantic Scholar: shared 1k req/s public limit — always behind the token bucket + circuit breaker.
- OpenRouter: ~20 RPM, no SLA — reserved for the fallback cascade only.
- Cost is recomputed locally (never trusted from vendor) for internally-consistent metrics.

## Federated search sources

All keyless. The set mirrors industry practice (Elicit: Semantic Scholar +
PubMed + OpenAlex; ScholarXIV/Academix add arXiv, Crossref, dblp) with an
OA-full-text layer on top:

| Source | Role | Auth / limits |
|---|---|---|
| OpenAlex | DOI-first metadata backbone (free per-ID lookups) | mailto polite pool |
| PubMed (E-utilities) | Biomedical depth, 33M+ records, MeSH syntax | keyless, self-throttled 3/s |
| Europe PMC | Biomedical + OA full-text links | keyless |
| Semantic Scholar | Influence/citation signal | keyless (shared quota) |
| Crossref | DOI registry fallback (fetch + validation) | mailto polite pool |
| arXiv | Physics/math/CS preprints | keyless |
| bioRxiv / medRxiv | Life-science preprints: freshest ~14d slice + DOI fetch (archive via OpenAlex) | keyless |
| HuggingFace Papers | Community-ranked ML papers, code links | keyless |
| dblp | CS bibliography, venue metadata | keyless, bot-gated from some networks (degrades gracefully) |
| DOAJ | OA journals, full-text links | keyless |
| Unpaywall | Legal OA PDF resolution by DOI (fetch only) | real `UNPAYWALL_EMAIL` required |

Scrape tier: crawl4ai → trafilatura (local, free) → Jina Reader (remote last
resort, keyless ~20 RPM, optional `JINA_API_KEY`). Papers With Code is
officially sunset; its successor (HuggingFace Papers) is wired above.

Deliberately not wired: ORCID (researcher profiles, not papers), OpenCitations
(no verifiable live endpoint: v1 410, v2 paths 404/400), OpenAIRE (OAF schema
too nested to map robustly; same records arrive via OpenAlex), key-gated APIs
(CORE, BASE, Lens, ADS), Google Scholar (no API; scraping violates ToS).
