"""Unit tests: record-mode memo (offline, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.lib.cassette import Cassette


@pytest.fixture
def record_tape(tmp_path: Path) -> Cassette:
    """Record-mode cassette rooted at an isolated dir."""
    return Cassette("case/run0", mode="record", directory=tmp_path)


def test_record_reuses_duplicate_chat_call(record_tape: Cassette) -> None:
    """The second identical live call reuses the first result (one client hit)."""
    calls = []

    def client(**request):
        calls.append(request)
        return {"response": "live", "usage": {}}

    first = record_tape.chat(client, model="m", messages=[{"role": "user", "content": "hi"}])
    second = record_tape.chat(client, model="m", messages=[{"role": "user", "content": "hi"}])
    assert first == second == {"response": "live", "usage": {}}
    assert len(calls) == 1


def test_memo_hit_counts_original_usage(record_tape: Cassette) -> None:
    """Memo hits log the original usage so record/replay accounting matches."""

    def client(**request):
        return {"response": "live", "usage": {"total_tokens": 5}}

    record_tape.chat(client, model="m", messages=[])
    record_tape.chat(client, model="m", messages=[])
    assert record_tape.usage["total_tokens"] == 10
    assert record_tape.usage["calls"] == 2


async def test_record_reuses_duplicate_acall(record_tape: Cassette) -> None:
    """Duplicate search/scrape payloads fetch once; both callers decode a copy."""
    calls = []

    async def fetch(url: str) -> dict:
        calls.append(url)
        return {"url": url, "n": 1}

    first = await record_tape.acall("scrape", fetch, {"url": "https://example.org"}, encode=lambda r: r, decode=dict)
    second = await record_tape.acall("scrape", fetch, {"url": "https://example.org"}, encode=lambda r: r, decode=dict)
    assert first == second == {"url": "https://example.org", "n": 1}
    assert calls == ["https://example.org"]


async def test_memo_returns_decoded_copies(record_tape: Cassette) -> None:
    """Mutating one decoded result must not corrupt later reuses."""

    async def fetch(url: str) -> dict:
        return {"url": url, "items": []}

    first = await record_tape.acall("s", fetch, {"url": "u"}, encode=lambda r: r, decode=dict)
    first["items"].append("mutated")
    second = await record_tape.acall("s", fetch, {"url": "u"}, encode=lambda r: r, decode=dict)
    assert second == {"url": "u", "items": []}


def test_shared_memo_spans_instances(tmp_path: Path) -> None:
    """Samples sharing one memo dict never re-fetch each other's calls."""
    memo: dict = {}
    calls = []

    def client(**request):
        calls.append(1)
        return {"response": "live", "usage": {}}

    first = Cassette("case/run0", mode="record", directory=tmp_path, memo=memo)
    second = Cassette("case/run1", mode="record", directory=tmp_path, memo=memo)
    first.chat(client, model="m", messages=[])
    second.chat(client, model="m", messages=[])
    assert len(calls) == 1


def test_replay_ignores_memo(tmp_path: Path) -> None:
    """Replay keeps reading files (memo only affects record mode)."""
    tape = Cassette("case/run0", mode="record", directory=tmp_path)
    tape.chat(lambda **r: {"response": "x", "usage": {}}, model="m", messages=[])
    replay = Cassette("case/run0", mode="replay", directory=tmp_path)
    assert replay.chat(
        lambda **r: (_ for _ in ()).throw(AssertionError("must not go live")), model="m", messages=[]
    ) == {
        "response": "x",
        "usage": {},
    }
