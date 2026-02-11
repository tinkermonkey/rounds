"""SigNoz telemetry adapter.

Implements TelemetryPort by querying SigNoz REST API for errors, traces, and logs.
Normalizes SigNoz-specific data structures into core domain models.
"""

import logging
from datetime import datetime
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


class SigNozTelemetryAdapter(TelemetryPort):
    """SigNoz-backed telemetry adapter via REST API."""

    def __init__(self, api_url: str, api_key: str = ""):
        """Initialize SigNoz adapter.

        Args:
            api_url: Base URL for SigNoz API (e.g., http://localhost:4418)
            api_key: Optional API key for authentication
        """
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            base_url=self.api_url,
            headers=self._get_headers(),
            timeout=30.0,
        )

    def _get_headers(self) -> dict[str, str]:
        """Build request headers."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.client.aclose()

    async def close(self) -> None:
        """Close the httpx client and clean up resources.

        Must be called when done using the adapter if not using it as a context manager.
        """
        await self.client.aclose()

    async def get_recent_errors(
        self, since: datetime, services: list[str] | None = None
    ) -> list[ErrorEvent]:
        """Return error events since the given timestamp."""
        try:
            # Build ClickHouse query for errors
            service_filter = ""
            if services:
                # Validate service names to prevent injection
                # Services should be alphanumeric with dots/hyphens
                invalid_services = [
                    s for s in services if not self._is_valid_identifier(s)
                ]
                if invalid_services:
                    logger.warning(
                        f"Skipping invalid service names: {invalid_services}"
                    )
                    services = [s for s in services if self._is_valid_identifier(s)]

                if services:
                    service_list = "','".join(services)
                    service_filter = f"AND serviceName IN ('{service_list}')"

            query = f"""
                SELECT
                    traceID,
                    spanID,
                    serviceName,
                    exceptionType,
                    exceptionMessage,
                    timestamp,
                    attributes,
                    severityText
                FROM traces
                WHERE timestamp > {int(since.timestamp() * 1e9)}
                    AND exceptionType != ''
                    {service_filter}
                ORDER BY timestamp DESC
                LIMIT 1000
            """

            response = await self.client.post(
                "/api/v1/query_range",
                json={"query": query},
            )
            response.raise_for_status()

            data = response.json()
            errors = []

            for result in data.get("result", []):
                for value in result.get("values", []):
                    error = self._parse_error_event(value)
                    if error:
                        errors.append(error)

            return errors

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch errors from SigNoz: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching errors: {e}")
            raise

    async def get_trace(self, trace_id: str) -> TraceTree:
        """Return the full span tree for a trace."""
        try:
            response = await self.client.get(
                f"/api/v1/traces/{trace_id}",
            )
            response.raise_for_status()

            data = response.json()
            spans_data = data.get("spans", [])

            if not spans_data:
                raise ValueError(f"No spans found for trace {trace_id}")

            # Build a mutable intermediate structure to handle parent-child relationships
            span_dicts = {}
            root_span_data = None

            # First pass: create intermediate dicts with children lists
            for span_data in spans_data:
                span_id = span_data["spanID"]
                span_dicts[span_id] = {
                    "data": span_data,
                    "children": [],
                }
                if span_data.get("parentSpanID") is None:
                    root_span_data = span_id

            # Second pass: build parent-child relationships
            for span_id, span_dict in span_dicts.items():
                parent_id = span_dict["data"].get("parentSpanID")
                if parent_id and parent_id in span_dicts:
                    span_dicts[parent_id]["children"].append(span_id)

            # Third pass: construct SpanNode tree (bottom-up, leaves first)
            span_node_map = {}

            def build_span_node(span_id: str) -> SpanNode:
                """Recursively build SpanNode with all children."""
                if span_id in span_node_map:
                    return span_node_map[span_id]

                span_dict = span_dicts[span_id]
                span_data = span_dict["data"]
                child_ids = span_dict["children"]

                # Recursively build children first
                children = tuple(build_span_node(child_id) for child_id in child_ids)

                node = SpanNode(
                    span_id=span_data.get("spanID", ""),
                    parent_id=span_data.get("parentSpanID"),
                    service=span_data.get("serviceName", ""),
                    operation=span_data.get("operationName", ""),
                    duration_ms=span_data.get("duration", 0) / 1e6,
                    status=span_data.get("status", "unset"),
                    attributes=span_data.get("attributes", {}),
                    events=tuple(span_data.get("events", [])),
                    children=children,
                )
                span_node_map[span_id] = node
                return node

            # Build the tree from root
            if not root_span_data:
                root_span_data = next(iter(span_dicts.keys())) if span_dicts else None

            if not root_span_data:
                raise ValueError(f"Cannot determine root span for trace {trace_id}")

            root_span = build_span_node(root_span_data)

            # Collect error spans
            error_spans = []

            def collect_error_spans(node: SpanNode):
                """Recursively collect spans with error status."""
                if node.status == "error" or "error" in (
                    node.attributes.get("otel.status_code") or ""
                ):
                    error_spans.append(node)
                for child in node.children:
                    collect_error_spans(child)

            collect_error_spans(root_span)

            return TraceTree(
                trace_id=trace_id,
                root_span=root_span,
                error_spans=tuple(error_spans),
            )

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch trace {trace_id} from SigNoz: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching trace: {e}")
            raise

    async def get_traces(self, trace_ids: list[str]) -> list[TraceTree]:
        """Batch trace retrieval."""
        traces = []
        for trace_id in trace_ids:
            try:
                trace = await self.get_trace(trace_id)
                traces.append(trace)
            except Exception as e:
                logger.warning(f"Failed to fetch trace {trace_id}: {e}")
                # Continue with other traces
        return traces

    async def get_correlated_logs(
        self, trace_ids: list[str], window_minutes: int = 5
    ) -> list[LogEntry]:
        """Return logs correlated with the given traces."""
        try:
            if not trace_ids:
                return []

            # Validate trace IDs to prevent injection
            valid_trace_ids = [
                tid for tid in trace_ids if self._is_valid_trace_id(tid)
            ]
            if not valid_trace_ids:
                logger.warning("No valid trace IDs provided")
                return []

            # Build ClickHouse query for logs
            trace_list = "','".join(valid_trace_ids)
            query = f"""
                SELECT
                    timestamp,
                    severityText,
                    body,
                    attributes,
                    traceID,
                    spanID
                FROM logs
                WHERE traceID IN ('{trace_list}')
                ORDER BY timestamp DESC
                LIMIT 500
            """

            response = await self.client.post(
                "/api/v1/query_range",
                json={"query": query},
            )
            response.raise_for_status()

            data = response.json()
            logs = []

            for result in data.get("result", []):
                for value in result.get("values", []):
                    log = self._parse_log_entry(value)
                    if log:
                        logs.append(log)

            return logs

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch correlated logs: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching logs: {e}")
            raise

    async def get_events_for_signature(
        self, fingerprint: str, limit: int = 5
    ) -> list[ErrorEvent]:
        """Return recent events matching a known fingerprint.

        Note: SigNoz doesn't have native fingerprint support.
        Returns errors from the last 24 hours as a reasonable compromise.
        A real implementation would tag errors with fingerprints in the telemetry backend.

        Args:
            fingerprint: Signature fingerprint (currently unused due to SigNoz limitations).
            limit: Maximum number of events to return.

        Returns:
            List of recent ErrorEvent objects (up to limit).
        """
        # Return recent errors from last 24 hours
        from datetime import timedelta

        since = datetime.now(datetime.timezone.utc) - timedelta(hours=24)

        # Fetch recent errors and limit results
        all_errors = await self.get_recent_errors(since)
        return all_errors[:limit]

    def _parse_error_event(self, span_data: dict[str, Any]) -> ErrorEvent | None:
        """Parse a span with exception into an ErrorEvent."""
        try:
            trace_id = span_data.get("traceID", "")
            span_id = span_data.get("spanID", "")
            service = span_data.get("serviceName", "")
            error_type = span_data.get("exceptionType", "")
            error_message = span_data.get("exceptionMessage", "")

            # SigNoz timestamps are in nanoseconds, convert to seconds
            timestamp_ns = int(span_data.get("timestamp", 0))
            timestamp = datetime.fromtimestamp(timestamp_ns / 1e9)
            severity_text = span_data.get("severityText", "ERROR")

            if not error_type:
                return None

            # Parse stack frames from attributes
            stack_frames = ()
            attributes = span_data.get("attributes", {})

            if isinstance(attributes, dict):
                stack_trace = attributes.get("exception.stacktrace", "")
                if stack_trace:
                    stack_frames = self._parse_stack_trace(stack_trace)

            return ErrorEvent(
                trace_id=trace_id,
                span_id=span_id,
                service=service,
                error_type=error_type,
                error_message=error_message,
                stack_frames=stack_frames,
                timestamp=timestamp,
                attributes=attributes if isinstance(attributes, dict) else {},
                severity=self._parse_severity(severity_text),
            )

        except Exception as e:
            logger.warning(f"Failed to parse error event: {e}")
            return None

    def _parse_span(self, span_data: dict[str, Any]) -> SpanNode:
        """Parse a SigNoz span into a SpanNode."""
        return SpanNode(
            span_id=span_data.get("spanID", ""),
            parent_id=span_data.get("parentSpanID"),
            service=span_data.get("serviceName", ""),
            operation=span_data.get("operationName", ""),
            duration_ms=span_data.get("duration", 0) / 1e6,  # Convert to ms
            status=span_data.get("status", "unset"),
            attributes=span_data.get("attributes", {}),
            events=tuple(span_data.get("events", [])),
        )

    def _parse_log_entry(self, log_data: dict[str, Any]) -> LogEntry | None:
        """Parse a SigNoz log into a LogEntry."""
        try:
            # SigNoz timestamps are in nanoseconds, convert to seconds
            timestamp_ns = int(log_data.get("timestamp", 0))
            return LogEntry(
                timestamp=datetime.fromtimestamp(timestamp_ns / 1e9),
                severity=self._parse_severity(log_data.get("severityText", "INFO")),
                body=log_data.get("body", ""),
                attributes=log_data.get("attributes", {}),
                trace_id=log_data.get("traceID"),
                span_id=log_data.get("spanID"),
            )
        except Exception as e:
            logger.warning(f"Failed to parse log entry: {e}")
            return None

    @staticmethod
    def _parse_stack_trace(stack_trace: str) -> tuple[StackFrame, ...]:
        """Parse a stack trace string into StackFrame objects."""
        frames = []
        lines = stack_trace.split("\n")

        for line in lines:
            if not line.strip():
                continue

            # Simple heuristic: look for file:lineno patterns
            parts = line.split(":")
            if len(parts) >= 2:
                try:
                    lineno = int(parts[-1])
                    filename = parts[-2]
                    module = ".".join(parts[:-2]) or "unknown"
                    function = "unknown"

                    frame = StackFrame(
                        module=module,
                        function=function,
                        filename=filename,
                        lineno=lineno,
                    )
                    frames.append(frame)
                except (ValueError, IndexError):
                    continue

        return tuple(frames)

    @staticmethod
    def _is_valid_identifier(name: str) -> bool:
        """Validate that a name is a safe identifier (alphanumeric, dots, hyphens).

        Used to prevent SQL injection in query construction.
        """
        if not name:
            return False
        # Allow alphanumeric, dots, hyphens, underscores
        return all(c.isalnum() or c in "._-" for c in name)

    @staticmethod
    def _is_valid_trace_id(trace_id: str) -> bool:
        """Validate that a trace ID is a safe hex string.

        OpenTelemetry trace IDs are 128-bit hex strings (32 hex chars).
        """
        if not trace_id or len(trace_id) > 32:
            return False
        return all(c in "0123456789abcdefABCDEF" for c in trace_id)

    @staticmethod
    def _parse_severity(severity_text: str) -> Severity:
        """Parse severity text into Severity enum."""
        mapping = {
            "TRACE": Severity.TRACE,
            "DEBUG": Severity.DEBUG,
            "INFO": Severity.INFO,
            "WARN": Severity.WARN,
            "WARNING": Severity.WARN,
            "ERROR": Severity.ERROR,
            "FATAL": Severity.FATAL,
        }
        return mapping.get(severity_text.upper(), Severity.INFO)
