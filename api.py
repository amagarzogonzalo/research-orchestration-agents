"""Render/uvicorn entrypoint shim (SPEC §4).

Re-exports the service app so ``uvicorn api:app`` works from the repo root; the
Dockerfile prefers the fully-qualified ``knowledge_engine.service.api:app``.
"""

from __future__ import annotations

from knowledge_engine.service.api import app

__all__ = ["app"]
