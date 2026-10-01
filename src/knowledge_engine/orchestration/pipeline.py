"""LangGraph orchestrator-worker graph (ADR 0005).

Deterministic workflows (routing, eager parallel fan-out, evaluator-optimizer)
over the resilient event shell. The graph never calls providers directly — it
awaits typed results from the shell/ports. State persists via a Postgres
checkpointer on Neon for pause/resume + human-in-the-loop.
"""

from __future__ import annotations

import asyncio
import logging

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from knowledge_engine.core.ports import BrokerPort, ProviderPort, ScrapePort, SearchPort
from knowledge_engine.schemas.extraction import (
    GraphState,
    GraphStatus,
)
from knowledge_engine.schemas.provider import Affinity, ChatRequest, ChatResult
from knowledge_engine.schemas.source import DocumentBody, SourceRecord

logger = logging.getLogger(__name__)

MAX_EVAL_ITERATIONS = 2


class Pipeline:
    """Assembles + runs the LangGraph state machine over injected ports."""

    def __init__(
        self,
        *,
        search: SearchPort,
        scrape: ScrapePort,
        provider: ProviderPort,
        broker: BrokerPort,
        checkpointer: PostgresSaver | None = None,
    ) -> None:
        """Create a Pipeline.

        Args:
            search: Scholarly search port.
            scrape: Scrape-to-markdown port.
            provider: Provider key for rate limiting.
            broker: Event broker transport.
            checkpointer: Durability checkpointer.
        """
        self.search = search
        self.scrape = scrape
        self.provider = provider
        self.broker = broker
        self.checkpointer = checkpointer
        self.graph = self._build().compile(checkpointer=checkpointer) if checkpointer else self._build().compile()

    # ---- nodes ----
    async def ingest(self, state: GraphState) -> dict:
        """Ingest.

        Args:
            state: Current workflow state.

        Returns:
            dict: Result mapping.
        """
        sources = await self.search.search(state.query, limit=10)
        return {"sources": sources}

    async def relevance(self, state: GraphState) -> dict:
        """Cheap Groq-driven boolean filter (affinity=MICRO).

        Args:
            state: Current workflow state.

        Returns:
            dict: Result mapping.

        """
        passed: list[SourceRecord] = []
        for src in state.sources:
            req = ChatRequest.of(
                system="Return ONLY 'true' if this paper is relevant, else 'false'.",
                user=src.title + ("\n" + (src.abstract or "") if src.abstract else ""),
                affinity=Affinity.MICRO,
                max_tokens=8,
            )
            try:
                res: ChatResult = await self.provider.complete(req)
                if "true" in res.message.content.lower():
                    passed.append(src)
            except Exception as exc:  # noqa: BLE001 - keep pipe alive
                logger.warning("relevance filter failed for %s: %s", src.id, exc)
        return {"filtered": passed}

    async def fetch_bodies(self, state: GraphState) -> dict:
        """Fetch bodies.

        Args:
            state: Current workflow state.

        Returns:
            dict: Result mapping.
        """

        async def one(src: SourceRecord) -> DocumentBody | None:
            """One.

            Args:
                src: Source record.

            Returns:
                DocumentBody | None: Body or None.
            """
            if not src.pdf_url and not src.id.startswith("arxiv:"):
                return None
            url = src.pdf_url or f"https://arxiv.org/abs/{src.id.replace('arxiv:', '')}"
            try:
                return await self.scrape.to_markdown(url)
            except Exception as exc:  # noqa: BLE001 - partial fan-out tolerated
                logger.warning("scrape failed for %s: %s", src.id, exc)
                return None

        bodies = await asyncio.gather(*(one(s) for s in state.filtered))
        return {"bodies": [b for b in bodies if b is not None]}

    async def synthesize(self, state: GraphState) -> dict:
        """Synthesize.

        Args:
            state: Current workflow state.

        Returns:
            dict: Result mapping.
        """
        critique = state.judge_critique
        prompt = (
            f"Synthesize the research findings into structured notes for query:\n{state.query}\n\\nSources:\n"
            + "\n---\n".join((b.title or b.url) + "\n" + b.markdown[:4000] for b in state.bodies)
        )
        if critique:
            prompt += f"\n\nReviewer feedback to address:\n{critique}"
        req = ChatRequest.of(
            system="You are a senior research synthesizer. Produce authoritative, grounded notes.",
            user=prompt,
            affinity=Affinity.MACRO,
            max_tokens=1500,
        )
        try:
            res: ChatResult = await self.provider.complete(req)
            notes = state.synthetic_notes + [res.message.content]
            return {"synthetic_notes": notes}
        except Exception as exc:  # noqa: BLE001
            logger.error("synthesis failed: %s", exc)
            return {"status": GraphStatus.FAILED}

    async def judge(self, state: GraphState) -> dict:
        """Judge.

        Args:
            state: Current workflow state.

        Returns:
            dict: Result mapping.
        """
        req = ChatRequest.of(
            system=(
                "Score groundedness and citation integrity 0-1 on the synthesis. "
                'Reply as JSON {"score": float, "pass": bool, "critique": str}.'
            ),
            user="\n\n".join(state.synthetic_notes),
            affinity=Affinity.MACRO,
            max_tokens=300,
        )
        try:
            res: ChatResult = await self.provider.complete(req)
            raw = res.message.content
            passed = '"pass": true' in raw or '"pass": True' in raw
            return {"judge_score": _parse_score(raw), "judge_critique": "" if passed else raw}
        except Exception as exc:  # noqa: BLE001
            logger.error("judge failed: %s", exc)
            return {"judge_score": 0.0, "judge_critique": "judge unavailable; accept on merit"}

    async def render(self, state: GraphState) -> dict:
        """Render.

        Args:
            state: Current workflow state.

        Returns:
            dict: Result mapping.
        """
        return {"output_path": _fake_output_path(state.thread_id), "status": "done"}

    # ---- routing edges ----
    def _should_iterate(self, state: GraphState) -> str:
        """Should iterate.

        Args:
            state: Current workflow state.

        Returns:
            str: Result text.
        """
        if state.judge_critique and len(state.synthetic_notes) < MAX_EVAL_ITERATIONS:
            return "synthesize"
        return "render"

    async def _fan(self, state: GraphState) -> list[Send]:
        # Eager parallel fan-out of body fetches (bounded by shell/token buckets).
        """Fan.

        Args:
            state: Current workflow state.

        Returns:
            list[Send]: Send directives.

        """
        return [Send("fetch_bodies_thunk", state) for _ in [0]]

    def _build(self) -> StateGraph:
        """Build the object.

        Returns:
            StateGraph: Graph builder.


        """
        g = StateGraph(GraphState)
        g.add_node("ingest", self.ingest)
        g.add_node("relevance", self.relevance)
        g.add_node("fetch_bodies", self.fetch_bodies)
        g.add_node("synthesize", self.synthesize)
        g.add_node("judge", self.judge)
        g.add_node("render", self.render)
        g.add_edge(START, "ingest")
        g.add_edge("ingest", "relevance")
        g.add_edge("relevance", "fetch_bodies")
        g.add_edge("fetch_bodies", "synthesize")
        g.add_edge("synthesize", "judge")
        g.add_conditional_edges("judge", self._should_iterate, {"synthesize": "synthesize", "render": "render"})
        g.add_edge("render", END)
        return g

    async def run(self, thread_id: str, query: str) -> GraphState:
        """Run the unit of work.

        Args:
            thread_id: Run thread identifier.
            query: Scholarly query string.

        Returns:
            GraphState: Durable state snapshot.

        """
        config = {"configurable": {"thread_id": thread_id}}
        initial = GraphState(thread_id=thread_id, query=query)
        final = await self.graph.ainvoke(initial.model_dump(), config=config)
        return GraphState(**final)


def _parse_score(raw: str) -> float:
    """Parse score.

    Args:
        raw: Raw payload.

    Returns:
        float: Computed value.
    """
    import json
    import re

    m = re.search(r"\d+\.?\d*", raw)
    if m:
        return min(1.0, max(0.0, float(m.group())))
    try:
        return float(json.loads(raw).get("score", 0.0))
    except Exception:
        return 0.0


def _fake_output_path(thread_id: str) -> str:
    """Fake output path.

    Args:
        thread_id: Run thread identifier.

    Returns:
        str: Result text.
    """
    return f"knowledge_base/{thread_id}.md"
