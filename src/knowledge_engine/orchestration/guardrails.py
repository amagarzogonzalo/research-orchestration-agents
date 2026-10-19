"""Deterministic guardrails for recursive research (no LLM, pure functions).

Three layers, cheapest first: input sanitization (PII-lite redaction before any
model call), shared-budget enforcement (depth/rounds/LLM-call caps so the tree
can never explode), and a rubric sufficiency gate (coverage/diversity/grounding
heuristics decide stop vs. replan — never the model's intuition alone).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

from knowledge_engine.schemas.decomposition import (
    MAX_BRANCH_LLM_CALLS,
    MAX_DECOMPOSE_DEPTH,
    MAX_DEEP_ROUNDS,
    RUBRIC_THRESHOLD,
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_SECRET_RE = re.compile(r"\b(sk-[A-Za-z0-9]{8,}|api[_-]?key\s*[:=]\s*\S+)", re.IGNORECASE)


def sanitize_query(query: str) -> str:
    """Redact emails and key-like secrets before any model call.

    Args:
        query: Raw user input.

    Returns:
        str: Query with PII replaced by placeholders.
    """
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", query)
    return _SECRET_RE.sub("[REDACTED_SECRET]", redacted)


def depth_allowed(depth: int, *, max_depth: int = MAX_DECOMPOSE_DEPTH) -> bool:
    """Return True when a worker may still run at ``depth``.

    Args:
        depth: Proposed worker depth.
        max_depth: Deepest allowed depth.

    Returns:
        bool: True on success.
    """
    return depth <= max_depth


def budget_left(llm_calls: int, max_calls: int = MAX_BRANCH_LLM_CALLS) -> bool:
    """Return True while the shared LLM-call budget is not exhausted.

    Args:
        llm_calls: Calls spent so far (accumulated via the add-reducer).
        max_calls: Shared budget cap.

    Returns:
        bool: True on success.
    """
    return llm_calls < max_calls


def rubric_assess(
    sources: Sequence[object],
    bodies: Sequence[object],
    notes: Sequence[str],
) -> tuple[float, list[str]]:
    """Score evidence sufficiency deterministically (coverage/diversity/grounding).

    Args:
        sources: Source records or ``{"domain": ...}`` mappings.
        bodies: Fetched bodies or ``{"title": ...}`` mappings.
        notes: Synthesized branch notes.

    Returns:
        tuple[float, list[str]]: Score in 0..1 plus human-readable gaps.
    """
    gaps: list[str] = []
    coverage = min(len(bodies) / 3.0, 1.0) * 0.4
    if len(bodies) < 3:
        gaps.append(f"coverage: only {len(bodies)} bodies (need 3)")
    domains = {_domain_of(s) for s in sources} | {_domain_of(b) for b in bodies}
    domains.discard("")
    if len(domains) >= 2:
        diversity = 0.3
    elif len(domains) == 1:
        diversity = 0.15
        gaps.append("diversity: single source domain")
    else:
        diversity = 0.0
        gaps.append("diversity: no source domains")
    if len(notes) >= 2:
        grounding = 0.3
    elif len(notes) == 1:
        grounding = 0.15
        gaps.append("grounding: single branch note")
    else:
        grounding = 0.0
        gaps.append("grounding: no synthesized notes")
    return round(min(1.0, coverage + diversity + grounding), 3), gaps


def should_stop(
    *,
    score: float,
    gaps: Sequence[str],
    depth: int,
    rounds: int,
    llm_calls: int,
    sources: int = 0,
    min_sources: int = 0,
    threshold: float = RUBRIC_THRESHOLD,
) -> bool:
    """Decide stop vs. replan: rubric pass with enough evidence, or budget spent.

    Args:
        score: Rubric score from :func:`rubric_assess`.
        gaps: Open evidence gaps.
        depth: Current plan depth.
        rounds: Replan rounds spent.
        llm_calls: LLM calls spent.
        sources: Merged evidence-source count across branches.
        min_sources: Evidence floor required for a rubric pass (0 disables).
        threshold: Sufficiency threshold.

    Returns:
        bool: True when the deep loop must stop (go to judge).
    """
    if rounds >= MAX_DEEP_ROUNDS:
        return True
    if not budget_left(llm_calls):
        return True
    if depth > MAX_DECOMPOSE_DEPTH:
        return True
    return (score >= threshold or not gaps) and sources >= min_sources


def _domain_of(item: object) -> str:
    """Extract a source domain from a record, body, or plain mapping.

    Args:
        item: Source-like object.

    Returns:
        str: Domain or empty string.
    """
    if isinstance(item, dict):
        if item.get("domain"):
            return str(item["domain"])
        url = str(item.get("url") or "")
        try:
            return urlparse(url).netloc.lower()
        except ValueError:
            return ""
    record: Any = item
    try:
        domain = record.domain
    except AttributeError:
        domain = None
    if domain:
        return str(domain)
    try:
        url = record.url or ""
    except AttributeError:
        url = ""
    try:
        return urlparse(str(url)).netloc.lower()
    except ValueError:
        return ""


__all__ = ["budget_left", "depth_allowed", "rubric_assess", "sanitize_query", "should_stop"]
