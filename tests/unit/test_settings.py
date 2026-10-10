"""RunSettings — the per-run overrides contract (W6, ADR 0016).

The settings channel is validated ONCE at the engine boundary (extra="forbid"
— a closed surface: callers cannot inject arbitrary state through settings)
and read with budget-constant defaults inside nodes (pure-node conformant).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from knowledge_engine.schemas.settings import RunSettings


def test_unknown_keys_rejected() -> None:
    """extra=forbid: settings cannot smuggle arbitrary state or model names."""
    with pytest.raises(ValidationError):
        RunSettings(model="gpt-4o")  # type: ignore[call-arg]


def test_all_fields_optional() -> None:
    """An empty RunSettings means 'use the budget constants'."""
    settings = RunSettings()
    assert settings.max_depth is None
    assert settings.max_subqueries is None
    assert settings.max_iterations is None


def test_bounds_validated() -> None:
    """Negative or nonsensical overrides are rejected at the boundary."""
    with pytest.raises(ValidationError):
        RunSettings(max_depth=-1)
    with pytest.raises(ValidationError):
        RunSettings(max_subqueries=0)


def test_model_dump_roundtrip() -> None:
    """The engine writes settings.model_dump() into the channel."""
    settings = RunSettings(
        max_subqueries=2, wiki_extract=True, wiki_save_answers=True, thesis="claim", max_wiki_pages=5
    )
    assert settings.model_dump() == {
        "max_depth": None,
        "max_subqueries": 2,
        "max_iterations": None,
        "wiki_extract": True,
        "wiki_save_answers": True,
        "thesis": "claim",
        "max_wiki_pages": 5,
    }
