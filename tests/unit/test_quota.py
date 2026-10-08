"""Unit tests: MonthlyQuotaLedger (offline, tmp files, injected clock)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from knowledge_engine.core.exceptions import QuotaExhausted
from knowledge_engine.infrastructure.search.quota import MonthlyQuotaLedger


def _clock(month: int) -> object:
    return lambda: datetime(2026, month, 15, tzinfo=UTC)


async def test_records_and_limits(tmp_path) -> None:
    ledger = MonthlyQuotaLedger("tavily", limit=2, root=tmp_path, clock=_clock(10))
    assert await ledger.available() is True
    await ledger.record()
    await ledger.record()
    assert await ledger.available() is False
    with pytest.raises(QuotaExhausted):
        await ledger.check()


async def test_mark_exhausted(tmp_path) -> None:
    ledger = MonthlyQuotaLedger("tavily", limit=1000, root=tmp_path, clock=_clock(10))
    await ledger.record(exhausted=True)
    assert await ledger.available() is False


async def test_month_rollover_resets(tmp_path) -> None:
    ledger = MonthlyQuotaLedger("tavily", limit=2, root=tmp_path, clock=_clock(10))
    await ledger.record()
    await ledger.record()
    assert await ledger.available() is False
    rolled = MonthlyQuotaLedger("tavily", limit=2, root=tmp_path, clock=_clock(11))
    assert await rolled.available() is True


async def test_lifetime_scope_never_resets(tmp_path) -> None:
    ledger = MonthlyQuotaLedger("serper", limit=1, root=tmp_path, scope="lifetime", clock=_clock(10))
    await ledger.record()
    assert await ledger.available() is False
    later = MonthlyQuotaLedger("serper", limit=1, root=tmp_path, scope="lifetime", clock=_clock(12))
    assert await later.available() is False


async def test_persists_across_instances(tmp_path) -> None:
    first = MonthlyQuotaLedger("tavily", limit=5, root=tmp_path, clock=_clock(10))
    await first.record()
    second = MonthlyQuotaLedger("tavily", limit=5, root=tmp_path, clock=_clock(10))
    assert (await second.snapshot())["used"] == 1


async def test_corrupt_ledger_starts_fresh(tmp_path) -> None:
    ledger = MonthlyQuotaLedger("tavily", limit=5, root=tmp_path, clock=_clock(10))
    path = ledger.path_for()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert await ledger.available() is True
    assert (await ledger.snapshot())["used"] == 0
