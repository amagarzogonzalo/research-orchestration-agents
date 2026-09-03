"""Event-driven stations (ADR 0007).

Each station consumes a single inbox stream and emits successor events carrying
the (optionally mutated) saga ``GraphState``. Stations are pure and replayable:
they read state from the envelope, use pipeline's ports, and emit the next hop.
"""

from __future__ import annotations

import asyncio
import logging

from knowledge_engine.core.events import (
    EVT_BODIES_READY,
    EVT_COMPLETED,
    EVT_RENDER_REQUESTED,
    EVT_SOURCES_FILTERED,
    EVT_SOURCES_RESOLVED,
    EVT_SYNTHESIS_DONE,
    EventOut,
    STREAM_BODIES_READY,
    STREAM_COMPLETED,
    STREAM_JUDGED,
    STREAM_RENDER,
    STREAM_REVIEW,
    STREAM_SOURCES_FILTERED,
    STREAM_SOURCES_RESOLVED,
)
from knowledge_engine.infrastructure.broker import Envelope
from knowledge_engine.schemas.extraction import GraphState, GraphStatus
from knowledge_engine.schemas.provider import Affinity, ChatRequest, ChatResult
from knowledge_engine.schemas.source import DocumentBody, SourceRecord
from knowledge_engine.orchestration.event_pipeline import EventPipeline, decode_state

logger = logging.getLogger(__name__)

MAX_EVAL_ITERATIONS = 2


class IngestStation:
    name = "ingest"
    in_stream = "research.inbox"

    async def handle(self, pipeline: EventPipeline, envelope: Envelope) -> list[EventOut]:
        state = decode_state(envelope)
        sources = await pipeline.search.search(state.query, limit=10)
        ns = state.model_copy(update={"sources": sources})
        ns.status = GraphStatus.RUNNING
        await pipeline.checkpoint(ns)
        return [EventOut(STREAM_SOURCES_RESOLVED, EVT_SOURCES_RESOLVED, {"state": ns.model_dump(), "thread_id": ns.thread_id})]


class RelevanceStation:
    name = "relevance"
    in_stream = STREAM_SOURCES_RESOLVED

    async def handle(self, pipeline: EventPipeline, envelope: Envelope) -> list[EventOut]:
        state = decode_state(envelope)
        filtered: list[SourceRecord] = []
        for src in state.sources:
            prompt = src.title + ("\n" + (src.abstract or "") if src.abstract else "")
            req = ChatRequest.of(
                system="Return ONLY 'true' if this paper is relevant, else 'false'.",
                user=prompt,
                affinity=Affinity.MICRO,
                max_tokens=8,
            )
            try:
                res: ChatResult = await pipeline.provider.complete(req)
                if "true" in res.message.content.lower():
                    filtered.append(src)
            except Exception as exc:  # noqa: BLE001 - keep pipe alive
                logger.warning("relevance filter failed for %s: %s", src.id, exc)
        ns = state.model_copy(update={"filtered": filtered})
        await pipeline.checkpoint(ns)
        return [EventOut(STREAM_SOURCES_FILTERED, EVT_SOURCES_FILTERED, {"state": ns.model_dump(), "thread_id": ns.thread_id})]


class BodiesStation:
    """Fetch LLM-ready Markdown for each filtered source (bounded fan-out)."""

    name = "bodies"
    in_stream = STREAM_SOURCES_FILTERED

    async def handle(self, pipeline: EventPipeline, envelope: Envelope) -> list[EventOut]:
        state = decode_state(envelope)
        sem = asyncio.Semaphore(4)

        async def one(src: SourceRecord) -> DocumentBody | None:
            url = _derive_url(src)
            if not url:
                return None
            async with sem:
                try:
                    return await pipeline.scrape.to_markdown(url)
                except Exception as exc:  # noqa: BLE001 - partial fan-out tolerated
                    logger.warning("scrape failed for %s: %s", src.id, exc)
                    return None

        bodies = await asyncio.gather(*(one(s) for s in state.filtered))
        ns = state.model_copy(update={"bodies": [b for b in bodies if b is not None]})
        await pipeline.checkpoint(ns)
        return [EventOut(STREAM_BODIES_READY, EVT_BODIES_READY, {"state": ns.model_dump(), "thread_id": ns.thread_id})]


class SynthesizeStation:
    """Abstractive synthesis; honors a judge critique on retry passes."""

    name = "synthesize"
    in_stream = STREAM_BODIES_READY

    async def handle(self, pipeline: EventPipeline, envelope: Envelope) -> list[EventOut]:
        state = decode_state(envelope)
        body_texts = "\n---\n".join((b.title or b.url) + "\n" + b.markdown[:4000] for b in state.bodies)
        prompt = f"Synthesize research findings into authoritative notes for:\n{state.query}\n\nSources:\n{body_texts}"
        if state.judge_critique:
            prompt += f"\n\nReviewer feedback to address:\n{state.judge_critique}"
        req = ChatRequest.of(
            system="You are a senior research synthesizer. Produce grounded, well-structured notes.",
            user=prompt,
            affinity=Affinity.MACRO,
            max_tokens=1500,
        )
        res: ChatResult = await pipeline.provider.complete(req)
        # Bounded evaluator-optimizer: append this iteration's notes.
        ns = state.model_copy(update={"synthetic_notes": [*state.synthetic_notes, res.message.content]})
        ns.judge_critique = ""  # clear after addressing
        await pipeline.checkpoint(ns)
        return [EventOut(STREAM_JUDGED, EVT_SYNTHESIS_DONE, {"state": ns.model_dump(), "thread_id": ns.thread_id})]


class JudgeStation:
    """LLM-as-a-judge; gate to render or loop back to synthesize (max N)."""

    name = "judge"
    in_stream = STREAM_JUDGED

    async def handle(self, pipeline: EventPipeline, envelope: Envelope) -> list[EventOut]:
        state = decode_state(envelope)
        if not state.synthetic_notes:
            return [EventOut(STREAM_RENDER, "judged.skip", {"state": state.model_dump(), "thread_id": state.thread_id})]
        body = state.synthetic_notes[-1]
        req = ChatRequest.of(
            system='Score groundedness and citation integrity 0-1. Reply JSON {"score": float, "pass": bool, "critique": str}.',
            user=body,
            affinity=Affinity.MACRO,
            max_tokens=300,
        )
        res: ChatResult = await pipeline.provider.complete(req)
        raw = res.message.content
        passed = '"pass": true' in raw or '"pass": True' in raw
        score = _parse_score(raw)
        ns = state.model_copy(update={"judge_score": score, "judge_critique": "" if passed else raw})
        await pipeline.checkpoint(ns)

        if passed:
            return [EventOut(STREAM_REVIEW, "review.requested", {"state": ns.model_dump(), "thread_id": ns.thread_id})]
        if len(ns.synthetic_notes) < MAX_EVAL_ITERATIONS:
            # evaluator-optimizer: re-synthesize addressing the critique
            retry = ns.model_copy(update={"judge_critique": raw})
            return [EventOut(STREAM_BODIES_READY, "synthesize.retry", {"state": retry.model_dump(), "thread_id": ns.thread_id})]
        # hit iteration cap -> best-effort to review
        return [EventOut(STREAM_REVIEW, "review.requested", {"state": ns.model_dump(), "thread_id": ns.thread_id})]


class ReviewStation:
    """Human-in-the-loop gate. Auto-approves when configured, else waits for
    an external `review.approved` / `review.rejected` event on the review stream.
    """

    name = "review"
    in_stream = STREAM_REVIEW

    async def handle(self, pipeline: EventPipeline, envelope: Envelope) -> list[EventOut]:
        state = decode_state(envelope)
        kind = (envelope.payload or {}).get("decision") or (envelope.type or "")
        if envelope.type == "review.approved":
            return [EventOut(STREAM_RENDER, EVT_RENDER_REQUESTED, {"state": state.model_dump(), "thread_id": state.thread_id})]
        if envelope.type == "review.rejected":
            ns = state.model_copy(update={"status": GraphStatus.FAILED})
            await pipeline.checkpoint(ns)
            return []  # saga terminated
        # review.requested
        if pipeline.auto_approve_review:
            return [EventOut(STREAM_RENDER, EVT_RENDER_REQUESTED, {"state": state.model_dump(), "thread_id": state.thread_id})]
        # Human approval required: do nothing; reviewer publishes review.approved.
        ns = state.model_copy(update={"status": GraphStatus.AWAITING_REVIEW})
        await pipeline.checkpoint(ns)
        return []


class RenderStation:
    name = "render"
    in_stream = STREAM_RENDER

    async def handle(self, pipeline: EventPipeline, envelope: Envelope) -> list[EventOut]:
        state = decode_state(envelope)
        ns = state.model_copy(update={"judge_critique": "", "status": GraphStatus.DONE})
        await pipeline.checkpoint(ns)
        return [EventOut(STREAM_COMPLETED, EVT_COMPLETED, {"state": ns.model_dump(), "thread_id": ns.thread_id})]


def default_stations() -> list:
    """Constructor-free station list bound to the pipeline at dispatch time."""
    return [IngestStation(), RelevanceStation(), BodiesStation(), SynthesizeStation(), JudgeStation(), ReviewStation(), RenderStation()]


def _derive_url(src: SourceRecord) -> str | None:
    if src.pdf_url:
        return src.pdf_url
    if src.doi:
        return f"https://doi.org/{src.doi}"
    if src.id.startswith("arxiv:"):
        return f"https://arxiv.org/abs/{src.id.replace('arxiv:', '')}"
    return None


def _parse_score(raw: str) -> float:
    import re

    m = re.search(r"\d+\.?\d*", raw)
    if m:
        return min(1.0, max(0.0, float(m.group())))
    return 0.0