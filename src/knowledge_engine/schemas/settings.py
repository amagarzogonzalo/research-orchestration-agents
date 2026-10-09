"""Per-run settings: state-borne overrides validated at the engine boundary (ADR 0016).

``RunSettings`` rides a ``settings`` channel written once by the engine facade
(``engine.research(settings=...)``) and read by nodes with budget-constant
defaults — per-run knobs without code changes, conformant with the pure-node
rule (``(State) -> State``). ``extra="forbid"`` keeps callers from injecting
arbitrary state or model names through the channel.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RunSettings(BaseModel):
    """Per-run override knobs (everything optional = use the constants).

    Attributes:
        model_config: Attribute description.
        max_depth: Deepest decompose depth for this run.
        max_subqueries: Fan-out cap per planning round for this run.
        max_iterations: Judge->synthesize optimizer loop cap for this run.
        wiki_extract: Enable LLM concept/entity extraction at wiki ingest
            (two-step: analysis -> generation). Off = deterministic filing.
        thesis: When set, file a for/against verdict page for this claim into
            ``wiki/synthesis/`` (requires ``wiki_extract``-capable provider).
        max_wiki_pages: Cap on concept/entity pages written per ingest.
    """

    model_config = ConfigDict(extra="forbid")

    max_depth: int | None = Field(default=None, ge=0)
    max_subqueries: int | None = Field(default=None, ge=1)
    max_iterations: int | None = Field(default=None, ge=0)
    wiki_extract: bool | None = None
    thesis: str | None = Field(default=None, max_length=300)
    max_wiki_pages: int | None = Field(default=None, ge=1)


__all__ = ["RunSettings"]
