"""HTTP service layer (issue #30): FastAPI facade over the research engine.

The library stays framework-free; this package is the deploy edge. It is only
imported by the uvicorn entrypoint (:mod:`knowledge_engine.service.api`) and by
tests, so the core never pays for FastAPI.
"""

from __future__ import annotations

__all__: list[str] = []
