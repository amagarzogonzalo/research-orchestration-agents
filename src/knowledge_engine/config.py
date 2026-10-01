"""Minimal .env loader — keep the library dependency-free.

Reads ``KEY=VALUE`` lines from a ``.env`` file (and ``.env.local`` if present)
into the process environment only for keys that are not already set — so a
deployed env always wins over the file. Comments (``#``) and blank lines are
ignored; quoted values are unquoted.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_LOADED = False


def load_dotenv(*, path: str | Path | None = None) -> bool:
    """Load a .env file into ``os.environ`` (no overwrite), once per process.

    Args:
        path: Optional explicit path; defaults to ``.env`` in the current
            working directory, falling back to ``.env.local``.

    Returns:
        bool: True if a file was read and at least one key was imported.
    """
    global _LOADED
    if _LOADED:
        return True
    candidates = [Path(path)] if path else [Path(".env"), Path(".env.local")]
    for candidate in candidates:
        if candidate.is_file():
            imported = _read_dotenv(candidate)
            if imported:
                logger.info("loaded %s (%d key(s))", candidate, imported)
                _LOADED = True
                return True
    logger.debug("no .env file found in %s", Path.cwd())
    return False


def _read_dotenv(path: Path) -> int:
    imported = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
            imported += 1
    return imported


__all__ = ["load_dotenv"]
