"""OpenTelemetry GenAI instrumentation (ADR 0006).

Initializes a tracer + meter exported via OTLP to a free self-hosted sink
(JSONL file or a local OTLP collector). No paid SaaS. Recomputed cost via
:mod:`knowledge_engine.core.pricing`, never vendor-reported.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

_OTEL_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "knowledge-engine")
# No-op by default: only export when explicitly configured via env, avoiding
# network attempts (e.g. toward localhost:4318) on plain imports and CI.
_TRACES_EXPORTER = os.environ.get("OTEL_TRACES_EXPORTER", "").strip()
_METRICS_EXPORTER = os.environ.get("OTEL_METRICS_EXPORTER", "none").strip()


def _resource() -> Resource:
    """Resource.

    Returns:
        Resource: Telemetry resource.

    """
    return Resource.create({"service.name": _SERVICE_NAME})


def _init_trace() -> trace.Tracer:
    """Init trace.

    Returns:
        trace.Tracer: Telemetry tracer.

    """
    provider = TracerProvider(resource=_resource())
    if _TRACES_EXPORTER not in {"", "none"}:
        if _TRACES_EXPORTER == "otlp" or _TRACES_EXPORTER == "http":
            exporter = OTLPSpanExporter(endpoint=_OTEL_ENDPOINT)
        else:
            exporter = _ConsoleExporter()
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer("knowledge.engine")


class _ConsoleExporter:
    """Minimal fallback exporter logging spans when OTLP is disabled."""

    def export(self, spans: Any, timeout_millis: int = 30000) -> Any:
        """Export spans to the console.

        Args:
            spans: Spans to export.
            timeout_millis: Export timeout in milliseconds.

        Returns:
            Any: Success result.
        """
        for span in spans:
            logger.info("span: %s", span)
        return _OkResult()

    def shutdown(self):
        """Shut down the exporter."""
        pass


class _OkResult:
    """_OkResult.

    Attributes:
        status: Attribute description."""

    status = "success"


def _init_meter() -> metrics.Meter:
    """Init meter.

    Returns:
        metrics.Meter: Telemetry meter.

    """
    readers = []
    if _METRICS_EXPORTER == "console":
        readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter()))
    provider = MeterProvider(resource=_resource(), metric_readers=readers)
    metrics.set_meter_provider(provider)
    return metrics.get_meter("knowledge.engine")


tracer = _init_trace()
meter = _init_meter()


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Open a named span with scalar-or-stringified attributes.

    Cost is a no-op when no exporter is configured (the env-gated default
    tracer); callers never import an exporter — only this helper (DIP).

    Args:
        name: Span name (e.g. ``node.IngestNode``, ``search.federated``).
        **attributes: Span attributes; ``None`` values are dropped, non-scalar
            values are stringified (OTEL accepts str/bool/int/float).

    Yields:
        Any: The active span (for post-hoc ``set_attribute`` / status).
    """
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is None:
                continue
            current.set_attribute(key, value if isinstance(value, (str, bool, int, float)) else str(value))
        yield current


# Metric instruments, lazily bound in callers.
token_usage = meter.create_counter(
    "gen_ai.client.token_usage", unit="tokens", description="Token usage by provider/model/operation"
)
operation_duration = meter.create_histogram(
    "gen_ai.client.operation.duration", unit="ms", description="Latency of provider operations"
)
search_provider_calls = meter.create_counter(
    "knowledge_engine.search_provider.calls",
    unit="1",
    description="General-web chain attempts per provider with outcome (success/empty/failure/quota/breaker)",
)
