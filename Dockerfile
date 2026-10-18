# syntax=docker/dockerfile:1.7
#
# Knowledge Engine HTTP service (Render Free). No browser/scrape extras:
# Chromium cannot fit 512 MB, and the service only needs research + storage.
#
# Hardening: digest-pinned base, multi-stage (no build toolchain in runtime),
# non-root numeric UID, OCI labels, HEALTHCHECK. Build with SBOM + provenance:
#   docker buildx build --sbom=true --provenance=true -t knowledge-engine:local .

# ---- build: resolve the locked env into an isolated venv -------------------
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS build
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 /uv /uvx /usr/local/bin/
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra store,providers,supabase

# ---- runtime: minimal, non-root, no toolchain -----------------------------
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}"
WORKDIR /app
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build --chown=10001:10001 /app/.venv /app/.venv
COPY --from=build --chown=10001:10001 /app/src /app/src
COPY --from=build --chown=10001:10001 /app/pyproject.toml /app/uv.lock /app/README.md ./
# The service writes local run artifacts under the working directory; the
# non-root user must own /app to create research_store/ at runtime.
RUN mkdir -p /app/research_store && chown 10001:10001 /app /app/research_store
USER 10001:10001
EXPOSE 10000
LABEL org.opencontainers.image.title="knowledge-engine" \
      org.opencontainers.image.description="Zero-cost research orchestration service" \
      org.opencontainers.image.source="https://github.com/amagarzogonzalo/research-orchestration-agents" \
      org.opencontainers.image.licenses="MIT"
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:10000/health', timeout=4).status == 200 else 1)"
# WEB_CONCURRENCY is wired here so the render.yaml knob actually takes effect.
CMD ["sh", "-c", "uvicorn knowledge_engine.service.api:app --host 0.0.0.0 --port ${PORT:-10000} --workers ${WEB_CONCURRENCY:-1}"]
