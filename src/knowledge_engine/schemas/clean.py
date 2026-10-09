"""Cleaner schemas: dedup outcomes over federated sources (ADR wiki-loop0).

The cleaner is deterministic and stdlib-only: exact id/DOI/URL drops, a
MinHash-style near-duplicate pass with LSH banding + union-find clustering,
and a shingle-containment check for parent/child pairs. Nothing is deleted:
dropped records are reported as ``superseded_by`` pointers so history stays
retrievable (close-never-delete).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from knowledge_engine.schemas.source import SourceRecord


class CleanResult(BaseModel):
    """Outcome of cleaning one batch of source records.

    Attributes:
        model_config: Forbid extra keys.
        kept: Records that survive cleaning, in original order.
        superseded_by: Map of dropped record id -> surviving record id.
        exact_drops: Count of exact (id/doi/url) duplicates dropped.
        near_drops: Count of near-duplicates dropped by MinHash clustering.
        containment_drops: Count of records contained in a longer record.
        clusters: Number of transitive duplicate clusters found.
    """

    model_config = ConfigDict(extra="forbid")

    kept: list[SourceRecord] = Field(default_factory=list)
    superseded_by: dict[str, str] = Field(default_factory=dict)
    exact_drops: int = 0
    near_drops: int = 0
    containment_drops: int = 0
    clusters: int = 0

    @property
    def dropped(self) -> int:
        """Return the total number of dropped records.

        Returns:
            int: Exact + near + containment drops.
        """
        return self.exact_drops + self.near_drops + self.containment_drops


__all__ = ["CleanResult"]
