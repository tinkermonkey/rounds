"""OpenTelemetry instrumentation for Rounds CLI operations.

This module provides telemetry initialization and utilities for instrumenting
the rounds diagnostic system itself. It enables observability of CLI commands,
diagnosis operations, and error patterns within the rounds system.
"""

import logging
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger(__name__)


def initialize_telemetry(
    service_name: str = "rounds-cli",
    otlp_endpoint: str | None = None,
    enable_console_export: bool = False,
) -> trace.Tracer:
    """Initialize OpenTelemetry tracing for the rounds CLI.

    Args:
        service_name: Service name for the tracer (default: "rounds-cli").
        otlp_endpoint: OTLP HTTP endpoint URL. If None, only console export is used.
        enable_console_export: If True, also export spans to console for debugging.

    Returns:
        Configured tracer instance.
    """
    # Create resource with service name
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": "0.1.0",
        }
    )

    # Create tracer provider
    provider = TracerProvider(resource=resource)

    # Add OTLP exporter if endpoint is provided
    if otlp_endpoint:
        try:
            otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            logger.info(f"OpenTelemetry OTLP exporter initialized: {otlp_endpoint}")
        except Exception as e:
            logger.warning(f"Failed to initialize OTLP exporter: {e}", exc_info=True)

    # Add console exporter for debugging
    if enable_console_export:
        console_exporter = ConsoleSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(console_exporter))
        logger.debug("OpenTelemetry console exporter enabled")

    # Set global tracer provider
    trace.set_tracer_provider(provider)

    # Return tracer
    tracer = trace.get_tracer(__name__)
    logger.info(f"OpenTelemetry initialized for service: {service_name}")
    return tracer


def record_exception_in_span(
    span: trace.Span,
    exception: BaseException,
    *,
    set_status: bool = True,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Record an exception in the current span with additional context.

    Args:
        span: The span to record the exception in.
        exception: The exception to record.
        set_status: If True, set span status to ERROR (default: True).
        attributes: Additional attributes to add to the exception event.
    """
    # Record exception event
    event_attributes = {
        "exception.type": type(exception).__name__,
        "exception.message": str(exception),
    }

    if attributes:
        event_attributes.update(attributes)

    span.record_exception(exception, attributes=event_attributes)

    # Set span status to error
    if set_status:
        span.set_status(Status(StatusCode.ERROR, str(exception)))


def get_tracer(name: str | None = None) -> trace.Tracer:
    """Get a tracer instance.

    Args:
        name: Tracer name (defaults to calling module name).

    Returns:
        Tracer instance.
    """
    return trace.get_tracer(name or __name__)


def shutdown_telemetry() -> None:
    """Shutdown OpenTelemetry and flush all pending spans.

    This should be called before application exit to ensure all spans are
    exported and resources are cleaned up properly.
    """
    try:
        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()  # type: ignore[attr-defined]
            logger.info("OpenTelemetry telemetry shut down successfully")
    except Exception as e:
        logger.warning(f"Failed to shutdown telemetry gracefully: {e}", exc_info=True)
