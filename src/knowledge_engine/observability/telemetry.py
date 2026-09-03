"""OpenTelemetry GenAI instrumentation (ADR 0006).

Initializes a tracer + meter exported via OTLP to a free self-hosted sink
(JSONL file or a local OTLP collector). No paid SaaS. Recomputed cost via
:mod:`knowledge_engine.core.pricing`, never vendor-reported.
"""

from __future__ import annotations

import os

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_OTEL_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "knowledge-engine")
# No-op by default: only export when explicitly configured via env, avoiding
# network attempts (e.g. toward localhost:4318) on plain imports and CI.
_TRACES_EXPORTER = os.environ.get("OTEL_TRACES_EXPORTER", "").strip()
_METRICS_EXPORTER = os.environ.get("OTEL_METRICS_EXPORTER", "none").strip()


def _resource() -> Resource:
    return Resource.create({"service.name": _SERVICE_NAME})


def _init_trace() -> trace.Tracer:
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
    """Minimal fallback exporter printing spans when OTLP is disabled."""

    def export(self, spans, timeout_millis=30000):
        for span in spans:
            print(span)
        return _OkResult()

    def shutdown(self):
        pass


class _OkResult:
    status = "success"


def _init_meter() -> metrics.Meter:
    readers = []
    if _METRICS_EXPORTER == "console":
        readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter()))
    provider = MeterProvider(resource=_resource(), metric_readers=readers)
    metrics.set_meter_provider(provider)
    return metrics.get_meter("knowledge.engine")


tracer = _init_trace()
meter = _init_meter()

# Metric instruments, lazily bound in callers.
token_usage = meter.create_counter(
    "gen_ai.client.token_usage", unit="tokens", description="Token usage by provider/model/operation"
)
operation_duration = meter.create_histogram(
    "gen_ai.client.operation.duration", unit="ms", description="Latency of provider operations"
)