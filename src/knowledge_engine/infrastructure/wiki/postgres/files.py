"""File store port + ``wiki.files`` registry (SRP, pointer-not-URL).

Content-addressed storage: a ``FileRef`` is {bucket, path} where ``path`` is the
sha256 sharded by the first two hex chars (``ab/<sha256>.md``) — enforced by a
CHECK on ``wiki.files``. The write ORDER is honored by the store: (1) upload the
object (idempotent), (2) INSERT ``wiki.files`` ON CONFLICT DO NOTHING, (3) insert
the referencing row (``wiki.source_bodies`` / ``wiki.run_files``). Buckets stay
private and are reached with the backend's service key.
"""

from __future__ import annotations

import abc
import hashlib
from pathlib import Path
from typing import Any

from knowledge_engine.core.exceptions import KnowledgeEngineError


class FileRef:
    """A pointer to a stored file (bucket + sharded path)."""

    def __init__(self, sha256: str, path: str, bucket: str, mime: str) -> None:
        """Create a file pointer.

        Args:
            sha256: Content hash (the address).
            path: Object path within the bucket (``ab/<sha256>.md``).
            bucket: Storage bucket name.
            mime: Content type.
        """
        self.sha256 = sha256
        self.path = path
        self.bucket = bucket
        self.mime = mime

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        """Human-readable pointer.

        Returns:
            str: Pointer string.
        """
        return f"FileRef({self.bucket}/{self.path})"


def sharded_path(sha256: str, *, suffix: str = ".md") -> str:
    """Shard a content hash into ``ab/<sha256><suffix>``.

    Args:
        sha256: Hex content hash (64 chars).
        suffix: Filename suffix (default ``.md``).

    Returns:
        str: Sharded object path.

    Raises:
        ValueError: When the hash is not 64 hex chars.
    """
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError(f"expected a 64-char hex sha256, got {len(sha256)} chars")
    return f"{sha256[:2]}/{sha256}{suffix}"


def sha256_bytes(data: bytes) -> str:
    """SHA256 hex digest of raw bytes (the content address).

    Args:
        data: Raw bytes.

    Returns:
        str: Hex digest.
    """
    return hashlib.sha256(data).hexdigest()


class FileStorePort(abc.ABC):
    """Persist content-addressed bytes to an object store (SRP)."""

    @abc.abstractmethod
    async def put(self, data: bytes, *, mime: str = "text/markdown") -> FileRef:
        """Upload bytes idempotently and return a pointer.

        Args:
            data: Raw bytes.
            mime: Content type.

        Returns:
            FileRef: Pointer for downstream ``wiki.files`` registration.
        """

    @abc.abstractmethod
    async def get(self, ref: FileRef) -> bytes | None:
        """Read bytes back by pointer, or None when absent.

        Args:
            ref: Pointer returned by :meth:`put`.

        Returns:
            bytes | None: File bytes or None.
        """


class FilesRepo:
    """``wiki.files`` + ``wiki.run_files``/``wiki.source_bodies`` rows (SRP)."""

    _REGISTER = (
        "INSERT INTO wiki.files (sha256, bucket, path, size_bytes, mime) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (sha256) DO NOTHING"
    )
    _RUN_FILE = (
        "INSERT INTO wiki.run_files (thread_id, role, file_sha256) VALUES (%s, %s, %s) "
        "ON CONFLICT (thread_id, role) DO UPDATE SET file_sha256 = EXCLUDED.file_sha256"
    )

    async def register(self, cur: Any, ref: FileRef, data: bytes) -> None:
        """Insert the ``wiki.files`` row if absent (the object is already uploaded).

        Args:
            cur: Active async cursor.
            ref: The file pointer.
            data: The bytes, for the size.
        """
        await cur.execute(self._REGISTER, (ref.sha256, ref.bucket, ref.path, len(data), ref.mime))

    async def attach_run_file(self, cur: Any, *, thread_id: str, role: str, file_sha256: str) -> None:
        """Record a run artifact pointer (article/sources/metadata/events).

        Args:
            cur: Active async cursor.
            thread_id: Run thread.
            role: artifact role.
            file_sha256: File hash.
        """
        await cur.execute(self._RUN_FILE, (thread_id, role, file_sha256))


class LocalFileStore(FileStorePort):
    """Filesystem-backed object store (``bucket/<sharded>`` layout).

    Args:
        root: Base directory for objects.
        bucket: Bucket name used in pointers.
    """

    def __init__(self, root: str | Path, *, bucket: str = "bodies") -> None:
        self.root = Path(root)
        self.bucket = bucket

    def _object(self, ref: FileRef) -> Path:
        """Local object path for a pointer.

        Args:
            ref: File pointer.

        Returns:
            Path: Local path.
        """
        return self.root / ref.bucket / ref.path

    async def put(self, data: bytes, *, mime: str = "text/markdown") -> FileRef:
        """Write bytes idempotently and return the pointer.

        Args:
            data: Raw bytes.
            mime: Content type.

        Returns:
            FileRef: Pointer.
        """
        sha = sha256_bytes(data)
        ref = FileRef(sha, sharded_path(sha), self.bucket, mime)
        obj = self._object(ref)
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_bytes(data)
        return ref

    async def get(self, ref: FileRef) -> bytes | None:
        """Read bytes back.

        Args:
            ref: Pointer.

        Returns:
            bytes | None: Bytes or None.
        """
        obj = self._object(ref)
        return obj.read_bytes() if obj.is_file() else None


class SupabaseFileStore(FileStorePort):
    """Supabase Storage-backed object store (private bucket, service key).

    Args:
        client: A Supabase client (with ``.storage``) — injected to keep the
            core free of the SDK (DIP).
        bucket: Private bucket name (created on demand).
    """

    def __init__(self, client: Any, *, bucket: str = "bodies") -> None:
        self._client = client
        self._bucket = bucket

    async def put(self, data: bytes, *, mime: str = "text/markdown") -> FileRef:
        """Upsert bytes into the private bucket and return the pointer.

        Args:
            data: Raw bytes.
            mime: Content type.

        Returns:
            FileRef: Pointer.

        Raises:
            KnowledgeEngineError: When the upload fails.
        """
        sha = sha256_bytes(data)
        ref = FileRef(sha, sharded_path(sha), self._bucket, mime)
        try:
            self._client.storage.from_(self._bucket).upload(ref.path, data, {"content-type": mime, "upsert": "true"})
        except Exception as exc:  # noqa: BLE001 - storage outage is a hard stop
            raise KnowledgeEngineError(f"supabase file store put failed: {exc}") from exc
        return ref

    async def get(self, ref: FileRef) -> bytes | None:
        """Download bytes back, or None when absent.

        Args:
            ref: Pointer.

        Returns:
            bytes | None: Bytes or None.
        """
        try:
            blob = self._client.storage.from_(self._bucket).download(ref.path)
        except Exception:  # noqa: BLE001 - absent object is a normal miss
            return None
        return blob if isinstance(blob, bytes) else blob.encode("utf-8")


__all__ = [
    "FileRef",
    "FileStorePort",
    "FilesRepo",
    "LocalFileStore",
    "SupabaseFileStore",
    "sha256_bytes",
    "sharded_path",
]
