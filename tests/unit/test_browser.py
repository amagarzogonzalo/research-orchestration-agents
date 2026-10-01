"""Unit tests: browser action dispatch + result contract (offline, no browser)."""

from __future__ import annotations

from knowledge_engine.infrastructure.browser import PlaywrightBrowser, SeleniumBrowser
from knowledge_engine.schemas.browser import BrowserActionResult


class FakeBrowserActions:
    """Duck-typed stand-in implementing ``act`` for toolkit/port contract tests."""

    calls: list[str] = []

    async def act(self, action: str, **kwargs):
        self.calls.append(action)
        return BrowserActionResult.success(action, url="https://example.org", text="ok")


async def test_browser_result_success_contract() -> None:
    r = BrowserActionResult.success("navigate", url="https://x.org")
    assert r.ok is True
    assert r.action == "navigate"
    data = r.model_dump(mode="json")
    assert data["ok"] is True


async def test_browser_result_failure_carries_reason() -> None:
    r = BrowserActionResult.failure("fill", "selector missing")
    assert r.ok is False
    assert r.meta["reason"] == "selector missing"


async def test_playwright_constructs_without_importing_driver() -> None:
    # Construction must not import playwright (kept optional in [browser] extra).
    b = PlaywrightBrowser()
    assert b._headless is True


async def test_selenium_constructs_without_importing_driver() -> None:
    s = SeleniumBrowser()
    assert s._driver is None  # driver not created until first action
    assert s._headless is True


async def test_unknown_browser_action_returns_failure() -> None:
    b = PlaywrightBrowser()
    # act() resolves the method name before touching the driver, so an unknown
    # action fails cleanly without launching a browser.
    result = await b.act("does_not_exist")
    assert result.ok is False
    assert "unknown action" in result.meta.get("reason", "")


async def test_playwright_unknown_action_never_launches_driver() -> None:
    # Guard the no-browser invariant: an unknown action must be rejected before
    # Playwright (subprocess) is ever started, keeping unit tests hermetic.
    from unittest.mock import patch

    b = PlaywrightBrowser()
    with patch.object(b, "_ensure_page", side_effect=AssertionError("browser launched")):
        result = await b.act("bogus_action")
        assert result.ok is False
        assert "unknown action" in result.meta.get("reason", "")


async def test_playwright_concurrent_act_calls_serialize() -> None:
    """Concurrent act() calls must not interleave on the shared page."""
    import asyncio

    events: list[str] = []

    class FakeLocator:
        def __init__(self, tag: str) -> None:
            self._tag = tag

        async def inner_text(self) -> str:
            events.append(f"start-{self._tag}")
            await asyncio.sleep(0.01)
            events.append(f"end-{self._tag}")
            return f"text-{self._tag}"

    class FakePage:
        url = "https://example.org"

        def locator(self, selector: str) -> FakeLocator:
            return FakeLocator(selector)

    b = PlaywrightBrowser()
    b._page = FakePage()
    results = await asyncio.gather(*(b.act("extract_text", selector=s) for s in ("a", "b", "c")))
    assert all(r.ok for r in results)
    # serialized: each start is immediately followed by its own end
    assert len(events) == 6
    for start, end in zip(events[::2], events[1::2], strict=True):
        assert start.replace("start-", "") == end.replace("end-", ""), events
