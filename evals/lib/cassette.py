"""Record/replay for model calls.

Solves two problems at once: CI cost (replay is free) and determinism (you cannot
regression-test a graph whose upstream answers move under you). With calls pinned,
an artifact diff is attributable to YOUR change.

The tradeoff, stated plainly: replayed evals test your code, not the model. That
is what the nightly live job is for.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "cassettes"


class CassetteMiss(RuntimeError):
    """Replay hit an unrecorded request. Never silently fall back to a live call:
    that turns a free deterministic suite into a paid flaky one."""


def _key(request: dict[str, Any]) -> str:
    norm = {k: request.get(k) for k in ("model", "messages", "tools", "reasoning", "response_format")}
    return hashlib.sha256(json.dumps(norm, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:24]


class Cassette:
    """mode: 'replay' (default, offline) | 'record' (live, writes) | 'live'."""

    def __init__(self, name: str, mode: str | None = None, directory: Path | None = None):
        self.name = name
        self.mode = mode or os.environ.get("EVAL_MODE", "replay")
        self.dir = (directory or DEFAULT_DIR) / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.calls: list[dict[str, Any]] = []

    def chat(self, client: Any, **request: Any) -> dict[str, Any]:
        k = _key(request)
        path = self.dir / f"{k}.json"

        if self.mode == "replay":
            if not path.exists():
                raise CassetteMiss(
                    f"no recording for {self.name}/{k}\n"
                    f"  model={request.get('model')}\n"
                    f"  first_msg={str(request.get('messages', [{}])[0])[:120]}\n"
                    "Run `make evals-live` to re-record, then review the cassette diff."
                )
            rec = json.loads(path.read_text())
            self.calls.append({"key": k, "replayed": True, "usage": rec.get("usage", {})})
            return rec["response"]

        t0 = time.time()
        response = client(**request)
        elapsed = time.time() - t0
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
        self.calls.append({"key": k, "replayed": False, "usage": usage, "wall_s": elapsed})

        if self.mode == "record":
            path.write_text(
                json.dumps(
                    {
                        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "request": request,
                        "response": response,
                        "usage": usage,
                        "wall_s": round(elapsed, 3),
                    },
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                )
                + "\n"
            )
        return response

    async def achat(self, client: Any, **request: Any) -> dict[str, Any]:
        """Async mirror of :meth:`chat` for ``await``-style live clients.

        Args:
            client: Async callable invoked as ``await client(**request)`` on a miss.
            **request: Request fields (must be JSON-serializable for the key).

        Returns:
            dict[str, Any]: Recorded or replayed response mapping.

        Raises:
            CassetteMiss: In replay mode without a matching recording.
        """
        k = _key(request)
        path = self.dir / f"{k}.json"

        if self.mode == "replay":
            if not path.exists():
                raise CassetteMiss(
                    f"no recording for {self.name}/{k}\n"
                    f"  model={request.get('model')}\n"
                    f"  first_msg={str(request.get('messages', [{}])[0])[:120]}\n"
                    "Run `make evals-live` to re-record, then review the cassette diff."
                )
            rec = json.loads(path.read_text())
            self.calls.append({"key": k, "replayed": True, "usage": rec.get("usage", {})})
            return rec["response"]

        t0 = time.time()
        response = await client(**request)
        elapsed = time.time() - t0
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
        self.calls.append({"key": k, "replayed": False, "usage": usage, "wall_s": elapsed})

        if self.mode == "record":
            path.write_text(
                json.dumps(
                    {
                        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "request": request,
                        "response": response,
                        "usage": usage,
                        "wall_s": round(elapsed, 3),
                    },
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )
        return response

    async def acall(
        self,
        name: str,
        fn: Any,
        payload: dict[str, Any],
        encode: Any,
        decode: Any,
    ) -> Any:
        """Record/replay a non-chat call (search, scrape) with JSON-serializable results.

        Args:
            name: Stable call label namespacing the recording.
            fn: Async callable invoked as ``await fn(**payload)`` on a miss.
            payload: Call arguments (JSON-serializable for the key).
            encode: Maps the live result to JSON-serializable data.
            decode: Rebuilds the result from recorded data.

        Returns:
            Any: Decoded recorded result, or the live result.

        Raises:
            CassetteMiss: In replay mode without a matching recording.
        """
        key = hashlib.sha256(
            json.dumps({"name": name, "payload": payload}, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()[:24]
        path = self.dir / f"{key}.json"

        if self.mode == "replay":
            if not path.exists():
                raise CassetteMiss(
                    f"no recording for {self.name}/{name}/{key}. "
                    "Run `make evals-live` to re-record, then review the cassette diff."
                )
            rec = json.loads(path.read_text())
            self.calls.append({"key": key, "replayed": True, "usage": {}})
            return decode(rec["response"])

        t0 = time.time()
        result = await fn(**payload)
        elapsed = time.time() - t0
        self.calls.append({"key": key, "replayed": False, "usage": {}, "wall_s": elapsed})

        if self.mode == "record":
            path.write_text(
                json.dumps(
                    {
                        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "name": name,
                        "payload": payload,
                        "response": encode(result),
                        "usage": {},
                        "wall_s": round(elapsed, 3),
                    },
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )
        return result

    @property
    def usage(self) -> dict[str, int]:
        total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        for c in self.calls:
            total["calls"] += 1
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                total[k] += int(c.get("usage", {}).get(k, 0) or 0)
        return total
