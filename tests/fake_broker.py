"""In-memory broker double for offline tests (no Redis)."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque

from knowledge_engine.infrastructure.broker import Envelope, Handler


class FakeBroker:
    """Minimal stream store + handler loop for exercising the choreography."""

    def __init__(self) -> None:
        self.streams: dict[str, deque[dict]] = {}
        # For saga checkpoint() calls that touch broker.rc: provide a tiny stub.
        self.rc = _FakeRC()
        self._handlers: dict[str, Handler] = {}
        self.processed_events: set[str] = set()
        self.history: list[Envelope] = []

    async def publish(self, stream: str, envelope: Envelope) -> str:
        self.streams.setdefault(stream, deque()).append(envelope.to_dict())
        return uuid.uuid4().hex

    async def publish_simple(self, stream: str, type_: str, payload: dict, *, correlation_id: str = "") -> str:
        return await self.publish(
            stream,
            Envelope(type=type_, payload=payload, correlation_id=correlation_id or payload.get("thread_id", "")),
        )

    def drain(self, stream: str) -> list[dict]:
        return list(self.streams.get(stream, []))

    # Test harness: dispatch one pass over every non-empty stream.
    async def run_cycle(self, handlers: dict[str, Handler], *, max_passes: int = 50) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _ in range(max_passes):
            progressed = False
            for stream, handler in handlers.items():
                pending = self.streams.get(stream, [])
                emitted = 0
                while pending:
                    payload = pending.popleft()
                    envelope = Envelope.from_dict(payload)
                    if envelope.event_id in self.processed_events:
                        continue  # idempotent replay
                    self.processed_events.add(envelope.event_id)
                    self.history.append(envelope)
                    await handler(envelope)
                    emitted += 1
                    progressed = True
                if emitted:
                    counts[stream] = counts.get(stream, 0) + emitted
            if not progressed:
                break
        return counts

    async def close(self) -> None:
        pass


class _FakeRC:
    """Minimal stub standing in for broker.rc so checkpoint() calls succeed."""

    async def hset(self, key, mapping=None, *args, **kwargs):
        return 1