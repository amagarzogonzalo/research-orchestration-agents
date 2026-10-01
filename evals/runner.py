#!/usr/bin/env python3
"""Eval runner: replay -> assert -> diff -> judge -> report.

    python evals/runner.py              # replay cassettes, offline, free
    python evals/runner.py --record     # live calls, re-record cassettes ($)
    python evals/runner.py --accept     # adopt current digests as snapshots
    python evals/runner.py -k article   # filter by case id

Exit 0 green, 1 regression. Provider-agnostic: any OpenAI-shaped endpoint.
This module is OPTIONAL — delete evals/ if your project is not LLM-driven.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
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
from lib.cassette import Cassette, CassetteMiss

try:
    import yaml
except ImportError:
    sys.exit("pip install pyyaml")


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


def run_case(case: dict[str, Any], mode: str) -> dict[str, Any]:
    cid = case["id"]
    k = int(case.get("samples", 1))
    entry = load_entrypoint(case["entrypoint"])
    rubric_path = case.get("judge", {}).get("rubric")
    rubric = (ROOT / rubric_path).read_text() if rubric_path else None

    runs, failures, notes = [], [], []
    for i in range(k):
        cassette = Cassette(f"{cid}/run{i}", mode=mode)
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
            res = jd.score(
                runs[0]["artifact"],
                rubric,
                lambda **r: cassette.chat(client, **r),
                model,
                samples=int(jcfg.get("samples", 3)),
            )
            ok, msg = res.passes(float(jcfg.get("min_score", 7)))
            (notes if ok else failures).append(f"[{cid}] judge ({model}): {msg}")

    return {
        "id": cid,
        "ok": not failures,
        "failures": failures,
        "notes": notes,
        "digest": current,
        "usage": runs[0]["usage"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
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

    results = [run_case(c, mode) for c in cases]

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
        print(
            f"{'PASS' if r['ok'] else 'FAIL'}  {r['id']}  "
            f"({r.get('usage', {}).get('total_tokens', 0)} tok)"
        )
        for n in r.get("notes", []):
            print(f"      note: {n}")
        for f in r["failures"]:
            print(f"      {f}")
    print(f"\n{len(results) - len(failed)}/{len(results)} cases green")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
