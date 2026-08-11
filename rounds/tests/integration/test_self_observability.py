"""Integration tests for the Rounds self-observability dashboard instrumentation.

Exercises the real DaemonScheduler._run_loop() against in-memory OTEL exporters
to verify that poll/investigation/resolution cycle spans and latency histograms,
signature-count gauges, and diagnosis-cost gauges are actually emitted when
self-telemetry is enabled - not just that the wiring compiles.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime

import opentelemetry.metrics._internal as metrics_internal
import opentelemetry.trace as trace_api
import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, MetricsData, NumberDataPoint
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from rounds.adapters.scheduler.daemon import DaemonScheduler
from rounds.core.models import PollResult, Severity, Signature, SignatureStatus
from rounds.telemetry import register_dashboard_gauges
from rounds.tests.fakes.poll import FakePollPort
from rounds.tests.fakes.store import FakeSignatureStorePort


@pytest.fixture
def otel_in_memory() -> Iterator[tuple[InMemorySpanExporter, InMemoryMetricReader]]:
    """Install fresh in-memory tracer/meter providers for the duration of a test.

    OpenTelemetry's global providers can only be set once per process (a
    "set once" guard), so this fixture bypasses that guard directly and
    restores the prior global state on teardown to avoid leaking into other
    tests in the same pytest session.
    """
    prev_tracer_provider = trace_api._TRACER_PROVIDER
    prev_tracer_once_done = trace_api._TRACER_PROVIDER_SET_ONCE._done
    prev_meter_provider = metrics_internal._METER_PROVIDER
    prev_meter_once_done = metrics_internal._METER_PROVIDER_SET_ONCE._done

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    trace_api._TRACER_PROVIDER_SET_ONCE._done = False
    trace_api.set_tracer_provider(tracer_provider)

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    metrics_internal._METER_PROVIDER_SET_ONCE._done = False
    metrics_internal.set_meter_provider(meter_provider)

    try:
        yield span_exporter, metric_reader
    finally:
        trace_api._TRACER_PROVIDER = prev_tracer_provider
        trace_api._TRACER_PROVIDER_SET_ONCE._done = prev_tracer_once_done
        metrics_internal._METER_PROVIDER = prev_meter_provider
        metrics_internal._METER_PROVIDER_SET_ONCE._done = prev_meter_once_done


def _make_signature(status: SignatureStatus, service: str = "api") -> Signature:
    now = datetime.now(UTC)
    return Signature(
        id=f"sig-{status.value}",
        fingerprint=f"fp-{status.value}",
        error_type="RuntimeError",
        service=service,
        message_template="boom",
        stack_hash="hash",
        first_seen=now,
        last_seen=now,
        occurrence_count=1,
        status=status,
        diagnosis=None,
        tags=frozenset(),
        max_severity=Severity.ERROR,
    )


@pytest.mark.asyncio
async def test_run_loop_emits_cycle_spans_and_latency_histograms(
    otel_in_memory: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    """Poll, investigation, and resolution cycles each emit a span and a
    histogram data point, with the counts store used for signature gauges."""
    span_exporter, metric_reader = otel_in_memory

    store = FakeSignatureStorePort()
    await store.save(_make_signature(SignatureStatus.NEW))
    await store.save(_make_signature(SignatureStatus.DIAGNOSED))

    poll_port = FakePollPort()
    poll_port.set_default_poll_result(
        PollResult(
            errors_found=1,
            new_signatures=1,
            updated_signatures=0,
            investigations_queued=1,
            timestamp=datetime.now(UTC),
        )
    )

    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        signature_store=store,
    )
    register_dashboard_gauges(metrics_internal.get_meter(__name__), scheduler)

    async def _stop_after_first_cycle() -> None:
        while poll_port.execute_investigation_cycle_call_count < 1:
            await asyncio.sleep(0.01)
        scheduler.running = False

    scheduler.running = True
    await asyncio.gather(scheduler._run_loop(), _stop_after_first_cycle())

    span_names = {span.name for span in span_exporter.get_finished_spans()}
    assert "rounds.daemon.poll_cycle" in span_names
    assert "rounds.daemon.resolution_cycle" in span_names
    assert "rounds.daemon.investigation_cycle" in span_names

    metric_names = _collect_metric_names(metric_reader.get_metrics_data())
    assert "rounds.daemon.poll_cycle.duration" in metric_names
    assert "rounds.daemon.resolution_cycle.duration" in metric_names
    assert "rounds.daemon.investigation_cycle.duration" in metric_names

    # Signature-count gauge is refreshed from the store once per cycle.
    assert scheduler.signature_counts_by_status == {"new": 1, "diagnosed": 1}


@pytest.mark.asyncio
async def test_dashboard_gauges_report_live_cost_and_signature_counts(
    otel_in_memory: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    """The observable gauges reflect the scheduler's live cost/count state
    at collection time, with no polling loop required."""
    _, metric_reader = otel_in_memory

    store = FakeSignatureStorePort()
    await store.save(_make_signature(SignatureStatus.INVESTIGATING))

    scheduler = DaemonScheduler(
        poll_port=FakePollPort(),
        poll_interval_seconds=1,
        signature_store=store,
    )
    register_dashboard_gauges(metrics_internal.get_meter(__name__), scheduler)

    await scheduler.record_cost("diagnose", 1.50, service="api")
    await scheduler._refresh_signature_counts()

    data_points = _collect_gauge_data_points(
        metric_reader.get_metrics_data(), "rounds.diagnosis.daily_cost_usd"
    )
    assert data_points == [1.5]

    status_points = _collect_gauge_data_points(
        metric_reader.get_metrics_data(), "rounds.signatures.count_by_status"
    )
    assert status_points == [1]


def _collect_metric_names(metrics_data: MetricsData | None) -> set[str]:
    assert metrics_data is not None
    names: set[str] = set()
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                names.add(metric.name)
    return names


def _collect_gauge_data_points(metrics_data: MetricsData | None, metric_name: str) -> list[float]:
    assert metrics_data is not None
    values: list[float] = []
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name != metric_name:
                    continue
                for point in metric.data.data_points:
                    assert isinstance(point, NumberDataPoint)
                    values.append(point.value)
    return values
