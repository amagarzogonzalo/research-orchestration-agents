"""SourceCleaner — tiered dedup over federated sources (ADR wiki-loop0).

Deterministic, stdlib-only, close-never-delete:

1. **Exact** — same canonical id drops (DOI dedup is opt-in via
   ``dedup_doi``; Loop 2 flips it after measuring eval impact).
2. **Near** — MinHash-style signatures over word shingles + LSH banding for
   candidates + union-find transitive clustering at the merge threshold.
3. **Containment** — shingle-overlap coefficient with a length-ratio guard
   (parent review supersedes a contained child chunk).

Dropped records are reported as ``superseded_by`` pointers; nothing is
deleted, so history stays retrievable (valid/belief-time rendering is Loop 2).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from knowledge_engine.core.ports import CleanPort
from knowledge_engine.schemas.clean import CleanResult
from knowledge_engine.schemas.source import SourceRecord

_NUM_PERM = 64
_BAND_ROWS = 8
_SHINGLE = 3
_CANDIDATE = 0.6
_MERGE = 0.8
_CONTAIN = 0.9
_LENGTH_RATIO = 1.5
_ALL_PAIRS_LIMIT = 64


def _shingles(text: str) -> set[str]:
    """Word k-shingles of a normalized text.

    Args:
        text: Title + abstract text.

    Returns:
        set[str]: Shingle set (empty for very short texts).
    """
    words = "".join(c.lower() if c.isalnum() else " " for c in text).split()
    if len(words) < _SHINGLE:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + _SHINGLE]) for i in range(len(words) - _SHINGLE + 1)}


def _record_shingles(rec: SourceRecord) -> set[str]:
    """Shingle set of one record: title and abstract shingled separately.

    Shingling the concatenation would create wraparound shingles across the
    title/abstract boundary that no other record shares, breaking containment
    math; per-field union avoids that artifact.

    Args:
        rec: Source record.

    Returns:
        set[str]: Union of title + abstract shingles.
    """
    return _shingles(rec.title) | _shingles(rec.abstract or "")


def _signature(shingles: set[str]) -> tuple[int, ...]:
    """MinHash-style signature: min blake2b hash per permutation seed.

    Args:
        shingles: Shingle set.

    Returns:
        tuple[int, ...]: Signature (num_perm ints; 0-entries stay 0).
    """
    if not shingles:
        return tuple([0] * _NUM_PERM)
    sig: list[int] = []
    for seed in range(_NUM_PERM):
        hashes = (
            int.from_bytes(
                hashlib.blake2b(s.encode("utf-8"), digest_size=8, person=str(seed).encode()).digest(),
                "big",
            )
            for s in shingles
        )
        sig.append(min(hashes))
    return tuple(sig)


def _jaccard(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    """Estimated Jaccard similarity from two signatures.

    Args:
        a: First signature.
        b: Second signature.

    Returns:
        float: Fraction of matching permutation minima.
    """
    if not a or not b:
        return 0.0
    same = sum(1 for x, y in zip(a, b, strict=True) if x == y)
    return same / len(a)


class _UnionFind:
    """Minimal union-find over string ids (transitive duplicate clusters)."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        """Return the set representative for x.

        Args:
            x: Element id.

        Returns:
            str: Representative id.
        """
        self._parent.setdefault(x, x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        """Merge the sets of a and b.

        Args:
            a: First element id.
            b: Second element id.
        """
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


class SourceCleaner(CleanPort):
    """Tiered dedup adapter implementing :class:`CleanPort`.

    Args:
        dedup_doi: Also drop same-DOI records with different ids. Off by
            default in Loop 0 (eval replay safety); Loop 2 measures then flips.
        near_dedup: Enable the MinHash + LSH + union-find pass and the
            containment check. Off by default in Loop 0; Loop 2 measures
            recall/precision on the real corpus, then enables.
    """

    def __init__(self, *, dedup_doi: bool = False, near_dedup: bool = False) -> None:
        self._dedup_doi = dedup_doi
        self._near_dedup = near_dedup

    async def clean(self, sources: list[SourceRecord]) -> CleanResult:
        """Clean one batch of source records.

        Args:
            sources: Raw federated sources for the run.

        Returns:
            CleanResult: Kept records (original order) + superseded_by map.
        """
        kept: list[SourceRecord] = []
        superseded: dict[str, str] = {}
        exact = 0
        seen_id: dict[str, str] = {}
        seen_doi: dict[str, str] = {}

        for rec in sources:
            keeper = seen_id.get(rec.id)
            if keeper is None and self._dedup_doi and rec.doi:
                keeper = seen_doi.get(rec.doi)
            if keeper is not None:
                exact += 1
                superseded[rec.id] = keeper
                continue
            seen_id[rec.id] = rec.id
            if rec.doi:
                seen_doi.setdefault(rec.doi, rec.id)
            kept.append(rec)

        near = 0
        contained = 0
        clusters = 0
        if self._near_dedup and len(kept) > 1:
            # Exact shingle sets (deterministic; cheap for research-sized
            # batches). LSH banding only narrows candidates when the batch is
            # large; containment pairs have LOW signature-Jaccard (long unique
            # tails), so all-pairs is required below the LSH cutoff.
            sets = {r.id: _record_shingles(r) for r in kept}
            if len(kept) <= _ALL_PAIRS_LIMIT:
                # All pairs: containment pairs have LOW Jaccard (long unique
                # tails), so no similarity prefilter here — the loop below
                # decides merge vs containment per pair.
                pairs = [(a.id, b.id) for i, a in enumerate(kept) for b in kept[i + 1 :]]
            else:
                pairs = _candidates(kept)
            uf = _UnionFind()
            for a, b in pairs:
                if a in superseded or b in superseded:
                    continue
                inter = len(sets[a] & sets[b])
                union = len(sets[a] | sets[b])
                sim = inter / union if union else 0.0
                min_size = min(len(sets[a]), len(sets[b]))
                len_ratio = max(len(sets[a]), len(sets[b])) / max(1, min_size)
                if sim >= _MERGE:
                    uf.union(a, b)
                elif min_size > 0 and len_ratio >= _LENGTH_RATIO and inter / min_size >= _CONTAIN:
                    shorter, longer = (a, b) if len(sets[a]) <= len(sets[b]) else (b, a)
                    superseded[shorter] = longer
                    contained += 1

            groups: dict[str, list[str]] = {}
            for rid in {r.id for r in kept} - set(superseded):
                groups.setdefault(uf.find(rid), []).append(rid)
            for members in groups.values():
                if len(members) < 2:
                    continue
                clusters += 1
                keeper = sorted(members)[0]
                for member in sorted(members)[1:]:
                    superseded[member] = keeper
                    near += 1
            kept = [r for r in kept if r.id not in superseded]

        return CleanResult(
            kept=kept,
            superseded_by=superseded,
            exact_drops=exact,
            near_drops=near,
            containment_drops=contained,
            clusters=clusters,
        )


def _candidates(records: Iterable[SourceRecord]) -> list[tuple[str, str]]:
    """LSH candidate pairs: matching band -> candidate (with jaccard filter).

    Args:
        records: Kept records so far.

    Returns:
        list[tuple[str, str]]: Candidate id pairs.
    """
    ids = [r.id for r in records]
    sigs = {r.id: _signature(_record_shingles(r)) for r in records}
    bands: dict[tuple[int, ...], list[str]] = {}
    for rid, sig in sigs.items():
        for start in range(0, _NUM_PERM, _BAND_ROWS):
            bands.setdefault(sig[start : start + _BAND_ROWS], []).append(rid)
    pairs: set[tuple[str, str]] = set()
    for bucket in bands.values():
        for i, a in enumerate(bucket):
            for b in bucket[i + 1 :]:
                if _jaccard(sigs[a], sigs[b]) >= _CANDIDATE:
                    pairs.add((a, b) if a < b else (b, a))
    # Only pairs among records that still exist.
    alive = set(ids)
    return [(a, b) for (a, b) in sorted(pairs) if a in alive and b in alive]


__all__ = ["SourceCleaner"]
