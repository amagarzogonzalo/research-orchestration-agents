"""Unit tests: DeepEval runner wiring (offline, stubbed measure)."""

from __future__ import annotations

import pytest

pytest.importorskip("deepeval", reason="deepeval is an opt-in extra")

from evals import runner
from evals.lib import deepeval_metrics as dm


def test_select_metrics_empty_without_block() -> None:
    """Cases without a deepeval block opt out silently."""
    assert dm.select_metrics({"id": "x"}) == []
    assert dm.select_metrics({"id": "x", "deepeval": {}}) == []


def test_select_metrics_accepts_registry_names() -> None:
    """Configured metric names pass through in order."""
    case = {"id": "x", "deepeval": {"metrics": ["faithfulness", "answer_relevancy"]}}
    assert dm.select_metrics(case) == ["faithfulness", "answer_relevancy"]


def test_select_metrics_rejects_unknown() -> None:
    """Typos fail fast with the registry listed, not at measure time."""
    with pytest.raises(ValueError, match="unknown deepeval metrics"):
        dm.select_metrics({"id": "x", "deepeval": {"metrics": ["nope"]}})


def _stub_measure(names, test_case, model=None) -> list[dict]:
    """Deterministic stand-in for dm.run_selected (no model)."""
    assert model is not None
    assert test_case.input == "q"
    return [{"name": n, "score": 0.9, "threshold": 0.7, "passed": True, "reason": "stub"} for n in names]


def test_deepeval_notes_report_scores() -> None:
    """Stubbed metrics surface as advisory notes, never failures."""
    case = {"id": "research", "deepeval": {"metrics": ["faithfulness"]}}
    notes = runner._deepeval_notes(
        case, "q", "artifact text", [{"title": "t", "abstract": "a"}], None, "m", measure=_stub_measure
    )
    assert notes == ["[research] deepeval faithfulness: 0.90 (threshold 0.7)"]


def test_deepeval_notes_skip_without_block() -> None:
    """No block means no notes and no model construction."""
    assert runner._deepeval_notes({"id": "x"}, "q", "a", [], None, "m", measure=_stub_measure) == []


def test_deepeval_notes_skip_on_measure_error() -> None:
    """Quota/extra failures degrade to a skip note, never a gate failure."""

    def _boom(names, test_case, model=None) -> list[dict]:
        raise RuntimeError("429 throttled")

    case = {"id": "research", "deepeval": {"metrics": ["faithfulness"]}}
    notes = runner._deepeval_notes(case, "q", "a", [], None, "m", measure=_boom)
    assert len(notes) == 1
    assert "deepeval skipped" in notes[0]
