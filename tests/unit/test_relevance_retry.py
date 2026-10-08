"""Unit tests: relevance retries empty/unparseable replies once (offline)."""

from __future__ import annotations

from knowledge_engine.orchestration.nodes.relevance import RelevanceNode
from knowledge_engine.schemas.provider import ChatRequest
from knowledge_engine.schemas.source import SearchSource, SourceRecord


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Res:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)


class _StubProvider:
    """Provider stub replaying canned contents in order, counting calls."""

    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.calls = 0

    async def complete(self, request: ChatRequest) -> _Res:
        """Return the next canned reply."""
        content = self.contents[min(self.calls, len(self.contents) - 1)]
        self.calls += 1
        return _Res(content)


def _src(sid: str) -> SourceRecord:
    return SourceRecord(id=sid, title=f"Paper {sid}", source=SearchSource.ARXIV)


def _state(provider: _StubProvider) -> tuple[RelevanceNode, dict]:
    node = RelevanceNode(provider)  # type: ignore[arg-type]
    state: dict = {"query": "rag", "sources": [_src("a:1"), _src("b:2")]}
    return node, state


async def test_empty_reply_keeps_batch_without_retry() -> None:
    """Empty replies keep the batch on the single attempt (no wasteful retry)."""
    provider = _StubProvider([""])
    node, state = _state(provider)
    out = await node.run(state)  # type: ignore[arg-type]
    assert [s.id for s in out["filtered"]] == ["a:1", "b:2"]
    assert provider.calls == 1


async def test_valid_verdict_filters() -> None:
    """A parseable verdict filters the batch normally."""
    provider = _StubProvider(['{"relevant_ids": ["a:1"]}'])
    node, state = _state(provider)
    out = await node.run(state)  # type: ignore[arg-type]
    assert [s.id for s in out["filtered"]] == ["a:1"]
    assert provider.calls == 1


async def test_large_corpus_batches_and_unions() -> None:
    """Ledgers over the batch size split into independent verdict calls."""
    from knowledge_engine.orchestration.nodes import relevance as rel

    sources = [_src(f"p:{i}") for i in range(rel._RELEVANCE_BATCH + 5)]
    last = rel._RELEVANCE_BATCH
    provider = _StubProvider(
        [
            '{"relevant_ids": ["p:0"]}',
            f'{{"relevant_ids": ["p:{last}"]}}',
        ]
    )
    node = RelevanceNode(provider)  # type: ignore[arg-type]
    out = await node.run({"query": "rag", "sources": sources})  # type: ignore[dict-item]
    assert [s.id for s in out["filtered"]] == ["p:0", f"p:{last}"]
    assert provider.calls == 2


async def test_failed_batch_keeps_only_its_slice() -> None:
    """A dead batch degrades locally; healthy batches still filter."""
    from knowledge_engine.orchestration.nodes import relevance as rel

    batch = rel._RELEVANCE_BATCH
    sources = [_src(f"p:{i}") for i in range(batch + 2)]
    provider = _StubProvider(['{"relevant_ids": []}', ""])
    node = RelevanceNode(provider)  # type: ignore[arg-type]
    out = await node.run({"query": "rag", "sources": sources})  # type: ignore[dict-item]
    assert [s.id for s in out["filtered"]] == [f"p:{batch}", f"p:{batch + 1}"]
    assert provider.calls == 2


def test_ledger_lines_carry_ids_without_indices() -> None:
    """Ledger must not show numeric indices (models echo them back unusably)."""
    from knowledge_engine.orchestration.nodes import relevance as rel

    line = rel._ledger_line(_src("openalex:W1"))
    assert line.startswith("openalex:W1 | ")
    assert "[0]" not in line and "[1]" not in line


async def test_user_prompt_labels_sources_not_papers() -> None:
    """The verdict prompt must not mislabel web evidence as papers.

    The system prompt grants practitioner/web sources first-class status for
    practical questions, but user text saying ``Papers:`` / ``Relevant
    papers`` contradicts it — weaker models follow the nearer label and drop
    the web tier before it can ever be scraped or cited.
    """
    captured: list[str] = []

    class _Capturing(_StubProvider):
        async def complete(self, request: ChatRequest) -> _Res:
            captured.append(request.messages[-1].content)
            return await super().complete(request)

    provider = _Capturing(['{"relevant_ids": ["a:1"]}'])
    node, state = _state(provider)
    await node.run(state)  # type: ignore[arg-type]
    user = captured[0]
    assert "Sources (papers and web pages):" in user
    assert "Relevant papers" not in user
