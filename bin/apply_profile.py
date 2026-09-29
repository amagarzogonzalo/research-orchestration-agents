#!/usr/bin/env python3
"""Resolve harness.toml + profiles/<name>.toml into opencode.json.

    python bin/apply_profile.py                # re-apply the active profile
    python bin/apply_profile.py frontier       # switch
    python bin/apply_profile.py --list         # what's available
    python bin/apply_profile.py --offline      # skip catalog validation

Why this exists: model IDs go stale, and a stale ID in a config file fails at the
worst moment — mid-task, as an opaque provider error. This validates every ID
against OpenRouter's live catalog before writing, and when one is gone it tells
you what replaced it instead of making you go read a changelog.

The generated block in opencode.json is owned by this script. Edit the profile,
not the output.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = "https://openrouter.ai/api/v1/models"
MANAGED = "_generated_by_apply_profile"


def load_toml(p: Path) -> dict:
    with p.open("rb") as fh:
        return tomllib.load(fh)


def fetch_catalog() -> set[str] | None:
    try:
        with urllib.request.urlopen(CATALOG, timeout=15) as r:
            return {m["id"] for m in json.load(r).get("data", [])}
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as e:
        print(f"warn: could not reach the OpenRouter catalog ({e}); skipping validation")
        return None


def validate(models: dict[str, str], catalog: set[str] | None) -> list[str]:
    if catalog is None:
        return []
    problems = []
    for capability, mid in models.items():
        bare = mid.lstrip("~").split(":")[0]  # strip ~variant prefix and :free/:nitro suffix
        if bare in catalog:
            continue
        near = difflib.get_close_matches(bare, catalog, n=3, cutoff=0.6)
        same_family = sorted(m for m in catalog if m.split("/")[0] == bare.split("/")[0])[:5]
        hint = near or same_family
        problems.append(
            f"{capability}: {mid!r} is not in the OpenRouter catalog."
            + (f" Did you mean: {', '.join(hint)}?" if hint else "")
        )
    return problems


def provider_options(routing: dict) -> dict:
    """OpenRouter provider-routing block, forwarded as providerOptions.openrouter."""
    prov = {
        "sort": routing.get("sort", "throughput"),
        "allow_fallbacks": routing.get("allow_fallbacks", True),
        "require_parameters": routing.get("require_parameters", True),
        "data_collection": routing.get("data_collection", "deny"),
    }
    if routing.get("zdr"):
        prov["zdr"] = True
    if routing.get("ignore"):
        prov["ignore"] = routing["ignore"]
    opts = {"provider": prov}
    # Explicit empty list disables middle-out truncation. Omitting the key is NOT
    # the same thing: OpenRouter applies the transform by default on over-long
    # prompts, quietly deleting the middle of your context.
    opts["transforms"] = routing.get("transforms", [])
    return opts


def build(profile: dict, cfg: dict) -> dict:
    roles, routing = cfg["roles"], cfg.get("routing", {})
    models = profile["models"]
    missing = {r: c for r, c in roles.items() if c not in models}
    if missing:
        raise SystemExit(
            f"profile {profile['name']!r} has no model for capabilities: "
            f"{sorted(set(missing.values()))}"
        )

    opts = provider_options(routing)
    model_block = {mid: {"options": opts} for mid in sorted(set(models.values()))}

    def m(role: str) -> str:
        return f"openrouter/{models[roles[role]]}"

    return {
        MANAGED: {
            "profile": profile["name"],
            "roles": {r: models[c] for r, c in roles.items()},
            "note": "Generated. Edit harness.toml or profiles/, then `make profile`.",
        },
        "model": m("build"),
        "small_model": m("small"),
        "provider": {"openrouter": {"models": model_block}},
        "agent": {
            "plan": {"model": m("plan")},
            "build": {"model": m("build")},
            "critic": {"model": m("review")},
            "scribe": {"model": m("scribe")},
        },
    }


def merge(base: dict, generated: dict) -> dict:
    """Preserve everything hand-written; replace only what this script owns."""
    out = dict(base)
    out[MANAGED] = generated[MANAGED]
    out["model"] = generated["model"]
    out["small_model"] = generated["small_model"]
    out.setdefault("provider", {})["openrouter"] = generated["provider"]["openrouter"]
    agents = out.setdefault("agent", {})
    for name, patch in generated["agent"].items():
        agents.setdefault(name, {}).update(patch)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile", nargs="?", help="profile name; omit to re-apply the active one")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--offline", action="store_true", help="skip catalog validation")
    args = ap.parse_args()

    cfg = load_toml(ROOT / "harness.toml")
    available = sorted(p.stem for p in (ROOT / "profiles").glob("*.toml"))

    if args.list:
        for n in available:
            p = load_toml(ROOT / "profiles" / f"{n}.toml")
            active = " (active)" if n == cfg["profile"]["active"] else ""
            print(f"{n}{active}: {p.get('description', '')}")
            for cap, mid in p["models"].items():
                print(f"    {cap:<10} {mid}")
        return 0

    name = args.profile or cfg["profile"]["active"]
    path = ROOT / "profiles" / f"{name}.toml"
    if not path.exists():
        raise SystemExit(f"no profile {name!r}; available: {', '.join(available)}")
    profile = load_toml(path)

    problems = validate(profile["models"], None if args.offline else fetch_catalog())
    if problems:
        print(f"profile {name!r} is stale:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print(f"\nFix profiles/{name}.toml, or pass --offline to write it anyway.", file=sys.stderr)
        return 1

    cfg_path = ROOT / "opencode.json"
    base = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    cfg_path.write_text(json.dumps(merge(base, build(profile, cfg)), indent=2) + "\n")

    hp = ROOT / "harness.toml"
    hp.write_text(re.sub(r'(?m)^(active\s*=\s*)".*"$', rf'\1"{name}"', hp.read_text()))

    print(f"profile: {name}")
    for role, cap in cfg["roles"].items():
        print(f"  {role:<8} {cap:<10} -> openrouter/{profile['models'][cap]}")
    print("\nRestart opencode — the provider catalog is read at startup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
