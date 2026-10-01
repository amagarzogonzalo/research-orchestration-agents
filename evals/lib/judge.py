"""LLM-as-judge — layer 3, only for what layers 1 and 2 cannot express.

Rules this enforces, because an uncalibrated judge is worse than no judge (it
produces numbers):
  * The judge model is PINNED in harness.toml, separate from the model under
    test, and does not follow your active profile. A judge that changes when you
    switch profiles silently rescores your whole history.
  * Anchored rubric, evidence quoted before scoring. Unanchored 1-10 asks return
    a wall of 7s.
  * A calibration set of known-good / known-bad artifacts. Fail it and the
    judge's verdicts this run are discarded rather than trusted.
"""

from __future__ import annotations

import json
import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SYSTEM = """You are a strict evaluator. You score a document against a rubric.

Rules:
- For every criterion, quote a short span from the document as evidence BEFORE scoring.
- If evidence is absent, the score is 1. Absence of evidence is not a middling score.
- Do not reward length, confidence, or polish. Reward only what the rubric names.
- Return ONLY a JSON object, no prose, no markdown fences:
  {"criteria":[{"name":str,"evidence":str,"score":int,"why":str}],"overall":int}
Scores are integers 1-10 and must match the anchors in the rubric."""


@dataclass
class JudgeResult:
    scores: list[float] = field(default_factory=list)
    detail: list[dict[str, Any]] = field(default_factory=list)

    @property
    def median(self) -> float:
        return statistics.median(self.scores) if self.scores else 0.0

    @property
    def spread(self) -> float:
        return (max(self.scores) - min(self.scores)) if len(self.scores) > 1 else 0.0

    def passes(self, threshold: float, max_spread: float = 3.0) -> tuple[bool, str]:
        if not self.scores:
            return False, "judge produced no parseable scores"
        if self.spread > max_spread:
            return False, (
                f"judge disagrees with itself (spread {self.spread}); the rubric "
                "is ambiguous — fix the rubric, do not raise the threshold"
            )
        if self.median < threshold:
            return False, f"median {self.median} < threshold {threshold}"
        return True, f"median {self.median} (spread {self.spread})"


def _parse(text: str) -> dict[str, Any] | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        try:
            return json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            return None


def score(
    artifact: str, rubric: str, call: Callable[..., dict[str, Any]], model: str, samples: int = 3
) -> JudgeResult:
    res = JudgeResult()
    for i in range(samples):
        resp = call(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"# RUBRIC\n{rubric}\n\n# DOCUMENT\n{artifact}"},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            seed=1000 + i,
        )
        parsed = _parse(resp["choices"][0]["message"]["content"])
        if parsed and isinstance(parsed.get("overall"), (int, float)):
            res.scores.append(float(parsed["overall"]))
            res.detail.append(parsed)
    return res


def calibrate(
    calibration_dir: Path,
    rubric: str,
    call: Callable[..., dict[str, Any]],
    model: str,
    tolerance: float = 2.0,
) -> list[str]:
    """Files named `<name>.expect<N>.md`. Run BEFORE trusting any verdict."""
    failures = []
    for path in sorted(calibration_dir.glob("*.expect*.md")):
        m = re.search(r"\.expect(\d+)\.md$", path.name)
        if not m:
            continue
        expected = float(m.group(1))
        got = score(path.read_text(), rubric, call, model, samples=3).median
        if abs(got - expected) > tolerance:
            failures.append(f"{path.name}: judge said {got}, calibration expects ~{expected}")
    return failures
