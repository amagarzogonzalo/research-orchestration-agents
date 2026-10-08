"""Golden master: SynthesizeNode._build_prompt bytes are pinned (W3).

Cassette keys hash prompt bytes (``evals/lib/cassette.py``), so this test is
the tripwire that any evidence-formatting change fails HERE first — not as a
late ``CassetteMiss`` during eval replay. Regenerate fixtures ONLY for
deliberate changes: ``uv run python bin/golden_fixtures.py`` (then follow the
documented eval re-record loop).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from knowledge_engine.orchestration.nodes.synthesize import SynthesizeNode
from knowledge_engine.orchestration.state import WorkflowState
from knowledge_engine.schemas.source import SourceRecord

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden"


def _fixtures() -> list[dict[str, Any]]:
    """Load the committed golden fixtures, sorted by name for stable ids.

    Returns:
        list[dict[str, Any]]: Parsed fixture mappings.
    """
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(GOLDEN.glob("*.json"))]


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda fixture: fixture["name"])
def test_synthesize_prompt_bytes_are_pinned(fixture: dict[str, Any]) -> None:
    """Evidence formatting is byte-stable (cassette keys hash these bytes)."""
    state = cast("WorkflowState", dict(fixture["state"]))
    if fixture["use_object_sources"]:
        state["sources"] = [SourceRecord.model_validate(record) for record in fixture["state"]["sources"]]
        if fixture["state"].get("filtered"):
            state["filtered"] = [SourceRecord.model_validate(record) for record in fixture["state"]["filtered"]]
    assert SynthesizeNode._build_prompt(state, fixture["critique"]) == fixture["expected_prompt"]
