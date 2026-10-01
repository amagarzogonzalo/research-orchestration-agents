"""Self-test graph so `make evals` is green on a fresh clone with no API key.

Delete this and evals/cases/_demo.yaml once your real entrypoint is wired.
It exists so you can verify the harness works before trusting it with your code.
"""

from __future__ import annotations


def run(query: str, cassette=None, **_):
    sources = {"sources": [{"id": "example.org"}, {"id": "example.net"}]}
    artifact = f"""---
title: {query}
date: 2026-01-01
sources: [example.org, example.net]
query: {query}
---

# {query}

## Background
The relevant rule was revised in two stages (src:example.org).

## What changed
The threshold rose to 73% by 2028 (src:example.net).

## Open questions
Enforcement timelines are unpublished; the implementing act is pending.
"""
    return {"artifact": artifact, "sources": sources, "events": []}
