.PHONY: help install test test-fast lint types arch semgrep agents context check dup \
        profile profiles evals evals-deep evals-live evals-accept sessions wiki-lint \
        bench bench-accept bench-check wikibench wikibench-accept wikibench-check \
        retention retention-apply \
        wiki-db-up wiki-db-down wiki-files-check \
        compress-on compress-off compress-savings compress-help

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | expand -t22

install:            ## install deps into .venv (uv)
	uv sync --extra dev

# ---- the gate -------------------------------------------------------------
test:               ## unit/integration/e2e (offline; live excluded by default)
	uv run pytest tests/ -q

test-fast:          ## unit tests only (quick; runs on every push)
	uv run pytest tests/unit -q

test-full:          ## unit+integration+e2e offline (long; main + MR only)
	uv run pytest tests/unit tests/integration tests/e2e -q -m "not live"

lint:               ## formatting + style (project surface only)
	uv run ruff check src tests && uv run ruff format --check src tests

types:              ## static types (strict on new architecture)
	uv run mypy src

arch:               ## module boundaries (tach; hexagonal layering enforced)
	uv run tach check

semgrep:            ## custom security/correctness rules (isolated via uvx)
	uvx semgrep --config .semgrep/rules/ --metrics=off --error src tests

agents:             ## structural checks on AGENTS.md
	uv run python bin/lint_agents.py

dup:                ## copy/paste detection (jscpd, advisory; needs npx network)
	npx -y jscpd@4 src --config .jscpd.json

context:            ## what the always-on prompt costs, enforced
	uv run python bin/context_budget.py

bench:              ## offline perf baseline: ram/checkpoint/supersteps (fakes, no network)
	uv run python -m knowledge_engine.scripts.bench

bench-accept:       ## record bench/baseline.json (review the diff like code)
	uv run python -m knowledge_engine.scripts.bench --accept

bench-check:        ## fail on >10% ram/checkpoint regression vs baseline
	uv run python -m knowledge_engine.scripts.bench --check

wikibench:          ## LLM wiki benchmark: Tier 0 deterministic suites (offline, no LLM)
	uv run python -m knowledge_engine.scripts.wikibench --report

wikibench-accept:   ## adopt the current wiki-benchmark metrics as the baseline (review like code)
	uv run python -m knowledge_engine.scripts.wikibench --accept

wikibench-check:    ## gate the wiki benchmark vs bench/wikibench/baseline.json
	uv run python -m knowledge_engine.scripts.wikibench --check

retention:          ## prune old run artifacts (Supabase free tier); dry-run by default
	uv run python -m knowledge_engine.scripts.retention

retention-apply:    ## apply retention deletions (review the dry-run report first)
	uv run python -m knowledge_engine.scripts.retention --apply

wiki-lint:          ## health gate for the compiled wiki (Loop 5; offline)
	uv run python bin/wiki_lint.py

wiki-lint-deep:     ## deterministic + optional LLM cross-page contribution/stale/gap lint
	uv run python bin/wiki_lint_deep.py

wiki-review:        ## draft review loop: list/accept/reject concept+entity drafts
	uv run python bin/wiki_review.py list

wiki-graph:         ## dump the wiki knowledge graph (nodes/edges/communities) as JSON
	uv run python bin/wiki_graph.py

wiki-db-up:         ## start the scratch Postgres for wiki v2 real-DB tests
	docker compose up -d wiki-db

wiki-db-down:       ## stop the scratch Postgres
	docker compose down

wiki-files-check:   ## read-only files health check (unreferenced/missing-body)
	uv run python bin/wiki_files_check.py

check: lint types arch semgrep agents context test evals bench-check wikibench-check   ## everything. This is the gate.
	@echo green

# ---- models ---------------------------------------------------------------
profiles:           ## list model profiles
	uv run python bin/apply_profile.py --list

profile:            ## switch profile:  make profile P=budget
	uv run python bin/apply_profile.py $(P)

sessions:           ## cost per model over the last 30 days
	uv run python bin/sessions.py

# ---- evals (behavioral regression for LLM-driven code) --------------------
evals:              ## offline replay, free, deterministic
	uv run python evals/runner.py

evals-deep:         ## replay + DeepEval metrics pass (needs the evals extra)
	uv run python evals/runner.py --deep

evals-live:         ## live calls, re-records cassettes. Costs money. Ask first.
	EVAL_MODE=record uv run python evals/runner.py --record

evals-accept:       ## adopt current digests as the new baseline (HUMAN ONLY)
	uv run python evals/runner.py --accept

# ---- context compression (optional) ---------------------------------------
compress-on:        ## start the local compression proxy
	./bin/compress.sh on
compress-off:       ## stop it and restore config
	./bin/compress.sh off
compress-savings:   ## measured reduction on your own traffic
	./bin/compress.sh savings
compress-help:
	./bin/compress.sh help
