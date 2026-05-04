"""OpenTelemetry bootstrap. No-op when no OTLP endpoint is configured."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from legalbot.core.config import get_settings

_configured = False


def configure_tracing() -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    if not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        _configured = True
        return

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    resource = Resource.create({"service.name": settings.OTEL_SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=str(settings.OTEL_EXPORTER_OTLP_ENDPOINT)))
    )
    trace.set_tracer_provider(provider)
    _configured = True


def get_tracer(name: str) -> trace.Tracer:
    configure_tracing()
    return trace.get_tracer(name)
