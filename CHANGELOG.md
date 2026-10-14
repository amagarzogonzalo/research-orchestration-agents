# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project aims for
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **HTTP service** (`knowledge_engine.service`): FastAPI facade with `/health`,
  `/readiness`, `POST /research` (202 + poll), `/result/{id}`, `/wiki/query`,
  `/wiki/ingest`, `/wiki/lint`; durable Postgres `service_jobs`; bearer auth
  (write + read-only tokens); per-key rate limiting; per-job timeout.
- **Deploy assets**: `Dockerfile`, `render.yaml`, `deploy.yml`,
  `docs/deploy-render.md` (Render Free + Supabase Free, session-pooler note).
- **Retention** (`knowledge_engine.scripts.retention`): prunes Storage objects
  and old `service_jobs`, reports Storage/Postgres usage before/after, and warns
  above a budget threshold; weekly workflow.
- **Keepalive** workflow (daily wake + readiness + one `qa` run) and Dependabot.
- **WikiBench** (`knowledge_engine.wikibench`): deterministic, no-LLM benchmark
  (retrieval, miss, cost, ingest, lint, robustness, workflows), CI-gated against
  a committed baseline.
- **Docs**: `docs/quickstart.md`, `CONTRIBUTING.md` pointers, `LICENSE` (MIT).
- **Env cutover switches** wired into `ResearchEngine` (`CHECKPOINTER_KIND`,
  `STORE_KIND`, `ARTIFACT_STORE_KIND`, `BODY_STORE_KIND`, `WIKI_STORE_KIND`).

### Changed

- **Storage buckets are private**; locators are object keys, reads go through the
  authenticated service.
- `/wiki/query` and `/result` require a (read) token; `/research` and wiki
  mutations require the write token.
- The wiki store is built once per engine and closed on `engine.close()`.

### Fixed

- `SupabaseArtifactStore`/`SupabaseBodyStorage` no longer heal buckets to public.
- `SupabaseBodyStorage` covered by tests (previously untested).
- Hybrid wiki search no longer returns duplicate pages; overview links use each
  page's own subdirectory; filed theses/answers are indexed.

## [0.1.0]

- Initial portable, zero-cost research library: Hexagonal core, LangGraph
  orchestration, files-only persistence, free-tier LLM routing, OpenTelemetry.

[Unreleased]: https://github.com/amagarzogonzalo/research-orchestration-agents/commits/main
[0.1.0]: https://github.com/amagarzogonzalo/research-orchestration-agents/releases/tag/v0.1.0
