"""Semantic shape digest.

Diffing raw LLM prose does not work: output varies every run, the diff is always
red, you stop reading it. Diff a structural projection instead — the things that
must stay stable, with the thing allowed to vary (wording) removed.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_FM = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_FENCE = re.compile(r"^```([A-Za-z0-9_+-]*)", re.MULTILINE)
_LINK = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")
_FOOTNOTE = re.compile(r"\[\^([A-Za-z0-9_.:-]+)\]")
_INLINE_CITE = re.compile(r"\((?:src|source|cite):([A-Za-z0-9_.:-]+)\)")
_NUM = re.compile(r"\d[\d,._]*")


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Key -> type. Values discarded; the contract is the shape, not the content."""
    m = _FM.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line or line.startswith((" ", "\t", "-")):
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        kind = (
            "list"
            if raw.startswith("[")
            else "empty"
            if not raw
            else "number"
            if _NUM.fullmatch(raw)
            else "bool"
            if raw.lower() in {"true", "false"}
            else "str"
        )
        out[key.strip()] = kind
    return out


def heading_tree(text: str) -> list[str]:
    return [
        f"{len(h)}:{re.sub(r'[^a-z0-9]+', '-', t.lower()).strip('-')}"
        for h, t in _HEADING.findall(text)
    ]


def citations(text: str, sources: Any = None) -> list[str]:
    ids = set(_FOOTNOTE.findall(text)) | set(_INLINE_CITE.findall(text))
    for url in _LINK.findall(text):
        ids.add("host:" + re.sub(r"^https?://(www\.)?", "", url).split("/")[0])
    seq = sources.get("sources", []) if isinstance(sources, dict) else (sources or [])
    for s in seq:
        if isinstance(s, dict) and (sid := s.get("id")):
            ids.add(str(sid))
    return sorted(ids)


def _bucket(n: int) -> str:
    for hi in (250, 500, 1000, 2000, 4000, 8000):
        if n < hi:
            return f"<{hi}"
    return ">=8000"


def digest(artifact: str, sources: Any = None) -> dict[str, Any]:
    body = _FM.sub("", artifact)
    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    d: dict[str, Any] = {
        "frontmatter": _parse_frontmatter(artifact),
        "headings": heading_tree(artifact),
        "citations": citations(artifact, sources),
        "code_langs": sorted(set(_FENCE.findall(body))),
        "counts": {
            "paragraphs": len(paragraphs),
            "words_bucket": _bucket(len(body.split())),
            "list_items": len(re.findall(r"^\s*[-*+]\s+", body, re.MULTILINE)),
            "tables": len(re.findall(r"^\|.+\|$", body, re.MULTILINE)),
        },
    }
    d["hash"] = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]
    return d


def diff(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Ordered by how much you should care. BREAKING fails the build."""
    out: list[str] = []
    ofm, nfm = old.get("frontmatter", {}), new.get("frontmatter", {})
    out += [f"BREAKING frontmatter key removed: {k}" for k in sorted(set(ofm) - set(nfm))]
    out += [f"frontmatter key added: {k}" for k in sorted(set(nfm) - set(ofm))]
    out += [
        f"BREAKING frontmatter type changed: {k}: {ofm[k]} -> {nfm[k]}"
        for k in sorted(set(ofm) & set(nfm))
        if ofm[k] != nfm[k]
    ]

    oc, nc = set(old.get("citations", [])), set(new.get("citations", []))
    out += [f"BREAKING citation dropped: {c}" for c in sorted(oc - nc)]
    out += [f"citation added: {c}" for c in sorted(nc - oc)]

    oh, nh = old.get("headings", []), new.get("headings", [])
    if oh != nh:
        removed, added = sorted(set(oh) - set(nh)), sorted(set(nh) - set(oh))
        if removed:
            out.append(f"BREAKING sections removed: {', '.join(removed)}")
        if added:
            out.append(f"sections added: {', '.join(added)}")
        if not removed and not added:
            out.append("section order changed")

    for k in ("paragraphs", "words_bucket", "list_items", "tables"):
        o, n = old.get("counts", {}).get(k), new.get("counts", {}).get(k)
        if o != n:
            out.append(f"counts.{k}: {o} -> {n}")
    return out
