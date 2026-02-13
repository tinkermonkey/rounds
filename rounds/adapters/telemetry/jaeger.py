"""Jaeger telemetry adapter.

Implements TelemetryPort by querying Jaeger API for errors, traces, and logs.
Normalizes Jaeger-specific data structures into core domain models.

Jaeger provides distributed tracing and can integrate with various backends
(Elasticsearch, Cassandra, Badger, etc.).
"""

import json
import logging
from datetime import datetime, timedelta, timezone
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


def _is_valid_identifier(identifier: str) -> bool:
    """Validate an identifier to prevent query injection.

    Args:
        identifier: The identifier to validate.

    Returns:
        True if identifier is safe for queries.
    """
    if not isinstance(identifier, str):
        return False
    # Allow alphanumeric, underscore, hyphen, and dot
    return bool(identifier) and all(c.isalnum() or c in "_-." for c in identifier)


class JaegerTelemetryAdapter(TelemetryPort):
    """Jaeger-backed telemetry adapter via Query API."""

    def __init__(self, api_url: str, service_name: str = ""):
        """Initialize Jaeger adapter.

        Args:
            api_url: Base URL for Jaeger Query API (e.g., http://localhost:16686)
            service_name: Optional default service name for queries.
        """
        self.api_url = api_url.rstrip("/")
        self.service_name = service_name
        self.client = httpx.AsyncClient(
            base_url=self.api_url,
            timeout=30.0,
        )

    async def __aenter__(self) -> "JaegerTelemetryAdapter":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.client.aclose()

    async def close(self) -> None:
        """Close the httpx client and clean up resources."""
        await self.client.aclose()

    async def get_recent_errors(
        self, since: datetime, services: list[str] | None = None
    ) -> list[ErrorEvent]:
        """Return error events since the given timestamp.

        Queries Jaeger for spans with error status or exceptions.

        Args:
            since: Timestamp to query from.
            services: Optional list of service names to filter by.

        Returns:
            List of ErrorEvent objects.

        Raises:
            Exception: If Jaeger API is unavailable.
        """
        if not services and self.service_name:
            services = [self.service_name]

        # Validate service names to prevent injection
        if services:
            for service in services:
                if not _is_valid_identifier(service):
                    logger.warning(f"Invalid service name format: {service}")
                    services = [s for s in services if _is_valid_identifier(s)]
                    if not services:
                        return []

        errors: list[ErrorEvent] = []

        try:
            # Calculate time range
            end_time_us = int(datetime.now(timezone.utc).timestamp() * 1e6)
            start_time_us = int(since.timestamp() * 1e6)

            # If no services specified, query each service separately
            services_to_query = services or await self._get_services()

            for service in services_to_query:
                # Build search tags for error spans
                tags = "error=true OR otel.status_code=ERROR"

                response = await self.client.get(
                    "/api/traces",
                    params={
                        "service": service,
                        "tags": tags,
                        "start": start_time_us,
                        "end": end_time_us,
                        "limit": 100,
                    },
                )

                if response.status_code == 200:
                    data = response.json()
                    traces = data.get("data", [])

                    for trace in traces:
                        error_events = self._extract_error_events(trace)
                        errors.extend(error_events)

            return errors

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch errors from Jaeger: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching errors: {e}", exc_info=True)
            raise

    async def _get_services(self) -> list[str]:
        """Get list of services available in Jaeger.

        Returns:
            List of service names.

        Raises:
            httpx.HTTPError: If Jaeger API request fails.
            Exception: If unexpected error occurs.
        """
        try:
            response = await self.client.get("/api/services")
            response.raise_for_status()
            data = response.json()
            return data.get("data", [])
        except httpx.HTTPError as e:
            logger.error(f"HTTP error fetching services from Jaeger: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching services from Jaeger: {e}", exc_info=True)
            raise

    def _extract_error_events(self, trace: dict[str, Any]) -> list[ErrorEvent]:
        """Extract ErrorEvent objects from a Jaeger trace.

        Args:
            trace: Jaeger trace object.

        Returns:
            List of ErrorEvent objects found in the trace.
        """
        error_events: list[ErrorEvent] = []
        spans = trace.get("spans", [])

        for span in spans:
            # Check if span represents an error
            if not self._is_error_span(span):
                continue

            # Extract stack frames from span logs
            stack_frames = self._extract_stack_frames(span)

            # Get error information - convert tags list to dict
            tags = {t["key"]: t["value"] for t in span.get("tags", [])} if isinstance(span.get("tags", []), list) else span.get("tags", {})
            error_type = tags.get("error.kind", "Exception")
            error_message = span.get("logs", [{}])[0].get("message", "")

            # Parse error message if it's JSON
            try:
                if error_message.startswith("{"):
                    error_data = json.loads(error_message)
                    error_message = error_data.get("message", error_message)
            except (json.JSONDecodeError, ValueError):
                pass

            event = ErrorEvent(
                trace_id=trace.get("traceID", ""),
                span_id=span.get("spanID", ""),
                service=span.get("process", {}).get("serviceName", ""),
                error_type=error_type,
                error_message=error_message,
                stack_frames=stack_frames,
                timestamp=datetime.fromtimestamp(
                    span.get("startTime", 0) / 1e6, tz=timezone.utc
                ),
                attributes=tags,
                severity=Severity.ERROR,
            )

            error_events.append(event)

        return error_events

    @staticmethod
    def _is_error_span(span: dict[str, Any]) -> bool:
        """Check if span represents an error.

        Args:
            span: Jaeger span object.

        Returns:
            True if span represents an error.
        """
        # Convert tags list to dict if needed
        tags_list = span.get("tags", [])
        if isinstance(tags_list, list):
            tags = {t["key"]: t["value"] for t in tags_list}
        else:
            tags = tags_list

        # Check for error tags
        if tags.get("error") is True:
            return True

        if tags.get("otel.status_code") == "ERROR":
            return True

        # Check for exceptions in logs
        logs = span.get("logs", [])
        for log in logs:
            if log.get("fields", []):
                for field in log["fields"]:
                    if field.get("key") == "event" and field.get("value") == "error":
                        return True

        return False

    @staticmethod
    def _extract_stack_frames(span: dict[str, Any]) -> tuple[StackFrame, ...]:
        """Extract stack frames from span logs.

        Args:
            span: Jaeger span object.

        Returns:
            Tuple of StackFrame objects.
        """
        frames: list[StackFrame] = []
        logs = span.get("logs", [])

        for log in logs:
            fields = log.get("fields", [])
            stack_trace = None

            # Find stack trace in fields
            for field in fields:
                if field.get("key") == "stack":
                    stack_trace = field.get("value")
                    break

            if not stack_trace:
                continue

            # Parse stack trace string
            lines = stack_trace.split("\n")
            for line in lines:
                line = line.strip()
                if not line or line.startswith("Traceback"):
                    continue

                # Try to parse frame
                try:
                    # Expected format: "File "path.py", line N, in function_name"
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
                except (IndexError, ValueError):
                    logger.debug(f"Skipped unparseable stack trace line: {line[:100]}")
                    continue

        return tuple(frames)

    async def get_trace(self, trace_id: str) -> TraceTree:
        """Return the full span tree for a trace.

        Args:
            trace_id: Jaeger trace ID.

        Returns:
            TraceTree object with span hierarchy.

        Raises:
            ValueError: If trace not found or invalid.
            Exception: If Jaeger API error occurs.
        """
        if not _is_valid_trace_id(trace_id):
            raise ValueError(f"Invalid trace ID format: {trace_id}")

        try:
            response = await self.client.get(f"/api/traces/{trace_id}")
            response.raise_for_status()

            data = response.json()
            traces = data.get("data", [])

            if not traces:
                raise ValueError(f"No trace found for ID {trace_id}")

            trace = traces[0]
            spans_data = trace.get("spans", [])

            if not spans_data:
                raise ValueError(f"No spans found for trace {trace_id}")

            # Build span tree
            span_dicts: dict[str, dict[str, Any]] = {}
            root_span_id = None

            # First pass: create intermediate dicts
            for span_data in spans_data:
                span_id = span_data.get("spanID", "")
                parent_id = span_data.get("parentSpanID")

                span_dicts[span_id] = {
                    "data": span_data,
                    "children": [],
                }

                if not parent_id:
                    root_span_id = span_id

            # Second pass: build parent-child relationships
            for span_id, span_dict in span_dicts.items():
                parent_id = span_dict["data"].get("parentSpanID")
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

                # Merge tags and logs into attributes
                attributes = span_data.get("tags", {}).copy()
                process = trace.get("processes", {}).get(span_data.get("processID", ""), {})
                attributes["serviceName"] = process.get("serviceName", "")

                node = SpanNode(
                    span_id=span_id,
                    parent_id=span_data.get("parentSpanID"),
                    service=process.get("serviceName", ""),
                    operation=span_data.get("operationName", ""),
                    duration_ms=span_data.get("duration", 0) / 1000,
                    status="error" if attributes.get("error") else "ok",
                    attributes=attributes,
                    events=tuple(),  # Jaeger doesn't have events like OTel
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

            def has_error(node: SpanNode) -> bool:
                """Recursively check if a span node or its children contain errors."""
                # Check current span tags
                span = span_dicts.get(node.span_id, {}).get("data", {})
                tags_list = span.get("tags", [])
                if isinstance(tags_list, list):
                    tags = {t["key"]: t["value"] for t in tags_list}
                else:
                    tags = tags_list

                if tags.get("error") or tags.get("otel.status_code") == "ERROR":
                    return True

                # Check children
                for child in node.children:
                    if has_error(child):
                        return True
                return False

            for node in span_node_map.values():
                if has_error(node):
                    error_spans.append(node)

            return TraceTree(
                trace_id=trace_id,
                root_span=root_node,
                error_spans=tuple(error_spans),
            )

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch trace from Jaeger: {e}", exc_info=True)
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

        Extracts logs from span logs in the traces. Includes a time window
        around each trace for context.

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

        # Fetch raw trace data to extract logs
        try:
            for trace_id in trace_ids:
                try:
                    logs.extend(await self._extract_logs_from_raw_trace(trace_id))
                except Exception as e:
                    logger.warning(f"Failed to extract logs from trace {trace_id}: {e}")
                    continue

            logger.debug(f"Extracted {len(logs)} log entries from {len(trace_ids)} traces")

        except Exception as e:
            logger.error(f"Failed to extract logs from traces: {e}", exc_info=True)
            raise

        return logs

    async def _extract_logs_from_raw_trace(self, trace_id: str) -> list[LogEntry]:
        """Extract LogEntry objects from raw Jaeger trace data.

        Args:
            trace_id: Jaeger trace ID to fetch and extract logs from.

        Returns:
            List of LogEntry objects found in the trace.
        """
        log_entries: list[LogEntry] = []

        try:
            response = await self.client.get(f"/api/traces/{trace_id}")
            response.raise_for_status()

            data = response.json()
            traces = data.get("data", [])

            if not traces:
                return log_entries

            trace = traces[0]
            spans_data = trace.get("spans", [])

            # Extract logs from each span's logs field
            for span_data in spans_data:
                span_id = span_data.get("spanID", "")
                span_logs = span_data.get("logs", [])

                for log in span_logs:
                    if isinstance(log, dict):
                        # Extract timestamp
                        timestamp_us = log.get("timestamp", 0)
                        if timestamp_us:
                            timestamp = datetime.fromtimestamp(
                                timestamp_us / 1e6, tz=timezone.utc
                            )
                        else:
                            timestamp = datetime.now(timezone.utc)

                        # Extract message and other fields
                        message = ""
                        log_attrs: dict[str, Any] = {}
                        for field in log.get("fields", []):
                            if isinstance(field, dict):
                                key = field.get("key", "")
                                value = field.get("value", "")
                                if key == "message":
                                    message = value
                                else:
                                    log_attrs[key] = value

                        if message:
                            log_entry = LogEntry(
                                timestamp=timestamp,
                                severity=Severity.INFO,
                                body=message,
                                attributes=log_attrs,
                                trace_id=trace_id,
                                span_id=span_id,
                            )
                            log_entries.append(log_entry)

        except Exception as e:
            logger.error(f"Failed to fetch raw trace {trace_id}: {e}", exc_info=True)
            raise

        return log_entries

    async def get_events_for_signature(
        self, fingerprint: str, limit: int = 5
    ) -> list[ErrorEvent]:
        """Retrieve recent errors matching a fingerprint.

        Queries all services for error spans and filters by fingerprint tag.

        Args:
            fingerprint: The fingerprint to search for.
            limit: Maximum number of events to return.

        Returns:
            List of ErrorEvent objects matching the fingerprint.

        Raises:
            Exception: If Jaeger API is unavailable.
        """
        try:
            # Get all services
            services = await self._get_services()
            if not services:
                logger.warning("No services available for fingerprint search")
                return []

            all_events: list[ErrorEvent] = []

            # Query each service for recent errors with the fingerprint tag
            for service in services:
                try:
                    # Build tag query for fingerprint and error status
                    tags = f"error=true AND fingerprint={fingerprint}"

                    now = datetime.now(timezone.utc)
                    end_time_us = int(now.timestamp() * 1e6)
                    # Look back 24 hours for events matching this fingerprint
                    start_time_us = int((now - timedelta(hours=24)).timestamp() * 1e6)

                    response = await self.client.get(
                        "/api/traces",
                        params={
                            "service": service,
                            "tags": tags,
                            "start": start_time_us,
                            "end": end_time_us,
                            "limit": limit,
                        },
                    )

                    if response.status_code == 200:
                        data = response.json()
                        traces = data.get("data", [])

                        for trace in traces:
                            error_events = self._extract_error_events(trace)
                            all_events.extend(error_events)

                            # Stop if we've collected enough events
                            if len(all_events) >= limit:
                                return all_events[:limit]

                except Exception as e:
                    logger.warning(
                        f"Failed to query fingerprint {fingerprint} in service {service}: {e}"
                    )
                    continue

            return all_events[:limit]

        except Exception as e:
            logger.error(f"Failed to fetch events for fingerprint {fingerprint}: {e}", exc_info=True)
            raise
