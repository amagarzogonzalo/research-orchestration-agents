#!/usr/bin/env python3
"""Eval runner: replay -> assert -> diff -> judge -> report.

    python evals/runner.py              # replay cassettes, offline, free
    python evals/runner.py --record     # live calls, re-record cassettes ($)
    python evals/runner.py --accept     # adopt current digests as snapshots
    python evals/runner.py --deep       # + DeepEval metrics pass (opt-in extra)
    python evals/runner.py -k article   # filter by case id

Exit 0 green, 1 regression. Provider-agnostic: any OpenAI-shaped endpoint.
This module is OPTIONAL — delete evals/ if your project is not LLM-driven.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT))

from lib import digest as dg
from lib import invariants as inv
from lib import judge as jd
from lib.cassette import DEFAULT_DIR, Cassette, CassetteMiss


def judge_model() -> str:
    """Pinned in harness.toml, deliberately NOT following the active profile."""
    cfg = tomllib.loads((ROOT.parent / "harness.toml").read_text())
    return cfg.get("evals", {}).get("judge_model", "anthropic/claude-sonnet-4.5")


def load_entrypoint(spec: str):
    mod, _, attr = spec.partition(":")
    return getattr(importlib.import_module(mod), attr)


def _client():
    """Live OpenAI-compatible client. Only used in record/live mode."""
    from openai import OpenAI

    c = OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
        default_headers={"HTTP-Referer": "https://localhost", "X-Title": "harness-evals"},
    )

    def call(**request):
        # transforms=[] : never let the provider silently truncate an eval prompt.
        request.setdefault("extra_body", {}).setdefault("transforms", [])
        return c.chat.completions.create(**request).model_dump()

    return call


def _noop_client(**request):
    """Stand-in used in replay mode, where cassette.chat never reaches the network."""
    raise RuntimeError("live judge client used in replay mode — re-record with `make evals-live`")


def _text_of(response: dict[str, Any]) -> str:
    """Extract judge text from a chat-completions response mapping.

    Args:
        response: Raw chat response.

    Returns:
        str: First choice message content.
    """
    return str((((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""))


def _deepeval_notes(
    case: dict[str, Any],
    query: str,
    artifact: str,
    sources: list[dict[str, Any]] | None,
    chat: Any,
    model: str,
    measure: Any | None = None,
) -> list[str]:
    """Run the case's DeepEval metrics as advisory signal (never a failure).

    The opt-in ``evals`` extra may be absent and free-tier judge quota may be
    throttled — either way this reports a note, never a gate failure. Judge
    traffic flows through the same cassette path as the classic judge.

    Args:
        case: Eval case mapping with an optional ``deepeval:`` block.
        query: Case input query.
        artifact: Rendered article text.
        sources: Source records as plain dicts.
        chat: Callable ``(**request) -> response`` (cassette-routed).
        model: Pinned judge model name.
        measure: Override ``(names, test_case, model)`` (tests inject stubs).

    Returns:
        list[str]: Advisory notes, possibly a skip reason.
    """
    from evals.lib import deepeval_metrics as dm

    try:
        names = dm.select_metrics(case)
    except ValueError as exc:
        return [f"[{case.get('id')}] deepeval config error: {exc}"]
    if not names:
        return []
    try:
        from evals.lib.deepeval_judge import PinnedJudge

        def prompt_to_text(prompt: str) -> str:
            """Bridge a bare prompt to the cassette-routed chat client."""
            return _text_of(
                chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                )
            )

        test_case = dm.build_test_case(
            query, artifact, sources or [], expected=(case.get("deepeval") or {}).get("expected")
        )
        judge = PinnedJudge(prompt_to_text, model)
        run = measure or dm.run_selected
        results = run(names, test_case, model=judge)
    except ImportError as exc:
        return [f"[{case.get('id')}] deepeval skipped (opt-in extra not installed): {exc}"]
    except Exception as exc:  # noqa: BLE001 - advisory signal must never fail the gate
        return [f"[{case.get('id')}] deepeval skipped: {type(exc).__name__}: {str(exc)[:120]}"]
    return [f"[{case.get('id')}] deepeval {r['name']}: {r['score']:.2f} (threshold {r['threshold']})" for r in results]


def _citation_yield(artifact: str, sources: list[dict[str, Any]] | None) -> str:
    """Share of retrieved sources actually cited in the artifact footnotes.

    Informational only (precision signal for the relevance loop): junk that
    passes relevance but never gets cited shows up here, not in the gate.

    Args:
        artifact: Rendered article text.
        sources: Source records as plain dicts.

    Returns:
        str: Human-readable "used/total sources cited" summary.
    """
    ids = {s.get("id") for s in (sources or []) if isinstance(s, dict) and s.get("id")}
    used = set(re.findall(r"\[\^([^\]]+)\]", artifact or "")) & ids
    total = len(sources or [])
    return f"{len(used)}/{total} sources cited"


_WEB_SOURCES = frozenset({"wikipedia", "duckduckgo", "tavily", "exa", "serper"})


def _is_web_source(record: dict[str, Any]) -> bool:
    """Whether an eval source dict belongs to the general-web tier.

    Args:
        record: Source record as a plain dict.

    Returns:
        bool: True for Wikipedia/DuckDuckGo/Tavily/Exa/Serper records.
    """
    source = str(record.get("source") or "")
    if source in _WEB_SOURCES:
        return True
    sid = str(record.get("id") or "")
    return sid.startswith(("wikipedia:", "duckduckgo:", "tavily:", "exa:", "serper:"))


def _citation_yield_by_tier(
    artifact: str, sources: list[dict[str, Any]] | None
) -> dict[str, str]:
    """Per-tier citation yield: scholarly vs general-web vs total.

    Splits the retrieval-vs-generation signal per production RAG practice:
    low web yield with web sources retrieved means the synthesis never used
    the web evidence (the #19 gap), not that retrieval failed.

    Args:
        artifact: Rendered article text.
        sources: Source records as plain dicts.

    Returns:
        dict[str, str]: ``{"scholarly": "u/t", "web": "u/t", "total": "u/t"}``.
    """
    seq = [s for s in (sources or []) if isinstance(s, dict) and s.get("id")]
    used = set(re.findall(r"\[\^([^\]]+)\]", artifact or ""))
    scholarly = [s for s in seq if not _is_web_source(s)]
    web = [s for s in seq if _is_web_source(s)]
    used_scholarly = sum(1 for s in scholarly if str(s.get("id")) in used)
    used_web = sum(1 for s in web if str(s.get("id")) in used)
    return {
        "scholarly": f"{used_scholarly}/{len(scholarly)}",
        "web": f"{used_web}/{len(web)}",
        "total": f"{used_scholarly + used_web}/{len(seq)}",
    }


def run_case(case: dict[str, Any], mode: str, deep: bool = False) -> dict[str, Any]:
    cid = case["id"]
    k = int(case.get("samples", 1))
    entry = load_entrypoint(case["entrypoint"])
    rubric_path = case.get("judge", {}).get("rubric")
    rubric = (ROOT / rubric_path).read_text() if rubric_path else None

    runs, failures, notes = [], [], []
    # One memo per case: repeated identical calls across samples reuse the
    # first live result, so files always match the prompts built from them.
    memo: dict[str, Any] = {}
    for i in range(k):
        cassette = Cassette(f"{cid}/run{i}", mode=mode, memo=memo)
        t0 = time.time()
        try:
            out = entry(**case.get("input", {}), cassette=cassette)
        except CassetteMiss as e:
            failures.append(f"[{cid} run{i}] {e}")
            continue
        wall = time.time() - t0
        artifact, sources = out["artifact"], out.get("sources")

        run_fail = inv.run(artifact, case.get("invariants", {}), sources)
        budget, usage = case.get("budget", {}), cassette.usage
        if (mt := budget.get("max_total_tokens")) and usage["total_tokens"] > mt:
            run_fail.append(f"token budget: {usage['total_tokens']} > {mt}")
        if (mc := budget.get("max_model_calls")) and usage["calls"] > mc:
            run_fail.append(f"call budget: {usage['calls']} > {mc}")
        if (mw := budget.get("max_wall_s")) and mode != "replay" and wall > mw:
            run_fail.append(f"latency budget: {wall:.1f}s > {mw}s")

        runs.append(
            {
                "i": i,
                "digest": dg.digest(artifact, sources),
                "usage": usage,
                "wall_s": round(wall, 2),
                "failures": run_fail,
                "artifact": artifact,
                "cassette": cassette,
                "bodies": out.get("bodies", 0),
                "nsources": len(sources or []),
                "sources": [s.model_dump(mode="json") if hasattr(s, "model_dump") else s for s in (sources or [])],
            }
        )

    if not runs:
        return {"id": cid, "ok": False, "failures": failures, "notes": notes}

    if len({r["digest"]["hash"] for r in runs}) > 1:
        notes.append(
            f"NONDETERMINISM: {len({r['digest']['hash'] for r in runs})} distinct output "
            f"shapes across {k} samples. Under replay that means an unpinned source of "
            "randomness in your graph (set iteration, dict order from a thread pool, "
            "time, uuid, retry jitter). Fix that before trusting anything else here."
        )

    clean = [r for r in runs if not r["failures"]]
    need = int(case.get("pass_at_k", k))
    if len(clean) < need:
        for r in runs:
            failures += [f"[{cid} run{r['i']}] {f}" for f in r["failures"]]
        failures.append(f"[{cid}] {len(clean)}/{k} clean runs, need {need}")

    snap = ROOT / "snapshots" / f"{cid}.json"
    current = runs[0]["digest"]
    if snap.exists():
        changes = dg.diff(json.loads(snap.read_text()), current)
        failures += [f"[{cid}] {c}" for c in changes if c.startswith("BREAKING")]
        notes += [f"[{cid}] {c}" for c in changes if not c.startswith("BREAKING")]
    else:
        notes.append(f"[{cid}] no snapshot yet — run with --accept to create one")

    jcfg = case.get("judge")
    judge_criteria: list[dict[str, Any]] = []
    if jcfg and rubric:
        cassette, model = runs[0]["cassette"], judge_model()
        # Replay mode never calls the network (cassette.chat replays judge outputs),
        # so `make evals` must work with no key. Build the live client only when
        # actually recording, so a keyless clone can run the offline suite.
        client = _client() if mode == "record" else _noop_client
        calib = ROOT / "calibration"
        if calib.exists() and any(calib.glob("*.expect*.md")):
            bad = jd.calibrate(calib, rubric, lambda **r: cassette.chat(client, **r), model)
            if bad:
                notes.append(f"[{cid}] JUDGE UNCALIBRATED, verdict discarded: {bad}")
                jcfg = None
        if jcfg:
            try:
                res = jd.score(
                    runs[0]["artifact"],
                    rubric,
                    lambda **r: cassette.chat(client, **r),
                    model,
                    samples=int(jcfg.get("samples", 3)),
                )
            except CassetteMiss as exc:
                failures.append(f"[{cid}] judge recording missing: {exc}")
                res = None
            if res is not None:
                ok, msg = res.passes(float(jcfg.get("min_score", 7)))
                for sample in res.detail:
                    for criterion in sample.get("criteria", []):
                        judge_criteria.append(
                            {
                                "name": criterion.get("name", "?"),
                                "score": criterion.get("score", 0),
                                "why": str(criterion.get("why", ""))[:160],
                            }
                        )
                    break  # first sample is representative; spread is in msg
            else:
                ok, msg = False, "no verdict"
            if jcfg.get("advisory"):
                # Noisy free-tier judges report signal but never block the gate;
                # deterministic layers (invariants/budgets/snapshots) are the gate.
                notes.append(f"[{cid}] judge ({model}) advisory: {msg}")
            else:
                (notes if ok else failures).append(f"[{cid}] judge ({model}): {msg}")

    if deep:
        # Opt-in DeepEval pass (default `make evals` skips this: separate
        # cassette traffic, calibrated separately). Advisory until the
        # thresholds below are calibrated against live runs.
        first = runs[0]
        cassette, model = first["cassette"], judge_model()
        client = _client() if mode == "record" else _noop_client
        notes += _deepeval_notes(
            case,
            str(case.get("input", {}).get("query", "")),
            first["artifact"],
            first.get("sources"),
            lambda **r: cassette.chat(client, **r),
            model,
        )

    first = runs[0]
    notes.append(f"[{cid}] citation yield: {_citation_yield(first['artifact'], first.get('sources'))}")
    by_tier = _citation_yield_by_tier(first["artifact"], first.get("sources"))
    notes.append(
        f"[{cid}] citation yield by tier: scholarly {by_tier['scholarly']}, "
        f"web {by_tier['web']}, total {by_tier['total']}"
    )

    return {
        "id": cid,
        "ok": not failures,
        "failures": failures,
        "notes": notes,
        "digest": current,
        "usage": runs[0]["usage"],
        "budget": case.get("budget", {}),
        "artifact_chars": len(runs[0]["artifact"]),
        "judge_criteria": judge_criteria,
        "bodies": runs[0].get("bodies", 0),
        "nsources": runs[0].get("nsources", 0),
    }


def record_dir(cid: str, i: int) -> Path:
    """Cassette directory for one case run.

    Args:
        cid: Case id.
        i: Sample index.

    Returns:
        Path: Directory holding that run's recordings.
    """
    return DEFAULT_DIR / cid / f"run{i}"


def find_dirty_record_dirs(cases: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Map case ids to non-empty cassette dirs that a record would overwrite.

    Record mode overwrites on success but preserves files for calls that fail
    live — a later replay then hits those stale files and diverges from the
    recorded run. Recording into a dirty dir is the trap; wipe first.

    Args:
        cases: Eval case mappings (each with ``id`` and ``samples``).

    Returns:
        dict[str, list[str]]: Case id to sorted non-empty run-dir names.
    """
    dirty: dict[str, list[str]] = {}
    for case in cases:
        cid = str(case["id"])
        runs = int(case.get("samples", 1))
        hit = sorted(
            f"run{i}" for i in range(runs) if record_dir(cid, i).exists() and any(record_dir(cid, i).iterdir())
        )
        if hit:
            dirty[cid] = hit
    return dirty


def main() -> int:
    try:
        import yaml
    except ImportError:
        sys.exit("pip install pyyaml")
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--accept", action="store_true")
    ap.add_argument("-k", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    mode = "record" if args.record else "replay"
    cases = [yaml.safe_load(p.read_text()) for p in sorted((ROOT / "cases").glob("*.yaml"))]
    cases = [c for c in cases if args.k in c["id"]]
    if not cases:
        print("no cases matched")
        return 1

    if mode == "record" and not args.force and (dirty := find_dirty_record_dirs(cases)):
        print("refusing to record into non-empty cassette dirs (stale files make replay diverge):")
        for cid, runs in dirty.items():
            print(f"  {cid}: {', '.join(runs)}")
        print("wipe them first, or re-run with --force to overwrite anyway.")
        return 2

    results = [run_case(c, mode, deep=args.deep) for c in cases]

    if args.accept:
        (ROOT / "snapshots").mkdir(exist_ok=True)
        for r in results:
            if "digest" in r:
                (ROOT / "snapshots" / f"{r['id']}.json").write_text(
                    json.dumps(r["digest"], indent=2, sort_keys=True) + "\n"
                )
        print(f"accepted {len(results)} snapshots — commit them with the change that caused them")
        return 0

    if args.json:
        print(json.dumps(results, indent=2, default=str))

    failed = [r for r in results if not r["ok"]]
    for r in results:
        usage = r.get("usage", {})
        budget = r.get("budget", {})
        digest = r.get("digest", {})
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['id']}  ({usage.get('total_tokens', 0)} tok)")
        print(
            f"      cost: {usage.get('total_tokens', 0)}"
            f"/{budget.get('max_total_tokens', '?')} tok, "
            f"{usage.get('calls', 0)}/{budget.get('max_model_calls', '?')} calls"
        )
        print(
            f"      artifact: {r.get('artifact_chars', 0)} chars, "
            f"{len(digest.get('headings', []))} sections, "
            f"{len(digest.get('citations', []))} citations"
        )
        bodies, nsources = r.get("bodies", 0), r.get("nsources", 0)
        if nsources and bodies:
            print(f"      evidence: {bodies}/{nsources} bodies fetched ({bodies * 100 // nsources}% hit rate)")

        for criterion in r.get("judge_criteria", []):
            print(f"      judge {criterion['name']}: {criterion['score']} — {criterion['why']}")
        for n in r.get("notes", []):
            print(f"      note: {n}")
        for f in r["failures"]:
            print(f"      {f}")
    print(f"\n{len(results) - len(failed)}/{len(results)} cases green")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
