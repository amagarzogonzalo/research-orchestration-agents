#!/usr/bin/env python3
"""Structural checks on AGENTS.md. Token cost is handled by context_budget.py."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("## Layout", "## Commands", "## Hard rules", "## Definition of done")


def main() -> int:
    p = ROOT / "AGENTS.md"
    if not p.exists():
        print("AGENTS.md missing")
        return 1
    text = p.read_text()
    problems = [f"missing required section: {s}" for s in REQUIRED if s not in text]

    # Comments are guidance for the human filling this in, not context the model
    # is instructed by. Checking them produces false positives on the template's
    # own advice ("no 'you are Claude'").
    body = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    if re.search(r"^\s*```(python|js|ts|go|rust)\b", body, re.MULTILINE):
        problems.append(
            "code sample in AGENTS.md — implementations belong in the repo. "
            "Point at the file instead."
        )
    if re.search(
        r"\b(you are (claude|gpt|gemini|deepseek|qwen)|anthropic|openai)\b", body, re.IGNORECASE
    ):
        problems.append(
            "model- or vendor-specific language in AGENTS.md. This repo switches "
            "models; see .opencode/rules/models.md."
        )
    for ph in ("<PROJECT NAME>", "<entry>", "<One sentence"):
        if ph in body:
            problems.append(f"template placeholder still present: {ph}")

    for x in problems:
        print(f"AGENTS.md: {x}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
