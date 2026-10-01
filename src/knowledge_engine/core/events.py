"""Event topology for the event-driven shell (ADR 0007).

Every unit of work in the pipeline is an *event* (a typed ``Envelope`` carrying
the saga ``GraphState``) that moves between named :class:`Station` handlers
over Redis Streams. Stations are idempotent, replayable handlers: consume from
``in_stream``, do a bounded unit of work, then *publish* successor events before
acknowledging (outbox), so a crash re-arms retry without dropping side effects.
"""

from __future__ import annotations

# --- Stream names (one channel per station's inbox) ---
STREAM_INBOX = "research.inbox"
STREAM_SOURCES_RESOLVED = "research.sources.resolved"
STREAM_SOURCES_FILTERED = "research.sources.filtered"
STREAM_SCRAPE = "research.scrape.request"
STREAM_BODY_READY = "research.body.ready"
STREAM_BODIES_READY = "research.bodies.ready"
STREAM_SYNTHESIS = "research.synthesis.done"
STREAM_JUDGED = "research.judged"
STREAM_REVIEW = "research.review"
STREAM_RENDER = "research.render.request"
STREAM_COMPLETED = "research.completed"
STREAM_REQUIRED_ERRORS = "research.errored"  # terminal saga failures

# --- Event types (envelope.type) ---
EVT_RESEARCH_REQUESTED = "research.requested"
EVT_SOURCES_RESOLVED = "sources.resolved"
EVT_SOURCES_FILTERED = "sources.filtered"
EVT_SCRAPE_REQUESTED = "scrape.requested"
EVT_BODY_READY = "body.ready"
EVT_BODIES_READY = "bodies.ready"
EVT_SYNTHESIS_DONE = "synthesis.done"
EVT_JUDGED = "judged"
EVT_REVIEW_REQUESTED = "review.requested"
EVT_REVIEW_APPROVED = "review.approved"
EVT_REVIEW_REJECTED = "review.rejected"
EVT_RENDER_REQUESTED = "render.requested"
EVT_COMPLETED = "completed"
EVT_TERMINAL_ERROR = "terminal.error"

# Default consumer group used by worker processes.
DEFAULT_GROUP = "knowledge-engine"


class EventOut:
    """A successor event a station emits (target stream + envelope fields)."""

    __slots__ = ("stream", "type", "payload", "ref")

    def __init__(self, stream: str, type_: str, payload: dict, ref: str | None = None) -> None:
        self.stream = stream
        self.type = type_
        self.payload = payload
        self.ref = ref  # e.g. per-source scrape fan-out key

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"EventOut({self.stream}, {self.type})"
