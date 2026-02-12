"""Grafana Stack telemetry adapter.

Implements TelemetryPort by querying the Grafana Stack (Tempo for traces,
Loki for logs, Prometheus for metrics).

The Grafana Stack provides a comprehensive observability platform with:
- Tempo: Distributed tracing (stores traces)
- Loki: Log aggregation
- Prometheus: Metrics collection
- Grafana: Visualization and dashboarding

This adapter federates queries across these backends to provide a unified
view of errors, traces, and logs.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from rounds.core.models import (
    ErrorEvent,
    LogEntry,
    Severity,
    SpanNode,
    StackFrame,
    TraceTree,
)
from rounds.core.ports import TelemetryPort

logger = logging.getLogger(__name__)


def _is_valid_identifier(identifier: str) -> bool:
    """Validate an identifier to prevent LogQL injection.

    Args:
        identifier: The identifier to validate.

    Returns:
        True if identifier is safe for queries.
    """
    if not isinstance(identifier, str):
        return False
    # Allow alphanumeric, underscore, hyphen, and dot
    return bool(identifier) and all(c.isalnum() or c in "_-." for c in identifier)


def _is_valid_trace_id(trace_id: str) -> bool:
    """Validate trace ID format (128-bit hex string).

    Args:
        trace_id: The trace ID to validate.

    Returns:
        True if the trace ID is valid format.
    """
    if not isinstance(trace_id, str):
        return False
    return bool(trace_id) and all(c in "0123456789abcdefABCDEF" for c in trace_id)


class GrafanaStackTelemetryAdapter(TelemetryPort):
    """Grafana Stack telemetry adapter (Tempo + Loki + Prometheus)."""

    def __init__(
        self,
        tempo_url: str,
        loki_url: str,
        prometheus_url: str = "",
    ):
        """Initialize Grafana Stack adapter.

        Args:
            tempo_url: Base URL for Tempo API (e.g., http://localhost:3200)
            loki_url: Base URL for Loki API (e.g., http://localhost:3100)
            prometheus_url: Optional base URL for Prometheus (e.g., http://localhost:9090)
        """
        self.tempo_url = tempo_url.rstrip("/")
        self.loki_url = loki_url.rstrip("/")
        self.prometheus_url = prometheus_url.rstrip("/") if prometheus_url else ""

        self.tempo_client = httpx.AsyncClient(base_url=self.tempo_url, timeout=30.0)
        self.loki_client = httpx.AsyncClient(base_url=self.loki_url, timeout=30.0)
        self.prometheus_client: httpx.AsyncClient | None = None

        if self.prometheus_url:
            self.prometheus_client = httpx.AsyncClient(
                base_url=self.prometheus_url, timeout=30.0
            )

    async def __aenter__(self) -> "GrafanaStackTelemetryAdapter":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close all HTTP clients and clean up resources."""
        await self.tempo_client.aclose()
        await self.loki_client.aclose()
        if self.prometheus_client:
            await self.prometheus_client.aclose()

    async def get_recent_errors(
        self, since: datetime, services: list[str] | None = None
    ) -> list[ErrorEvent]:
        """Return error events since the given timestamp.

        Queries Loki for error logs across services.

        Args:
            since: Timestamp to query from.
            services: Optional list of service names to filter by.

        Returns:
            List of ErrorEvent objects.

        Raises:
            Exception: If API is unavailable.
        """
        errors: list[ErrorEvent] = []

        try:
            # Build LogQL query for errors
            service_filter = ""
            if services:
                # Validate service names to prevent LogQL injection
                valid_services = [s for s in services if _is_valid_identifier(s)]
                if not valid_services:
                    logger.warning(f"No valid service names provided: {services}")
                    return []
                service_regex = "|".join(valid_services)
                service_filter = f' | json | service =~ "{service_regex}"'

            query = f'{{level="error"}}{service_filter} | json'

            # Convert timestamp to nanoseconds for Loki
            start_ns = int(since.timestamp() * 1e9)
            end_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)

            response = await self.loki_client.get(
                "/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": start_ns,
                    "end": end_ns,
                    "limit": 1000,
                },
            )

            if response.status_code == 200:
                data = response.json()
                streams = data.get("data", {}).get("result", [])

                for stream in streams:
                    for timestamp, log_line in stream.get("values", []):
                        # Parse log entry
                        try:
                            log_data = json.loads(log_line)
                            error_event = self._parse_error_from_log(log_data)
                            if error_event:
                                errors.append(error_event)
                        except (json.JSONDecodeError, ValueError):
                            continue

            return errors

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch errors from Grafana Stack: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching errors: {e}", exc_info=True)
            raise

    @staticmethod
    def _parse_error_from_log(log_data: dict[str, Any]) -> ErrorEvent | None:
        """Parse ErrorEvent from a Loki log entry.

        Args:
            log_data: Parsed JSON log entry.

        Returns:
            ErrorEvent object or None if parsing fails.
        """
        try:
            error_type = log_data.get("error_type", "Exception")
            error_message = log_data.get("message", "")
            service = log_data.get("service", "")

            # Extract stack frames if available
            stack_frames = []
            if "stack" in log_data:
                stack_str = log_data["stack"]
                stack_frames = GrafanaStackTelemetryAdapter._parse_stack_frames(
                    stack_str
                )

            # Parse timestamp
            timestamp = datetime.now(timezone.utc)
            if "timestamp" in log_data:
                try:
                    timestamp = datetime.fromisoformat(log_data["timestamp"])
                except (ValueError, TypeError):
                    pass

            return ErrorEvent(
                trace_id=log_data.get("trace_id", ""),
                span_id=log_data.get("span_id", ""),
                service=service,
                error_type=error_type,
                error_message=error_message,
                stack_frames=tuple(stack_frames),
                timestamp=timestamp,
                attributes=log_data,
                severity=Severity.ERROR,
            )

        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"Failed to parse error from log: {e}")
            return None

    @staticmethod
    def _parse_stack_frames(stack_str: str) -> list[StackFrame]:
        """Parse stack frames from a stack trace string.

        Args:
            stack_str: Stack trace as a string.

        Returns:
            List of StackFrame objects.
        """
        frames: list[StackFrame] = []

        try:
            lines = stack_str.split("\n")
            for line in lines:
                line = line.strip()
                if not line or line.startswith("Traceback"):
                    continue

                # Parse frame line: "File "path.py", line N, in function"
                if "File" in line and "in " in line:
                    parts = line.split(", ")
                    if len(parts) >= 2:
                        filename = (
                            parts[0]
                            .replace('File "', "")
                            .replace('"', "")
                        )
                        function = (
                            parts[-1]
                            .replace("in ", "")
                            .strip()
                        )
                        module = filename.replace(".py", "").replace("/", ".")

                        frame = StackFrame(
                            module=module,
                            function=function,
                            filename=filename,
                            lineno=None,
                        )
                        frames.append(frame)

        except Exception as e:
            logger.debug(f"Failed to parse stack frames: {e}")

        return frames

    async def get_trace(self, trace_id: str) -> TraceTree:
        """Return the full span tree for a trace.

        Queries Tempo for the trace.

        Args:
            trace_id: Tempo trace ID.

        Returns:
            TraceTree object with span hierarchy.

        Raises:
            ValueError: If trace not found or invalid.
            Exception: If API error occurs.
        """
        if not _is_valid_trace_id(trace_id):
            raise ValueError(f"Invalid trace ID format: {trace_id}")

        try:
            response = await self.tempo_client.get(f"/api/traces/{trace_id}")
            response.raise_for_status()

            data = response.json()
            batches = data.get("batches", [])

            if not batches:
                raise ValueError(f"No trace data found for ID {trace_id}")

            # Extract spans from batches
            all_spans = []
            for batch in batches:
                for scope_span in batch.get("scopeSpans", []):
                    for span in scope_span.get("spans", []):
                        all_spans.append(span)

            if not all_spans:
                raise ValueError(f"No spans found for trace {trace_id}")

            # Build span tree
            span_dicts: dict[str, dict[str, Any]] = {}
            root_span_id = None

            # First pass: create intermediate dicts
            for span in all_spans:
                span_id = span.get("spanId", "")
                parent_id = span.get("parentSpanId")

                span_dicts[span_id] = {
                    "data": span,
                    "children": [],
                }

                if not parent_id:
                    root_span_id = span_id

            # Second pass: build parent-child relationships
            for span_id, span_dict in span_dicts.items():
                parent_id = span_dict["data"].get("parentSpanId")
                if parent_id and parent_id in span_dicts:
                    span_dicts[parent_id]["children"].append(span_id)

            # Third pass: construct SpanNode tree
            span_node_map: dict[str, SpanNode] = {}

            def build_span_node(span_id: str) -> SpanNode:
                """Recursively build SpanNode."""
                if span_id in span_node_map:
                    return span_node_map[span_id]

                span_dict = span_dicts[span_id]
                span_data = span_dict["data"]
                child_ids = span_dict["children"]

                # Build children first
                children = tuple(build_span_node(child_id) for child_id in child_ids)

                # Get attributes
                attributes = {}
                for attr in span_data.get("attributes", []):
                    key = attr.get("key", "")
                    value = attr.get("value", {}).get("stringValue", "")
                    attributes[key] = value

                # Determine status
                status_code = span_data.get("status", {}).get("code", 0)
                status = "error" if status_code != 0 else "ok"

                node = SpanNode(
                    span_id=span_id,
                    parent_id=span_data.get("parentSpanId"),
                    service=span_data.get("instrumentationScope", {}).get("name", ""),
                    operation=span_data.get("name", ""),
                    duration_ms=int(
                        (span_data.get("endTimeUnixNano", 0) -
                         span_data.get("startTimeUnixNano", 0)) / 1e6
                    ),
                    status=status,
                    attributes=attributes,
                    events=tuple(),
                    children=children,
                )

                span_node_map[span_id] = node
                return node

            # Build from root
            if not root_span_id and span_dicts:
                root_span_id = next(iter(span_dicts.keys()))

            if not root_span_id:
                raise ValueError(f"Could not find root span for trace {trace_id}")

            root_node = build_span_node(root_span_id)

            # Collect error spans for the trace
            error_spans: list[SpanNode] = []
            for span_id, node in span_dicts.items():
                if node["data"].get("status", {}).get("code", 0) != 0:
                    error_spans.append(build_span_node(span_id))

            return TraceTree(
                trace_id=trace_id,
                root_span=root_node,
                error_spans=tuple(error_spans),
            )

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch trace from Grafana Tempo: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching trace: {e}", exc_info=True)
            raise

    async def get_traces(self, trace_ids: list[str]) -> list[TraceTree]:
        """Batch retrieve multiple traces.

        Args:
            trace_ids: List of trace IDs to retrieve.

        Returns:
            List of TraceTree objects. Partial results may be returned if some
            traces cannot be fetched.

        Raises:
            ValueError: If any trace ID format is invalid.
        """
        # Validate all trace IDs upfront
        for trace_id in trace_ids:
            if not _is_valid_trace_id(trace_id):
                raise ValueError(f"Invalid trace ID format: {trace_id}")

        traces: list[TraceTree] = []

        for trace_id in trace_ids:
            try:
                trace = await self.get_trace(trace_id)
                traces.append(trace)
            except Exception as e:
                logger.warning(f"Failed to fetch trace {trace_id}: {e}")
                continue

        return traces

    async def get_correlated_logs(
        self, trace_ids: list[str], window_minutes: int = 5
    ) -> list[LogEntry]:
        """Retrieve logs correlated with the given traces.

        Includes a time window around each trace for context.

        Args:
            trace_ids: List of trace IDs to correlate logs with.
            window_minutes: Time window (minutes) before/after traces to include.

        Returns:
            List of LogEntry objects correlated with the traces.
            Empty list if no logs found.

        Raises:
            ValueError: If any trace ID format is invalid.
        """
        # Validate all trace IDs upfront
        for trace_id in trace_ids:
            if not _is_valid_trace_id(trace_id):
                raise ValueError(f"Invalid trace ID format: {trace_id}")

        logs: list[LogEntry] = []

        try:
            # Build LogQL query to correlate logs with traces
            # Use correct LogQL syntax with stream selectors
            trace_regex = "|".join(trace_ids)
            query = f'{{trace_id=~"{trace_regex}"}}'

            response = await self.loki_client.get(
                "/loki/api/v1/query",
                params={"query": query},
            )

            if response.status_code == 200:
                data = response.json()
                streams = data.get("data", {}).get("result", [])

                for stream in streams:
                    for timestamp, log_line in stream.get("values", []):
                        log_entry = LogEntry(
                            timestamp=datetime.fromtimestamp(int(timestamp) / 1e9, tz=timezone.utc),
                            severity=Severity.INFO,
                            body=log_line,
                            attributes={},
                            trace_id=None,
                            span_id=None,
                        )
                        logs.append(log_entry)

        except Exception as e:
            logger.error(
                f"Failed to fetch correlated logs: {e}",
                extra={"trace_ids": trace_ids},
                exc_info=True,
            )
            raise

        return logs

    async def get_events_for_signature(
        self, fingerprint: str, limit: int = 100
    ) -> list[ErrorEvent]:
        """Retrieve recent errors matching a fingerprint.

        Filters errors by computing fingerprints locally and matching against
        the requested fingerprint. This compensates for Grafana Stack not having
        native fingerprint support.

        Args:
            fingerprint: Signature fingerprint to match against.
            limit: Maximum number of matching events to return.

        Returns:
            List of ErrorEvent objects with matching fingerprints.
            May be empty if no recent matches found.

        Raises:
            Exception: If telemetry backend is unreachable.
        """
        from datetime import timedelta
        from rounds.core.fingerprint import Fingerprinter

        # Fetch recent errors from last 24 hours
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        all_errors = await self.get_recent_errors(since)

        # Filter by fingerprint using local computation
        matching_errors = []
        fingerprinter = Fingerprinter()

        for error in all_errors:
            if fingerprinter.fingerprint(error) == fingerprint:
                matching_errors.append(error)
                if len(matching_errors) >= limit:
                    break

        return matching_errors
