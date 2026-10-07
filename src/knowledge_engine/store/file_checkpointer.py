"""FileCheckpointer — a BaseCheckpointSaver backed by JSON files.

Keeps LangGraph's durability + human-in-the-loop features while staying
file-only: each thread's checkpoints are stored under
``<root>/checkpoints/<thread_id>/<checkpoint_id>.json`` (plus a
``.writes.json`` for node-level writes). This is the professional replacement
for SqliteSaver/PostgresSaver in a fully portable library — move the folder,
move the state.

Binary checkpoint blobs produced by LangGraph's serializer are base64-encoded
so every artifact is UTF-8 JSON text (the files remain human-inspectable).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

logger = logging.getLogger(__name__)

_B64 = "base64"


def _thread_id(config: RunnableConfig) -> str:
    """Extract the thread identifier.

    Args:
        config: Runnable configuration.

    Returns:
        str: Result text.

    """
    return str(config.get("configurable", {}).get("thread_id") or "default")


def _checkpoint_id(config: RunnableConfig) -> str | None:
    """Extract the checkpoint identifier.

    Args:
        config: Runnable configuration.

    Returns:
        str | None: Text or None.


    """
    return config.get("configurable", {}).get("checkpoint_id")


def _b64_encode(data: bytes) -> str:
    """Base64-encode bytes.

    Args:
        data: Binary payload.

    Returns:
        str: Result text.

    """
    return base64.b64encode(data).decode("ascii")


def _b64_decode(text: str) -> bytes:
    """Base64-decode text.

    Args:
        text: Encoded text.

    Returns:
        bytes: Raw bytes.


    """
    return base64.b64decode(text)


class FileCheckpointer(BaseCheckpointSaver[Any]):
    """Async JSON-file checkpointer for fully portable graph state.

    Locking is per-thread (one lock per ``thread_id``) so parallel runs on
    different threads never serialize on each other; only operations on the
    same thread contend. Lock entries are dropped by :meth:`adelete_thread`.

    Args:
        root: Store root directory.
    """

    def __init__(self, root: str = "research_store/checkpoints") -> None:
        super().__init__()
        self.root = Path(root)
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _lock_for(self, thread_id: str) -> asyncio.Lock:
        """Return the lock for a thread, creating it on first use.

        Args:
            thread_id: Run thread identifier.

        Returns:
            asyncio.Lock: Thread lock.

        """
        async with self._locks_guard:
            lock = self._locks.get(thread_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[thread_id] = lock
            return lock

    def _thread_dir(self, thread_id: str) -> Path:
        """Return the thread directory.

        Args:
            thread_id: Run thread identifier.

        Returns:
            Path: Resolved path.

        """
        path = self.root / thread_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _checkpoint_file(self, thread_id: str, ckpt_id: str) -> Path:
        """Return the checkpoint file path.

        Args:
            thread_id: Run thread identifier.
            ckpt_id: Checkpoint identifier.

        Returns:
            Path: Resolved path.

        """
        return self._thread_dir(thread_id) / f"{ckpt_id}.json"

    def _writes_file(self, thread_id: str, ckpt_id: str) -> Path:
        """Return the writes file path.

        Args:
            thread_id: Run thread identifier.
            ckpt_id: Checkpoint identifier.

        Returns:
            Path: Resolved path.

        """
        return self._thread_dir(thread_id) / f"{ckpt_id}.writes.json"

    # -- async BaseCheckpointSaver ------------------------------------------
    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Aput.

        Args:
            config: Runnable configuration.
            checkpoint: Checkpoint payload.
            metadata: Run metadata mapping.
            new_versions: New channel versions.

        Returns:
            RunnableConfig: Runnable configuration.
        """
        thread = _thread_id(config)
        ckpt_id = checkpoint["id"]
        type_, blob = self.serde.dumps_typed(checkpoint)
        mtype, mblob = self.serde.dumps_typed(metadata)
        payload = {
            "encoding": _B64,
            "type": type_,
            "checkpoint": _b64_encode(blob),
            "metadata_type": mtype,
            "metadata": _b64_encode(mblob),
            "parent_checkpoint_id": _checkpoint_id(config),
        }
        async with await self._lock_for(thread):
            self._checkpoint_file(thread, ckpt_id).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        return {
            "configurable": {
                "thread_id": thread,
                "checkpoint_ns": config.get("configurable", {}).get("checkpoint_ns", ""),
                "checkpoint_id": ckpt_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Aput writes.

        Args:
            config: Runnable configuration.
            writes: Channel writes.
            task_id: Task identifier.
            task_path: Task path."""
        thread = _thread_id(config)
        ckpt_id = _checkpoint_id(config) or ""
        serialized = []
        for channel, value in writes:
            type_, raw = self.serde.dumps_typed(value)
            serialized.append({"channel": channel, "encoding": _B64, "type": type_, "value": _b64_encode(raw)})
        payload = {"task_id": task_id, "task_path": task_path, "writes": serialized}
        async with await self._lock_for(thread):
            path = self._writes_file(thread, ckpt_id)
            existing: list[dict[str, Any]] = []
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
            existing.append(payload)
            path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Aget tuple.

        Args:
            config: Runnable configuration.

        Returns:
            CheckpointTuple | None: Checkpoint tuple or None.
        """
        thread = _thread_id(config)
        ckpt_id = _checkpoint_id(config)
        async with await self._lock_for(thread):
            if ckpt_id:
                path = self._checkpoint_file(thread, ckpt_id)
                candidates = [path] if path.exists() else []
            else:
                candidates = sorted(self._thread_dir(thread).glob("*.json"), reverse=True)
            for path in candidates:
                if path.name.endswith(".writes.json"):
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                checkpoint = self.serde.loads_typed((payload["type"], _b64_decode(payload["checkpoint"])))
                metadata = self.serde.loads_typed((payload["metadata_type"], _b64_decode(payload["metadata"])))
                pending = self._load_writes(self._writes_file(thread, path.stem))
                return CheckpointTuple(
                    config={
                        "configurable": {
                            "thread_id": thread,
                            "checkpoint_ns": config.get("configurable", {}).get("checkpoint_ns", ""),
                            "checkpoint_id": path.stem,
                        }
                    },
                    checkpoint=checkpoint,
                    metadata=metadata,
                    parent_config=_parent_config(thread, payload),
                    pending_writes=pending,
                )
        return None

    def _load_writes(self, writes_file: Path) -> list[tuple[str, str, Any]]:
        """Load pending writes.

        Args:
            writes_file: Writes file path.

        Returns:
            list[tuple[str, str, Any]]: Pending writes.


        """
        if not writes_file.exists():
            return []
        raw = json.loads(writes_file.read_text(encoding="utf-8"))
        pending: list[tuple[str, str, Any]] = []
        for entry in raw:
            for w in entry.get("writes", []):
                value = self.serde.loads_typed((w["type"], _b64_decode(w["value"])))
                pending.append((entry["task_id"], w["channel"], value))
        return pending

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """List checkpoints newest-first.

        Args:
            config: Runnable configuration.
            filter: Listing filter.
            before: List-before cursor.
            limit: Maximum records to return.

        Yields:
            CheckpointTuple: Checkpoint entries.
        """
        thread = _thread_id(config or {})
        before_id = _checkpoint_id(before) if before else None
        files = sorted(self._thread_dir(thread).glob("*.json"), reverse=True)
        yielded = 0
        for path in files:
            if path.name.endswith(".writes.json"):
                continue
            if before_id and path.stem >= before_id:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            checkpoint = self.serde.loads_typed((payload["type"], _b64_decode(payload["checkpoint"])))
            metadata = self.serde.loads_typed((payload["metadata_type"], _b64_decode(payload["metadata"])))
            yield CheckpointTuple(
                config={"configurable": {"thread_id": thread, "checkpoint_id": path.stem}},
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=_parent_config(thread, payload),
                pending_writes=self._load_writes(self._writes_file(thread, path.stem)),
            )
            yielded += 1
            if limit and yielded >= limit:
                return

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints + writes for a thread (and drop its lock).

        Args:
            thread_id: Run thread identifier.
        """
        import shutil

        async with await self._lock_for(thread_id):
            path = self.root / thread_id
            if path.exists():
                shutil.rmtree(path)
        async with self._locks_guard:
            self._locks.pop(thread_id, None)


def _parent_config(thread: str, payload: dict[str, Any]) -> RunnableConfig | None:
    """Build the parent checkpoint config.

    Args:
        thread: Thread identifier.
        payload: Event or resume payload.

    Returns:
        RunnableConfig | None: Runnable config or None.


    """
    parent = payload.get("parent_checkpoint_id")
    if not parent:
        return None
    return {"configurable": {"thread_id": thread, "checkpoint_id": parent}}


__all__ = ["FileCheckpointer"]
