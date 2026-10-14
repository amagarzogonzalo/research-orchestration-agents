# Contributing

New here? Read [docs/quickstart.md](docs/quickstart.md) first — it gets you from
a clone to a green offline run in ~2 minutes.

We use **trunk-based development** with short-lived feature branches and pull
requests (PRs / merge requests) into `main`.

## Workflow (from now on)

1. **Create a branch from `main`** — always start from an up-to-date `main`:
   ```bash
   git checkout main && git pull
   git checkout -b feature/<what-you-are-building>
   # or: git checkout -b fix/<bug-description>
   ```
2. **Do the work** in small, focused commits on that branch.
3. **Push the branch** and **open a PR** into `main`:
   ```bash
   git push -u origin feature/<branch>
   gh pr create --fill     # or open via the GitHub UI
   ```
4. **CI runs** the unit test suite + lint on every PR. It must pass.
5. **Get it reviewed**, address feedback, **merge via PR** (squash to keep `main`
   linear), then **delete the branch**.

## Conventions

- Keep `main` green at all times.
- Never push directly to `main` — always through a reviewed PR.
- Use prefixes: `feature/*`, `fix/*`, `chore/*`, `docs/*`, `refactor/*`.

## Local checks

The single gate is `make check` (lint + types + arch + semgrep + agents + context
+ tests + evals + benchmarks). Quick loop while working:

```bash
make test-fast    # unit tests only
make lint         # ruff check + format --check
make check        # everything — required before merging
```

> Note: on the free GitHub plan, `main` can't be hard-protected on a private
> repo. Treat `main` as protected by convention; the CI workflow is the
> enforcement point until the repo is public or upgraded.
