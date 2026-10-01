"""Composition root — wires adapters into the pipeline (ADR 0001).

Reads config from environment, builds the container once at boot, and exposes
the assembled `Pipeline`. Core never imports adapters; this is the only place
they meet.
"""

from __future__ import annotations

import os

from knowledge_engine.infrastructure.broker import RedisStreamBroker
from knowledge_engine.infrastructure.providers import (
    GeminiAdapter,
    GroqAdapter,
    MistralAdapter,
    OpenRouterAdapter,
)
from knowledge_engine.infrastructure.rate_limit import TokenBucketLimiter
from knowledge_engine.infrastructure.router import FallbackRouter
from knowledge_engine.infrastructure.scrape.scrape_router import ScrapeRouter
from knowledge_engine.infrastructure.search.demux import DemuxSearchAdapter
from knowledge_engine.infrastructure.search.openalex import OpenAlexAdapter
from knowledge_engine.infrastructure.search.semantic_scholar import SemanticScholarAdapter
from knowledge_engine.orchestration.event_pipeline import EventPipeline
from knowledge_engine.orchestration.pipeline import Pipeline
from knowledge_engine.orchestration.stations import default_stations


class Settings:
    def __init__(self) -> None:
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        self.database_url = os.environ.get("DATABASE_URL")
        self.openalex_mailto = os.environ.get("OPENALEX_MAILTO", "")
        self.crossref_mailto = os.environ.get("CROSSREF_MAILTO", self.openalex_mailto)
        self.s2_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")

    @classmethod
    def load(cls) -> Settings:
        return cls()


def build_container(settings: Settings | None = None) -> tuple[Pipeline, RedisStreamBroker]:
    settings = settings or Settings.load()

    search = DemuxSearchAdapter(
        [
            OpenAlexAdapter(settings.openalex_mailto),
            SemanticScholarAdapter(settings.s2_key),
        ]
    )
    scrape = ScrapeRouter()
    router = FallbackRouter(
        [GroqAdapter(), GeminiAdapter(), MistralAdapter(), OpenRouterAdapter()],
        limiter=TokenBucketLimiter(),
    )

    broker = RedisStreamBroker(settings.redis_url)

    checkpointer = None
    if settings.database_url:
        from langgraph.checkpoint.postgres import PostgresSaver

        checkpointer = PostgresSaver.from_conn_string(settings.database_url)

    pipeline = Pipeline(
        search=search,
        scrape=scrape,
        provider=router,
        broker=broker,
        checkpointer=checkpointer,
    )
    return pipeline, broker


def build_event_pipeline(settings: Settings | None = None, *, auto_approve_review: bool = True) -> EventPipeline:
    """Composition root for the event-driven choreography (ADR 0007)."""
    settings = settings or Settings.load()

    search = DemuxSearchAdapter(
        [
            OpenAlexAdapter(settings.openalex_mailto),
            SemanticScholarAdapter(settings.s2_key),
        ]
    )
    scrape = ScrapeRouter()
    router = FallbackRouter(
        [GroqAdapter(), GeminiAdapter(), MistralAdapter(), OpenRouterAdapter()],
        limiter=TokenBucketLimiter(),
    )
    broker = RedisStreamBroker(settings.redis_url)

    checkpointer = None
    if settings.database_url:
        from langgraph.checkpoint.postgres import PostgresSaver

        checkpointer = PostgresSaver.from_conn_string(settings.database_url)

    return EventPipeline(
        broker=broker,
        stations=default_stations(),
        search=search,
        scrape=scrape,
        provider=router,
        checkpointer=checkpointer,
        auto_approve_review=auto_approve_review,
    )
