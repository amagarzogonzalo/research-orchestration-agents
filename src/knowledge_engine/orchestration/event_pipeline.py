"""Event-driven pipeline: Station choreography over the shell (ADR 0007).

Each :class:`Station` consumes one inbox stream, performs a bounded unit of work
against injected ports, and returns successor :class:`EventOut` messages (its
outbox). The :class:`EventPipeline` wires stations to streams and exposes:

* ``submit``  — ingest a new research request as an event onto the inbox.
* ``consume_forever`` — blocking loop delegating to ``RedisStreamBroker.poll_forever``.

State is carried in the event payload (``GraphState``) so stations are pure and
replayable: same envelope -> same outbox, making requeue/dedupe safe. A durable
saga log (checkpointer) can persist state at each hop for human-in-the-loop resume.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from knowledge_engine.core.events import (
    DEFAULT_GROUP,
    EVT_TERMINAL_ERROR,
    STREAM_INBOX,
    STREAM_REQUIRED_ERRORS,
    EventOut,
)
from knowledge_engine.infrastructure.broker import Envelope, RedisStreamBroker
from knowledge_engine.schemas.extraction import GraphState

logger = logging.getLogger(__name__)

MAX_EVAL_ITERATIONS = 2


@runtime_checkable
class Station(Protocol):
    """A unit of event processing. Implement `handle` returning successor events.

    Attributes:
        name: Station name.
        in_stream: Inbox stream name.
    """

    name: str
    in_stream: str

    async def handle(self, pipeline: EventPipeline, envelope: Envelope) -> list[EventOut]:
        """Handle an envelope.

        Args:
            pipeline: Owning event pipeline.
            envelope: Incoming event envelope.

        Returns:
            list[EventOut]: Successor events.
        """
        ...


Handler = Callable[[Envelope], Awaitable[None]]


class EventPipeline:
    """Choreography engine: station registry + event emit + dispatch map."""

    def __init__(
        self,
        *,
        broker: RedisStreamBroker,
        stations: list[Station],
        search: Any | None = None,
        scrape: Any | None = None,
        provider: Any | None = None,
        checkpointer: Any | None = None,
        auto_approve_review: bool = True,
    ) -> None:
        """Create a pipeline.

        Args:
            broker: Event broker transport.
            stations: Station registry.
            search: Scholarly search port.
            scrape: Scrape-to-markdown port.
            provider: Provider key for rate limiting.
            checkpointer: Durability checkpointer.
            auto_approve_review: Whether review auto-approves.

        """
        self.broker = broker
        self.search = search
        self.scrape = scrape
        self.provider = provider
        self.checkpointer = checkpointer
        self.auto_approve_review = auto_approve_review
        self._stations = {s.name: s for s in stations}
        self._extra_handlers: dict[str, Handler] = {}

    # ---- outbox ----
    async def emit(self, source: Envelope, out: EventOut) -> str:
        """Emit a successor event.

        Args:
            source: Source envelope.
            out: Outgoing event.

        Returns:
            str: Event identifier.
        """
        successor = source.with_payload(out.payload, type_=out.type)
        if not successor.correlation_id:
            successor.correlation_id = (source.payload or {}).get("thread_id", "")
        return await self.broker.publish(out.stream, successor)

    async def emit_new(self, stream: str, type_: str, payload: dict, *, correlation_id: str = "") -> str:
        """Emit a new event.

        Args:
            stream: Stream name.
            type_: Event type name.
            payload: Event payload.
            correlation_id: Correlation identifier.

        Returns:
            str: Event identifier.
        """
        return await self.broker.publish_simple(stream, type_, payload, correlation_id=correlation_id)

    # ---- dispatch ----
    async def _dispatch(self, station: Station, envelope: Envelope) -> None:
        """Dispatch an envelope to a station.

        Args:
            station: Station to dispatch.
            envelope: Incoming event envelope.
        """
        try:
            outs = await station.handle(self, envelope)
        except Exception as exc:  # noqa: BLE001 - terminal-error discipline
            logger.exception("station %s failed for event %s", station.name, envelope.event_id)
            await self.emit_new(
                STREAM_REQUIRED_ERRORS,
                EVT_TERMINAL_ERROR,
                {"error": str(exc), "station": station.name, "thread_id": envelope.correlation_id},
                correlation_id=envelope.correlation_id,
            )
            return
        for out in outs or []:
            await self.emit(envelope, out)

    def handler_map(self) -> dict[str, Handler]:
        """Stream -> dispatch callable, consumed by the broker poll loop.

        Returns:
            dict[str, Handler]: Stream to handler mapping.

        """
        out: dict[str, Handler] = {s.in_stream: self._make_dispatcher(s) for s in self._stations.values()}
        out.update(self._extra_handlers)
        return out

    def _make_dispatcher(self, station: Station) -> Handler:
        """Create a dispatch callable for a station.

        Args:
            station: Station to dispatch.

        Returns:
            Handler: Dispatch callable.
        """

        async def _run(envelope: Envelope) -> None:
            """Run dispatch.

            Args:
                envelope: Incoming event envelope.
            """
            await self._dispatch(station, envelope)

        return _run

    def add_handler(self, stream: str, handler: Handler) -> None:
        """Register an extra handler.

        Args:
            stream: Stream name.
            handler: Event handler callable.
        """
        self._extra_handlers[stream] = handler

    # ---- entrypoints ----
    async def submit(self, thread_id: str, query: str) -> str:
        """Submit a research request.

        Args:
            thread_id: Run thread identifier.
            query: Scholarly query string.

        Returns:
            str: Event identifier.
        """
        state = GraphState(thread_id=thread_id, query=query)
        return await self.emit_new(
            STREAM_INBOX,
            "research.requested",
            {"state": state.model_dump(), "thread_id": thread_id},
            correlation_id=thread_id,
        )

    async def checkpoint(self, state: GraphState) -> None:
        """Persist a durable saga snapshot per thread (recover/HITL resume).

        Args:
            state: Current workflow state.
        """
        try:
            key = f"saga:{state.thread_id}"
            await self.broker.rc.hset(key, mapping={"state": state.model_dump_json(), "updated_at": str(_utc_now())})
        except Exception as exc:  # noqa: BLE001 - snapshot is best-effort
            logger.warning("saga checkpoint failed: %s", exc)

    async def consume_forever(self, *, group: str = DEFAULT_GROUP, consumer: str = "worker") -> None:
        """Consume streams forever.

        Args:
            group: Consumer group name.
            consumer: Consumer name.
        """
        await self.broker.poll_forever(self.handler_map(), group=group, consumer=consumer)

    async def close(self) -> None:
        """Close the broker."""
        await self.broker.close()


def decode_state(envelope: Envelope) -> GraphState:
    """Decode graph state from an envelope.

    Args:
        envelope: Incoming event envelope.

    Returns:
        GraphState: Decoded state.
    """
    state = (envelope.payload or {}).get("state") or {}
    return GraphState(**state)


def _utc_now() -> datetime:
    """Return the current UTC time.

    Returns:
        datetime: Current UTC time.
    """
    return datetime.now(UTC)
