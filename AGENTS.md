# AGENTS.md — Knowledge Engine (research-orchestration-agents)

A portable, zero-cost research library: scholarly search, web scrape, browser
automation, LLM synthesis — writing plain files under `research_store/`. No
paid LLM tiers, no SaaS monitoring.

## Layout
| Path | What lives here |
|---|---|
| `src/knowledge_engine/` | The library. Start at `engine.py` (ResearchEngine facade). |
| `src/knowledge_engine/orchestration/` | LangGraph: typed state, SOLID node classes, graphs. |
| `src/knowledge_engine/store/` | Files-only persistence: ResearchStore + FileCheckpointer. |
| `src/knowledge_engine/infrastructure/` | Adapters: search, scrape, browser, discovery, mcp. |
| `tests/` | unit/ integration/ e2e/ (all offline; `-m live` excluded by default). |
| `evals/` | Behavioral regression suite (optional). `_demo` is the green self-test. |
| `harness.toml` | Model profiles, routing, context budgets. Change models here, not in agent files. |

## Commands (verbatim — do not improvise flags)
```
make install     # uv sync --extra dev
make test        # pytest tests/ -q
make lint        # ruff check+format src tests
make types       # mypy src  (strict; adapter glue exempt)
make context     # what the always-on prompt costs
make check       # lint + types + context + test + evals   <- the gate
```

## Hard rules
1. **Never commit** `.env`, keys, or credentials. Commit on topic branches and push for review; never push directly to `main`.
2. **Do not "fix" intentional ignores.** `# type: ignore[...]` / `# noqa:` with a
   trailing reason are deliberate. Leave them.
3. **Zero-cost discipline holds.** Free tiers only (Groq/Gemini/Mistral/OpenRouter
   free/Ollama). Never introduce a paid LLM call. Prefer OpenAlex DOI lookups.
4. **No new dependency and no new MCP server without asking.** Every tool schema is
   prompt weight on every turn.
5. **If a read or fetch fails, say so and stop.** Never substitute recalled knowledge
   for a lookup that failed.
6. **Do not raise a budget to make a check pass.** Fix the thing, or say why.

## Definition of done
`make check` is green, and you have stated in one line each:
- what behavior changed,
- which eval case covers it (or the new case you added),
- what you did **not** do that a reviewer might expect.

## Conventions
- Python 3.11/3.12, uv. Ruff line length **119**; `select = E,F,I,W,UP,B`.
- mypy strict on the new architecture; adapter glue is documented as relaxed
  (`pyproject.toml` overrides). Google docstrings required on ALL
  modules/classes/methods/functions (public + private): `Args/Returns/Raises/Attributes`,
  no type hints in docstring (signatures carry types). See `skills/documenting-python/SKILL.md`.
- pre-commit: ruff, ruff-format, mypy, pydoclint (strict Google, all functions), trailing-whitespace, eof-fixer.
- Nodes stay pure `(State) -> State`; I/O goes through `infrastructure/`.
- Commits: `area: imperative summary`.
- Errors: typed exceptions from `errors.py`. Never bare `except:`.
