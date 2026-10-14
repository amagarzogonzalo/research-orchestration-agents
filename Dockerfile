# Knowledge Engine HTTP service (Render Free). No browser/scrape extras:
# Chromium cannot fit 512 MB, and the service only needs research + storage.
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy
WORKDIR /app

# uv provides a locked, reproducible install from uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra store,providers,supabase

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 10000
CMD ["sh", "-c", "uvicorn knowledge_engine.service.api:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
