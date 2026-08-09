"""OpenTelemetry instrumentation for Rounds CLI operations.

This module provides telemetry initialization and utilities for instrumenting
the rounds diagnostic system itself. It enables observability of CLI commands,
diagnosis operations, and error patterns within the rounds system.
"""

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Status, StatusCode

if TYPE_CHECKING:
    from rounds.adapters.scheduler.daemon import DaemonScheduler

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


def initialize_metrics(
    service_name: str = "rounds-cli",
    otlp_endpoint: str | None = None,
    enable_console_export: bool = False,
    export_interval_millis: int = 60000,
) -> metrics.Meter:
    """Initialize OpenTelemetry metrics for the rounds self-observability dashboard.

    Args:
        service_name: Service name for the meter (default: "rounds-cli").
        otlp_endpoint: OTLP HTTP endpoint URL for metrics. If None, no OTLP
            metric export is configured.
        enable_console_export: If True, also export metrics to console for debugging.
        export_interval_millis: How often the periodic metric readers export
            collected metrics, in milliseconds.

    Returns:
        Configured meter instance.
    """
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": "0.1.0",
        }
    )

    readers = []

    if otlp_endpoint:
        try:
            otlp_exporter = OTLPMetricExporter(endpoint=otlp_endpoint)
            readers.append(
                PeriodicExportingMetricReader(
                    otlp_exporter, export_interval_millis=export_interval_millis
                )
            )
            logger.info(f"OpenTelemetry OTLP metric exporter initialized: {otlp_endpoint}")
        except Exception as e:
            logger.warning(f"Failed to initialize OTLP metric exporter: {e}", exc_info=True)

    if enable_console_export:
        readers.append(
            PeriodicExportingMetricReader(
                ConsoleMetricExporter(), export_interval_millis=export_interval_millis
            )
        )
        logger.debug("OpenTelemetry console metric exporter enabled")

    provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(provider)

    meter = metrics.get_meter(__name__)
    logger.info(f"OpenTelemetry metrics initialized for service: {service_name}")
    return meter


def register_dashboard_gauges(meter: metrics.Meter, scheduler: "DaemonScheduler") -> None:
    """Register observable gauges backing the self-observability dashboard.

    Reads are backed entirely by in-memory state already maintained by the
    daemon scheduler (signature counts refreshed once per poll cycle, cost
    tracked live as diagnoses complete), so callbacks perform no I/O and are
    safe to invoke synchronously from the metrics SDK's export thread.

    Args:
        meter: Meter obtained from initialize_metrics().
        scheduler: DaemonScheduler instance whose live state backs the gauges.
    """

    def _signature_counts_callback(options: CallbackOptions) -> Iterable[Observation]:
        for status, count in scheduler.signature_counts_by_status.items():
            yield Observation(count, {"status": status})

    meter.create_observable_gauge(
        "rounds.signatures.count_by_status",
        callbacks=[_signature_counts_callback],
        description="Current count of error signatures grouped by status",
        unit="{signature}",
    )

    def _daily_cost_total_callback(options: CallbackOptions) -> Iterable[Observation]:
        yield Observation(scheduler.daily_cost_usd, {})

    meter.create_observable_gauge(
        "rounds.diagnosis.daily_cost_usd",
        callbacks=[_daily_cost_total_callback],
        description="Total diagnosis spend accrued today, in USD",
        unit="{usd}",
    )

    def _daily_cost_by_step_callback(options: CallbackOptions) -> Iterable[Observation]:
        for step, cost in scheduler.cost_by_step.items():
            yield Observation(cost, {"step": step})

    meter.create_observable_gauge(
        "rounds.diagnosis.daily_cost_usd_by_step",
        callbacks=[_daily_cost_by_step_callback],
        description="Diagnosis spend accrued today, broken down by pipeline step, in USD",
        unit="{usd}",
    )


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
    """Shutdown OpenTelemetry and flush all pending spans and metrics.

    This should be called before application exit to ensure all spans and
    metrics are exported and resources are cleaned up properly.
    """
    try:
        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
            logger.info("OpenTelemetry tracing shut down successfully")
    except Exception as e:
        logger.warning(f"Failed to shutdown tracing gracefully: {e}", exc_info=True)

    try:
        meter_provider = metrics.get_meter_provider()
        if hasattr(meter_provider, "shutdown"):
            meter_provider.shutdown()
            logger.info("OpenTelemetry metrics shut down successfully")
    except Exception as e:
        logger.warning(f"Failed to shutdown metrics gracefully: {e}", exc_info=True)
