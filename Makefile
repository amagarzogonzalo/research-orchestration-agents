.PHONY: help install test test-fast lint types arch semgrep agents context check dup \
        profile profiles evals evals-live evals-accept sessions \
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

check: lint types arch semgrep agents context test evals   ## everything. This is the gate.
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
