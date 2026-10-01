"""Redis Streams event shell (ADR 0001 / 0007).

The pipeline's write-ahead log: persistence, consumer groups for load balancing,
XACK for acknowledgment. Events are idempotent (deduped by ``event_id``) so the
"requeue -> dead-letter" discipline can redeliver safely. A station publishes
successor events (outbox) *before* XACK, so a crash re-arms retry rather than
dropping the resulting side effects.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import redis.asyncio as aioredis

from knowledge_engine.core.exceptions import KnowledgeEngineError

logger = logging.getLogger(__name__)

Handler = Callable[["Envelope"], Awaitable[None]]
DISPATCH_DELAY_SECONDS = 0.05


@dataclass
class Envelope:
    """Typed message envelope carrying a versioned, validated payload.

    Attributes:
        event_id: Attribute description.
        schema_version: Attribute description.
        type: Attribute description.
        payload: Attribute description.
        attempt: Attribute description.
        error: Attribute description.
        correlation_id: Attribute description.
        created_at: Attribute description.
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: int = 1
    type: str = ""
    payload: dict = field(default_factory=dict)
    attempt: int = 0
    error: str | None = None
    correlation_id: str = ""  # saga id (thread_id); enables end-to-end tracing
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """To dict.

        Returns:
            dict: Result mapping.
        """
        return {
            "event_id": self.event_id,
            "schema_version": self.schema_version,
            "type": self.type,
            "payload": self.payload,
            "attempt": self.attempt,
            "error": self.error,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Envelope:
        """From dict.

        Args:
            data: Binary payload.

        Returns:
            Envelope: Event envelope.
        """
        return cls(
            event_id=data.get("event_id", str(uuid.uuid4())),
            schema_version=data.get("schema_version", 1),
            type=data.get("type", ""),
            payload=data.get("payload", {}),
            attempt=data.get("attempt", 0),
            error=data.get("error"),
            correlation_id=data.get("correlation_id", ""),
            created_at=data.get("created_at", time.time()),
        )

    def with_payload(self, payload: dict, *, type_: str | None = None, event_id: str | None = None) -> Envelope:
        """Return a successor envelope (new id, same correlation).

        Args:
            payload: Event or resume payload.
            type_: Event type name.
            event_id: Event identifier.

        Returns:
            Envelope: Event envelope.

        """
        return Envelope(
            schema_version=self.schema_version,
            type=type_ or self.type,
            payload=payload,
            correlation_id=self.correlation_id or self.payload.get("thread_id", ""),
            event_id=event_id or str(uuid.uuid4()),
        )


class RedisStreamBroker:
    """Broker over Redis Streams with consumer-group + idempotent-dispatch semantics.

    Attributes:
        DEDUPE_TTL_SECONDS: Attribute description.
    """

    DEDUPE_TTL_SECONDS = 3600  # processed-event marker TTL

    def __init__(self, redis_url: str, *, max_attempts: int = 3, dedupe_enabled: bool = True) -> None:
        """Create a RedisStreamBroker.

        Args:
            redis_url: Redis connection URL.
            max_attempts: Maximum attempts.
            dedupe_enabled: Whether dedupe is enabled.

        """
        self.rc = aioredis.from_url(redis_url, decode_responses=True)
        self.max_attempts = max_attempts
        self.dedupe_enabled = dedupe_enabled
        self._group_ok: set[str] = set()

    @classmethod
    def from_redis(
        cls, rc: aioredis.Redis, *, max_attempts: int = 3, dedupe_enabled: bool = True
    ) -> RedisStreamBroker:
        """From redis.

        Args:
            rc: Redis client.
            max_attempts: Maximum attempts.
            dedupe_enabled: Whether dedupe is enabled.

        Returns:
            RedisStreamBroker: Event broker.
        """
        broker = cls.__new__(cls)
        broker.rc = rc
        broker.max_attempts = max_attempts
        broker.dedupe_enabled = dedupe_enabled
        broker._group_ok = set()
        return broker

    # ---- Producer ----
    async def publish(self, stream: str, envelope: Envelope) -> str:
        """Publish.

        Args:
            stream: Stream name.
            envelope: Incoming event envelope.

        Returns:
            str: Result text.
        """
        return await self.rc.xadd(stream, envelope.to_dict())

    async def publish_simple(self, stream: str, type_: str, payload: dict, *, correlation_id: str = "") -> str:
        """Publish simple.

        Args:
            stream: Stream name.
            type_: Event type name.
            payload: Event or resume payload.
            correlation_id: Correlation identifier.

        Returns:
            str: Result text.
        """
        return await self.publish(
            stream,
            Envelope(
                type=type_,
                payload=payload,
                correlation_id=correlation_id or payload.get("thread_id", ""),
            ),
        )

    # ---- Consumer group ----
    async def _ensure_group(self, stream: str, group: str) -> None:
        """Ensure group.

        Args:
            stream: Stream name.
            group: Consumer group name.
        """
        if group in self._group_ok:
            return
        try:
            await self.rc.xgroup_create(stream, group, id="$", mkstream=True)
        except aioredis.ResponseError:
            pass  # group already exists
        self._group_ok.add(group)

    async def _already_processed(self, event_id: str) -> bool:
        """Already processed.

        Args:
            event_id: Event identifier.

        Returns:
            bool: True on success.

        """
        if not self.dedupe_enabled:
            return False
        key = f"processed:{event_id}"
        prev = await self.rc.set(key, "1", nx=True, ex=self.DEDUPE_TTL_SECONDS)
        return prev is None  # set returned None => key existed => already processed

    async def consume_group(self, stream: str, group: str, consumer: str, handler: Handler, *, batch: int = 10) -> int:
        """Process up to `batch` pending messages once; returns count handled.

                Ordering: dedupe -> run handler (which publishes outbox successors) ->
                XACK. Outbox-before-XACK means a crash after publish but before ack
                re-runs the handler, but the dedupe guard makes it idempotent.

        Args:
            stream: Stream name.
            group: Consumer group name.
            consumer: Consumer name.
            handler: Event handler callable.
            batch: Batch size.

        Returns:
            int: Number of handled messages.
        """
        await self._ensure_group(stream, group)
        entries = await self.rc.xreadgroup(group, consumer, {stream: ">"}, count=batch)
        handled = 0
        for _stream, messages in entries:
            for msg_id, fields in messages:
                envelope = Envelope.from_dict(fields)
                if envelope.schema_version != 1:
                    await self._requeue_or_dead(
                        stream, group, msg_id, fields, KnowledgeEngineError("unsupported schema_version")
                    )
                    continue
                if await self._already_processed(envelope.event_id):
                    logger.debug("dedupe: skip already-processed event %s", envelope.event_id)
                    await self.rc.xack(stream, group, msg_id)
                    handled += 1
                    continue
                try:
                    await handler(envelope)
                except Exception as exc:  # noqa: BLE001 - dead-letter discipline
                    logger.exception("message %s failed", msg_id)
                    await self._requeue_or_dead(stream, group, msg_id, fields, exc)
                    continue
                await self.rc.xack(stream, group, msg_id)
                handled += 1
        return handled

    async def _requeue_or_dead(self, stream: str, group: str, msg_id: str, fields: dict, exc: Exception) -> None:
        """Requeue or dead.

        Args:
            stream: Stream name.
            group: Consumer group name.
            msg_id: Message identifier.
            fields: Field mapping.
            exc: Caught exception.

        """
        env = Envelope.from_dict(fields)
        env.attempt += 1
        env.error = str(exc)
        if env.attempt < self.max_attempts:
            await self.rc.xadd(f"requeue:{stream}", env.to_dict())
            logger.warning("message %s requeued (attempt %d): %s", msg_id, env.attempt, exc)
        else:
            await self.rc.xadd(f"dead-letter:{stream}", env.to_dict())
            logger.error("message %s dead-lettered after %d attempts", msg_id, env.attempt)
        await self.rc.xack(stream, group, msg_id)

    async def poll_forever(self, streams: dict[str, Handler], *, group: str, consumer: str, batch: int = 10) -> None:
        """Blocking dispatch loop: map each stream to a handler, process continuously.

        Args:
            streams: Stream to handler mapping.
            group: Consumer group name.
            consumer: Consumer name.
            batch: Batch size.
        """
        for stream in streams:
            await self._ensure_group(stream, group)
        while True:
            for stream, handler in streams.items():
                try:
                    await self.consume_group(stream, group, consumer, handler, batch=batch)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("poll %s failed: %s", stream, exc)
            await asyncio.sleep(DISPATCH_DELAY_SECONDS)

    async def close(self) -> None:
        """Release resources."""
        await self.rc.aclose()
