"""Deterministic assertions on a produced artifact. No model involved.

Layer 1, and the layer that actually stops bad merges. If a regex can express
the property, never reach for the judge: free, deterministic, self-explaining.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from . import digest as dg


class Violation(Exception):
    pass


CHECKS: dict[str, Callable[..., list[str]]] = {}


def check(name: str):
    def deco(fn):
        CHECKS[name] = fn
        return fn

    return deco


@check("frontmatter_keys")
def _fm(artifact: str, expected: list[str], **_: Any) -> list[str]:
    have = dg._parse_frontmatter(artifact)
    if not have:
        return ["no YAML frontmatter block found"]
    return [f"missing frontmatter key: {k}" for k in expected if k not in have]


@check("min_sections")
def _minsec(artifact: str, expected: int, **_: Any) -> list[str]:
    n = len([h for h in dg.heading_tree(artifact) if h.startswith("2:")])
    return [] if n >= expected else [f"only {n} level-2 sections, need >= {expected}"]


@check("must_cite")
def _cite(artifact: str, expected: list[str], sources: Any = None, **_: Any) -> list[str]:
    have = set(dg.citations(artifact, sources))
    return [f"required source not cited: {s}" for s in expected if s not in have]


@check("forbidden_patterns")
def _forbid(artifact: str, expected: list[str], **_: Any) -> list[str]:
    return [
        f"forbidden pattern present: {p!r}"
        for p in expected
        if re.search(p, artifact, re.IGNORECASE)
    ]


@check("required_patterns")
def _require(artifact: str, expected: list[str], **_: Any) -> list[str]:
    return [
        f"required pattern absent: {p!r}"
        for p in expected
        if not re.search(p, artifact, re.IGNORECASE)
    ]


@check("no_uncited_claims")
def _uncited(artifact: str, expected: bool = True, **_: Any) -> list[str]:
    """Paragraphs with a number or a quoted span must carry a citation."""
    if not expected:
        return []
    out = []
    body = re.sub(r"\A---\n.*?\n---\n", "", artifact, flags=re.DOTALL)
    body = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    # A year in a heading is not an uncited statistic.
    body = re.sub(r"^#{1,6} .*$", "", body, flags=re.MULTILINE)
    body = re.sub(r"^\[\^[^\]]+\]:.*$", "", body, flags=re.MULTILINE)
    for i, para in enumerate(p for p in re.split(r"\n\s*\n", body) if p.strip()):
        risky = re.search(r"\b\d{2,}(\.\d+)?%?\b", para) or '"' in para
        cited = re.search(r"\[\^|\((?:src|source|cite):|\]\(https?://", para)
        if risky and not cited:
            out.append(f"paragraph {i} makes a quantitative/quoted claim with no citation")
    return out


@check("valid_links")
def _links(artifact: str, expected: bool = True, sources: Any = None, **_: Any) -> list[str]:
    if not expected:
        return []
    refs = set(re.findall(r"\[\^([A-Za-z0-9_.:-]+)\]", artifact))
    defs = set(re.findall(r"^\[\^([A-Za-z0-9_.:-]+)\]:", artifact, re.MULTILINE))
    known = set(dg.citations("", sources))
    return [f"dangling footnote: [^{r}]" for r in sorted(refs - defs - known)]


@check("max_chars")
def _maxchars(artifact: str, expected: int, **_: Any) -> list[str]:
    return [] if len(artifact) <= expected else [f"artifact {len(artifact)} chars > {expected}"]


def run(artifact: str, spec: dict[str, Any], sources: Any = None) -> list[str]:
    failures: list[str] = []
    for name, expected in (spec or {}).items():
        fn = CHECKS.get(name)
        if fn is None:
            raise Violation(f"unknown invariant {name!r}; known: {sorted(CHECKS)}")
        failures += fn(artifact, expected, sources=sources)
    return failures
