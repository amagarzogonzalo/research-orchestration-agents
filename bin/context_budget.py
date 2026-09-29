#!/usr/bin/env python3
"""Measure the context you pay for on EVERY request, and fail when it grows.

    python bin/context_budget.py            # report + enforce
    python bin/context_budget.py --report   # report only, never fails

Context bloat is invisible by construction: nobody sees the prompt, so AGENTS.md
grows a section per incident, three MCP servers get added for one task each, and
six months later every trivial request drags 20k tokens of preamble. The cost is
not only money — past a certain length models demonstrably stop honoring the
middle of a long instruction block, so a bloated briefing file is *less obeyed*
than a short one.

This makes that number visible and gates it like any other budget.

What counts as always-on:
  AGENTS.md, everything in .opencode/rules/, every file named in
  opencode.json:instructions, the skill INDEX (name + description of each skill,
  which the model must see to know the skill exists), and MCP tool schemas.
What does not: skill BODIES. That is the whole point of progressive disclosure —
a skill body costs nothing until it triggers.
"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def count_tokens(text: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:  # noqa: BLE001 -- tiktoken is optional; char/3.7 fallback is intentional
        # ~3.7 chars/token for English prose+markdown. Within ~10% of a real
        # tokenizer, which is enough to catch a doubling.
        return round(len(text) / 3.7)


def frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip("\"'")
    return out


def collect() -> tuple[list[tuple[str, int, str]], list[str]]:
    rows: list[tuple[str, int, str]] = []
    warnings: list[str] = []
    cfg = tomllib.loads((ROOT / "harness.toml").read_text())
    limits = cfg.get("context", {})

    agents = ROOT / "AGENTS.md"
    if agents.exists():
        text = agents.read_text()
        n = len(text.splitlines())
        rows.append(("AGENTS.md", count_tokens(text), f"{n} lines"))
        if n > limits.get("agents_md_max_lines", 100):
            warnings.append(
                f"AGENTS.md is {n} lines (limit {limits['agents_md_max_lines']}). "
                "Move a procedure into skills/ — skills cost nothing until triggered."
            )
    else:
        warnings.append("no AGENTS.md — every tool that reads it gets nothing")

    for r in sorted((ROOT / ".opencode" / "rules").glob("*.md")):
        rows.append((f"rules/{r.name}", count_tokens(r.read_text()), ""))

    opencode = ROOT / "opencode.json"
    conf = json.loads(opencode.read_text()) if opencode.exists() else {}

    for extra in conf.get("instructions", []):
        if any(ch in extra for ch in "*?["):
            continue
        p = ROOT / extra
        if p.exists() and p.name != "AGENTS.md":
            rows.append((f"instructions/{p.name}", count_tokens(p.read_text()), ""))

    index, skills = [], sorted((ROOT / "skills").glob("*/SKILL.md"))
    over = []
    for s in skills:
        fm = frontmatter(s.read_text())
        name, desc = fm.get("name", s.parent.name), fm.get("description", "")
        index.append(f"{name}: {desc}")
        if len(desc) > limits.get("skill_description_max", 400):
            over.append(f"{name} ({len(desc)} chars)")
        if not desc:
            warnings.append(f"skill {name!r} has no description — it will never trigger")
    if index:
        rows.append(("skill index", count_tokens("\n".join(index)), f"{len(skills)} skills"))
    if over:
        warnings.append(f"skill descriptions over budget: {', '.join(over)}")

    mcp = conf.get("mcp", {})
    if mcp:
        # Schemas live on the servers, so this is a floor, not the real number.
        # Run `opencode` and check the session token count for the true figure.
        est = 350 * len(mcp)
        rows.append(("MCP servers (est.)", est, f"{len(mcp)} servers"))
        warnings.append(
            f"{len(mcp)} MCP server(s) configured. Every tool schema is prompt weight on "
            "every turn, including turns that will never call it. The estimate above is a "
            "floor — check a real session's token count."
        )
    return rows, warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="never exit non-zero")
    args = ap.parse_args()

    cfg = tomllib.loads((ROOT / "harness.toml").read_text())
    budget = cfg.get("context", {}).get("always_on_token_budget", 6000)
    rows, warnings = collect()
    total = sum(t for _, t, _ in rows)

    width = max((len(n) for n, _, _ in rows), default=10)
    print(f"{'source'.ljust(width)}  {'tokens':>7}   {'share':>6}")
    print("-" * (width + 22))
    for name, tok, note in sorted(rows, key=lambda r: -r[1]):
        share = f"{tok / total:.0%}" if total else "-"
        print(f"{name.ljust(width)}  {tok:>7}   {share:>6}  {note}")
    print("-" * (width + 22))
    print(f"{'TOTAL'.ljust(width)}  {total:>7}   of {budget} budget")

    # A number nobody can feel is a number nobody acts on.
    print(
        f"\nThat is {total} tokens attached to every request. Across 200 requests a day "
        f"that is {total * 200 / 1_000_000:.1f}M input tokens/day before you ask anything."
    )

    for w in warnings:
        print(f"\nwarn: {w}")

    if total > budget:
        print(
            f"\nFAIL: {total} > {budget}. Biggest contributor is "
            f"{max(rows, key=lambda r: r[1])[0]}. Cut it, or raise the budget in "
            "harness.toml with a comment saying why."
        )
        return 0 if args.report else 1
    print(f"\nOK: {budget - total} tokens of headroom.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
