"""Fake TelemetryPort implementation for testing."""

from datetime import datetime
from typing import Any

from rounds.core.models import ErrorEvent, LogEntry, SpanNode, TraceTree
from rounds.core.ports import TelemetryPort


class FakeTelemetryPort(TelemetryPort):
    """In-memory telemetry adapter for testing.

    Allows tests to pre-populate error events, traces, and logs that will be
    returned when the core services query the telemetry backend.
    """

    def __init__(self):
        """Initialize with empty collections."""
        self.errors: dict[datetime, list[ErrorEvent]] = {}
        self.traces: dict[str, TraceTree] = {}
        self.logs: list[LogEntry] = []
        self.signature_events: dict[str, list[ErrorEvent]] = {}
        self.get_recent_errors_call_count = 0
        self.get_trace_call_count = 0
        self.get_traces_call_count = 0
        self.get_correlated_logs_call_count = 0
        self.get_events_for_signature_call_count = 0

    def add_error(self, error: ErrorEvent) -> None:
        """Add an error event to the fake telemetry backend."""
        if error.timestamp not in self.errors:
            self.errors[error.timestamp] = []
        self.errors[error.timestamp].append(error)

    def add_errors(self, errors: list[ErrorEvent]) -> None:
        """Add multiple error events."""
        for error in errors:
            self.add_error(error)

    def add_trace(self, trace: TraceTree) -> None:
        """Add a trace to the fake backend."""
        self.traces[trace.trace_id] = trace

    def add_traces(self, traces: list[TraceTree]) -> None:
        """Add multiple traces."""
        for trace in traces:
            self.add_trace(trace)

    def add_log(self, log: LogEntry) -> None:
        """Add a log entry to the fake backend."""
        self.logs.append(log)

    def add_logs(self, logs: list[LogEntry]) -> None:
        """Add multiple log entries."""
        self.logs.extend(logs)

    def add_signature_events(self, fingerprint: str, events: list[ErrorEvent]) -> None:
        """Add events for a specific signature fingerprint."""
        self.signature_events[fingerprint] = events

    async def get_recent_errors(
        self, since: datetime, services: list[str] | None = None
    ) -> list[ErrorEvent]:
        """Get recent errors since the given timestamp.

        Returns all errors with timestamp >= since.
        Filters by service if services list is provided.
        """
        self.get_recent_errors_call_count += 1

        result = []
        for ts, errors in self.errors.items():
            if ts >= since:
                result.extend(errors)

        if services:
            result = [e for e in result if e.service in services]

        return result

    async def get_trace(self, trace_id: str) -> TraceTree:
        """Get a single trace by ID.

        Returns a synthetic empty trace if not found.
        """
        self.get_trace_call_count += 1

        if trace_id in self.traces:
            return self.traces[trace_id]

        # Return a synthetic empty trace if not found
        root_span = SpanNode(
            span_id=trace_id,
            parent_id=None,
            service="unknown-service",
            operation="unknown-op",
            duration_ms=0,
            status="unknown",
            attributes={},
            events=(),
        )
        return TraceTree(trace_id=trace_id, root_span=root_span, error_spans=())

    async def get_traces(self, trace_ids: list[str]) -> list[TraceTree]:
        """Get multiple traces by ID.

        Returns synthetic empty traces for any IDs not found.
        """
        self.get_traces_call_count += 1

        result = []
        for trace_id in trace_ids:
            if trace_id in self.traces:
                result.append(self.traces[trace_id])
            else:
                # Return a synthetic empty trace
                root_span = SpanNode(
                    span_id=trace_id,
                    parent_id=None,
                    service="unknown-service",
                    operation="unknown-op",
                    duration_ms=0,
                    status="unknown",
                    attributes={},
                    events=(),
                )
                result.append(
                    TraceTree(trace_id=trace_id, root_span=root_span, error_spans=())
                )

        return result

    async def get_correlated_logs(
        self, trace_ids: list[str], window_minutes: int = 5
    ) -> list[LogEntry]:
        """Get logs correlated with given trace IDs.

        Returns logs that belong to any of the trace IDs.
        """
        self.get_correlated_logs_call_count += 1

        result = []
        for log in self.logs:
            if log.trace_id in trace_ids:
                result.append(log)

        return result

    async def get_events_for_signature(
        self, fingerprint: str, limit: int = 5
    ) -> list[ErrorEvent]:
        """Get events for a specific error signature.

        Returns pre-added events for the signature, limited by count.
        """
        self.get_events_for_signature_call_count += 1

        if fingerprint in self.signature_events:
            events = self.signature_events[fingerprint]
            return events[:limit]

        return []

    def reset(self) -> None:
        """Reset all collected data and call counts."""
        self.errors.clear()
        self.traces.clear()
        self.logs.clear()
        self.signature_events.clear()
        self.get_recent_errors_call_count = 0
        self.get_trace_call_count = 0
        self.get_traces_call_count = 0
        self.get_correlated_logs_call_count = 0
        self.get_events_for_signature_call_count = 0
