"""SigNoz telemetry adapter.

Implements TelemetryPort by querying SigNoz REST API for errors, traces, and logs.
Normalizes SigNoz-specific data structures into core domain models.

Compatible with SigNoz unified image (v0.88+) where UI and API are served on the same
port (default 3301) via nginx routing: /api/* → backend query service.

Auth header: SIGNOZ-API-KEY (not Authorization: Bearer)
Query API: POST /api/v3/query_range with query builder format, millisecond timestamps
Trace API: GET /api/v1/traces/{id} returns columnar format
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from rounds.core.models import (
    ErrorEvent,
    LogEntry,
    PartialResultsInfo,
    Severity,
    SpanNode,
    SpanSummary,
    StackFrame,
    TraceTree,
)
from rounds.core.ports import TelemetryPort

logger = logging.getLogger(__name__)


class SigNozTelemetryAdapter(TelemetryPort):
    """SigNoz-backed telemetry adapter via REST API."""

    def __init__(
        self,
        api_url: str,
        api_key: str = "",
        fingerprinter: Callable[[ErrorEvent], str] | None = None,
    ):
        """Initialize SigNoz adapter.

        Args:
            api_url: Base URL for SigNoz API (e.g., http://localhost:3301)
            api_key: Optional API key for SIGNOZ-API-KEY header authentication
            fingerprinter: Function to compute fingerprints from ErrorEvent.
                If None, imported from core.fingerprint at runtime.
        """
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self._fingerprinter = fingerprinter
        self.client = httpx.AsyncClient(
            base_url=self.api_url,
            headers=self._get_headers(),
            timeout=30.0,
        )

    def _get_fingerprinter(self) -> Callable[[ErrorEvent], str]:
        """Lazy-load fingerprinter to avoid circular imports."""
        if self._fingerprinter:
            return self._fingerprinter
        from rounds.core.fingerprint import Fingerprinter
        return Fingerprinter().fingerprint

    def _get_headers(self) -> dict[str, str]:
        """Build request headers.

        SigNoz uses SIGNOZ-API-KEY header, not Authorization: Bearer.
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["SIGNOZ-API-KEY"] = self.api_key
        return headers

    async def __aenter__(self) -> "SigNozTelemetryAdapter":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.client.aclose()

    async def close(self) -> None:
        """Close the httpx client.

        Must be called when done using the adapter if not using it as a context manager.
        """
        await self.client.aclose()

    async def verify_connection(self) -> None:
        """Verify connectivity and authentication with the SigNoz API.

        Makes a lightweight GET request to the SigNoz health endpoint.
        Any 2xx or 4xx response (other than 401/403) confirms the server is reachable.
        Only ConnectError, TimeoutException, or auth failures (401/403) are propagated.

        Raises:
            httpx.ConnectError: If SigNoz cannot be reached at the configured URL.
            httpx.HTTPStatusError: If authentication fails (401 or 403).
            httpx.TimeoutException: If the request times out.
        """
        try:
            response = await self.client.get("/api/v1/health", timeout=10.0)
            if response.status_code in (401, 403):
                raise httpx.HTTPStatusError(
                    f"SigNoz authentication failed (HTTP {response.status_code}): "
                    "check SIGNOZ_API_KEY",
                    request=response.request,
                    response=response,
                )
            logger.info(f"SigNoz connection verified: {self.api_url}")
        except httpx.ConnectError as e:
            logger.error(f"Cannot connect to SigNoz at {self.api_url}: {e}", exc_info=True)
            raise
        except httpx.HTTPStatusError as e:
            logger.error(
                f"SigNoz authentication failed (HTTP {e.response.status_code}) "
                f"at {self.api_url}",
                exc_info=True,
            )
            raise
        except httpx.TimeoutException as e:
            logger.error(f"SigNoz connection timed out at {self.api_url}: {e}", exc_info=True)
            raise

    async def get_recent_errors(
        self, since: datetime, services: list[str] | None = None
    ) -> list[ErrorEvent]:
        """Return error events since the given timestamp.

        Queries SigNoz v3 query_range API with query builder format.
        Filters spans where hasError=true.

        Args:
            since: Query errors with timestamp >= this value
            services: Optional list of service names to filter by. Invalid names
                     are skipped with a warning.

        Returns:
            List of ErrorEvent objects from the telemetry backend.

        Raises:
            httpx.HTTPError: If the API request fails
        """
        try:
            now = datetime.now(UTC)
            start_ms = int(since.timestamp() * 1000)
            end_ms = int(now.timestamp() * 1000)

            # Build filter items: hasError=true plus optional service filters
            filter_items: list[dict[str, Any]] = [
                {
                    "key": {
                        "key": "hasError",
                        "dataType": "bool",
                        "type": "tag",
                        "isColumn": True,
                    },
                    "op": "=",
                    "value": True,
                }
            ]

            if services:
                valid_services = [s for s in services if self._is_valid_identifier(s)]
                invalid = [s for s in services if not self._is_valid_identifier(s)]
                if invalid:
                    logger.warning(f"Skipping invalid service names: {invalid}")
                if valid_services:
                    filter_items.append(
                        {
                            "key": {
                                "key": "serviceName",
                                "dataType": "string",
                                "type": "tag",
                                "isColumn": True,
                            },
                            "op": "in",
                            "value": valid_services,
                        }
                    )

            payload = {
                "start": start_ms,
                "end": end_ms,
                "step": 60,
                "variables": {},
                "compositeQuery": {
                    "queryType": "builder",
                    "panelType": "list",
                    "builderQueries": {
                        "A": {
                            "dataSource": "traces",
                            "queryName": "A",
                            "expression": "A",
                            "aggregateOperator": "noop",
                            "filters": {"op": "AND", "items": filter_items},
                            "limit": 1000,
                            "offset": 0,
                            "pageSize": 1000,
                            "orderBy": [
                                {"columnName": "timestamp", "order": "desc"}
                            ],
                            "selectColumns": [
                                {
                                    "key": "serviceName",
                                    "dataType": "string",
                                    "type": "tag",
                                    "isColumn": True,
                                },
                                {
                                    "key": "name",
                                    "dataType": "string",
                                    "type": "tag",
                                    "isColumn": True,
                                },
                                {
                                    "key": "spanID",
                                    "dataType": "string",
                                    "type": "tag",
                                    "isColumn": True,
                                },
                                {
                                    "key": "traceID",
                                    "dataType": "string",
                                    "type": "tag",
                                    "isColumn": True,
                                },
                                {
                                    "key": "statusMessage",
                                    "dataType": "string",
                                    "type": "tag",
                                    "isColumn": True,
                                },
                                {
                                    "key": "hasError",
                                    "dataType": "bool",
                                    "type": "tag",
                                    "isColumn": True,
                                },
                            ],
                        }
                    },
                },
            }

            response = await self.client.post("/api/v3/query_range", json=payload)
            response.raise_for_status()

            data = response.json()
            errors = []

            for result in data.get("data", {}).get("result", []):
                for item in result.get("list", []):
                    error = self._parse_error_event(item)
                    if error:
                        errors.append(error)

            return errors

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch errors from SigNoz: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching errors: {e}", exc_info=True)
            raise

    async def get_trace(self, trace_id: str) -> TraceTree:
        """Return the full span tree for a trace.

        Uses GET /api/v1/traces/{id} which returns spans in columnar format.
        """
        try:
            response = await self.client.get(f"/api/v1/traces/{trace_id}")
            response.raise_for_status()

            data = response.json()

            # v1/traces returns a list with one element containing columns+events
            spans_data = self._parse_trace_response(data)

            if not spans_data:
                raise ValueError(f"No spans found for trace {trace_id}")

            # Build a mutable intermediate structure to handle parent-child relationships
            span_dicts: dict[str, dict[str, Any]] = {}
            root_span_id: str | None = None

            for span_data in spans_data:
                span_id = span_data.get("spanID", "")
                if not span_id:
                    continue
                span_dicts[span_id] = {
                    "data": span_data,
                    "children": [],
                }
                if not span_data.get("parentSpanID"):
                    root_span_id = span_id

            # Build parent-child relationships
            for span_id, span_dict in span_dicts.items():
                parent_id = span_dict["data"].get("parentSpanID")
                if parent_id and parent_id in span_dicts:
                    span_dicts[parent_id]["children"].append(span_id)

            # Recursively construct SpanNode tree
            span_node_map: dict[str, SpanNode] = {}

            def build_span_node(sid: str) -> SpanNode:
                if sid in span_node_map:
                    return span_node_map[sid]

                sd = span_dicts[sid]
                span_data = sd["data"]
                children = tuple(build_span_node(cid) for cid in sd["children"])

                node = SpanNode(
                    span_id=span_data.get("spanID", ""),
                    parent_id=span_data.get("parentSpanID"),
                    service=span_data.get("serviceName", ""),
                    operation=span_data.get("name", ""),
                    duration_ms=int(span_data.get("durationNano", 0) or 0) / 1e6,
                    status="error" if span_data.get("hasError") else "ok",
                    attributes=span_data.get("attributes", {}),
                    events=tuple(span_data.get("events", [])),
                    children=children,
                )
                span_node_map[sid] = node
                return node

            if not root_span_id:
                root_span_id = next(iter(span_dicts.keys())) if span_dicts else None

            if not root_span_id:
                raise ValueError(f"Cannot determine root span for trace {trace_id}")

            root_span = build_span_node(root_span_id)

            error_spans: list[SpanNode] = []

            def collect_error_spans(node: SpanNode) -> None:
                if node.status == "error":
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
            logger.error(f"Failed to fetch trace {trace_id} from SigNoz: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching trace: {e}", exc_info=True)
            raise

    async def get_traces(
        self, trace_ids: list[str]
    ) -> tuple[list[TraceTree], PartialResultsInfo]:
        """Batch trace retrieval.

        Attempts to fetch all traces. Returns partial results if any fail.
        """
        traces = []
        failed_trace_ids = []

        for trace_id in trace_ids:
            try:
                trace = await self.get_trace(trace_id)
                traces.append(trace)
            except Exception as e:
                logger.warning(f"Failed to fetch trace {trace_id}: {e}")
                failed_trace_ids.append(trace_id)

        is_partial = len(failed_trace_ids) > 0
        if is_partial:
            logger.warning(
                f"Batch trace retrieval incomplete: "
                f"retrieved {len(traces)}/{len(trace_ids)} traces. "
                f"Failed trace IDs: {failed_trace_ids}"
            )

        partial_info = PartialResultsInfo(
            total_requested=len(trace_ids),
            total_returned=len(traces),
            is_partial=is_partial,
            reason=(
                f"Failed to retrieve {len(failed_trace_ids)} traces"
                if is_partial
                else None
            ),
        )

        return traces, partial_info

    async def get_correlated_logs(
        self, trace_ids: list[str], window_minutes: int = 5
    ) -> list[LogEntry]:
        """Return logs correlated with the given traces.

        Uses POST /api/v3/query_range with dataSource=logs and traceID filter.
        """
        try:
            if not trace_ids:
                return []

            valid_trace_ids = [
                tid for tid in trace_ids if self._is_valid_trace_id(tid)
            ]
            if not valid_trace_ids:
                logger.warning("No valid trace IDs provided")
                return []

            now = datetime.now(UTC)
            since = now - timedelta(minutes=window_minutes)
            start_ms = int(since.timestamp() * 1000)
            end_ms = int(now.timestamp() * 1000)

            # Build OR filter for each trace ID.
            # trace_id is a core log column (type=""), not a tag attribute.
            trace_filter_items = [
                {
                    "key": {
                        "key": "trace_id",
                        "dataType": "string",
                        "type": "",
                        "isColumn": True,
                    },
                    "op": "=",
                    "value": tid,
                }
                for tid in valid_trace_ids
            ]

            payload = {
                "start": start_ms,
                "end": end_ms,
                "step": 60,
                "variables": {},
                "compositeQuery": {
                    "queryType": "builder",
                    "panelType": "list",
                    "builderQueries": {
                        "A": {
                            "dataSource": "logs",
                            "queryName": "A",
                            "expression": "A",
                            "aggregateOperator": "noop",
                            "filters": {
                                "op": "OR",
                                "items": trace_filter_items,
                            },
                            "limit": 500,
                            "offset": 0,
                            "pageSize": 500,
                            "orderBy": [
                                {"columnName": "timestamp", "order": "desc"}
                            ],
                        }
                    },
                },
            }

            response = await self.client.post("/api/v3/query_range", json=payload)
            response.raise_for_status()

            data = response.json()
            logs = []

            for result in data.get("data", {}).get("result", []):
                for item in result.get("list", []):
                    log = self._parse_log_entry(item)
                    if log:
                        logs.append(log)

            return logs

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch correlated logs: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching logs: {e}", exc_info=True)
            raise

    async def search_logs(
        self,
        query: str,
        since: datetime,
        until: datetime | None = None,
        services: list[str] | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        """Search logs by keyword and optional metadata filters.

        Queries SigNoz v3 query_range API with dataSource=logs.
        Uses a body contains filter for keyword search.

        Args:
            query: Keyword to search in log bodies. Empty string matches all.
            since: Return logs after this timestamp.
            until: Return logs before this timestamp. Defaults to now.
            services: Optional service name filter (matched against service.name resource attr).
            limit: Maximum results to return.
        """
        try:
            now = datetime.now(UTC)
            start_ms = int(since.timestamp() * 1000)
            end_ms = int((until or now).timestamp() * 1000)

            filter_items: list[dict[str, Any]] = []

            if query:
                filter_items.append({
                    "key": {
                        "key": "body",
                        "dataType": "string",
                        "type": "",
                        "isColumn": True,
                    },
                    "op": "contains",
                    "value": query,
                })

            if services:
                valid_services = [s for s in services if self._is_valid_identifier(s)]
                invalid = [s for s in services if not self._is_valid_identifier(s)]
                if invalid:
                    logger.warning(f"Skipping invalid service names: {invalid}")
                if valid_services:
                    filter_items.append({
                        "key": {
                            "key": "service.name",
                            "dataType": "string",
                            "type": "resource",
                            "isColumn": False,
                        },
                        "op": "in",
                        "value": valid_services,
                    })

            payload = {
                "start": start_ms,
                "end": end_ms,
                "step": 60,
                "variables": {},
                "compositeQuery": {
                    "queryType": "builder",
                    "panelType": "list",
                    "builderQueries": {
                        "A": {
                            "dataSource": "logs",
                            "queryName": "A",
                            "expression": "A",
                            "aggregateOperator": "noop",
                            "filters": {"op": "AND", "items": filter_items},
                            "limit": limit,
                            "offset": 0,
                            "pageSize": limit,
                            "orderBy": [{"columnName": "timestamp", "order": "desc"}],
                        }
                    },
                },
            }

            response = await self.client.post("/api/v3/query_range", json=payload)
            response.raise_for_status()

            data = response.json()
            logs = []
            for result in data.get("data", {}).get("result", []):
                for item in result.get("list", []):
                    log = self._parse_log_entry(item)
                    if log:
                        logs.append(log)

            return logs

        except httpx.HTTPError as e:
            logger.error(f"Failed to search logs in SigNoz: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error searching logs: {e}", exc_info=True)
            raise

    async def search_spans(
        self,
        since: datetime,
        until: datetime | None = None,
        services: list[str] | None = None,
        operation: str | None = None,
        attributes: dict[str, str] | None = None,
        has_error: bool | None = None,
        limit: int = 100,
    ) -> list[SpanSummary]:
        """Search spans by metadata filters.

        Queries SigNoz v3 query_range API with dataSource=traces.
        All filters are combined with AND logic.

        Args:
            since: Return spans after this timestamp.
            until: Return spans before this timestamp. Defaults to now.
            services: Optional service name filter.
            operation: Optional operation name substring filter.
            attributes: Optional key-value span attribute filters (exact match per key).
            has_error: If set, filter to error or non-error spans only.
            limit: Maximum results to return.
        """
        try:
            now = datetime.now(UTC)
            start_ms = int(since.timestamp() * 1000)
            end_ms = int((until or now).timestamp() * 1000)

            filter_items: list[dict[str, Any]] = []

            if has_error is not None:
                filter_items.append({
                    "key": {
                        "key": "hasError",
                        "dataType": "bool",
                        "type": "tag",
                        "isColumn": True,
                    },
                    "op": "=",
                    "value": has_error,
                })

            if services:
                valid_services = [s for s in services if self._is_valid_identifier(s)]
                invalid = [s for s in services if not self._is_valid_identifier(s)]
                if invalid:
                    logger.warning(f"Skipping invalid service names: {invalid}")
                if valid_services:
                    filter_items.append({
                        "key": {
                            "key": "serviceName",
                            "dataType": "string",
                            "type": "tag",
                            "isColumn": True,
                        },
                        "op": "in",
                        "value": valid_services,
                    })

            if operation:
                filter_items.append({
                    "key": {
                        "key": "name",
                        "dataType": "string",
                        "type": "tag",
                        "isColumn": True,
                    },
                    "op": "contains",
                    "value": operation,
                })

            if attributes:
                for key, value in attributes.items():
                    filter_items.append({
                        "key": {
                            "key": key,
                            "dataType": "string",
                            "type": "tag",
                            "isColumn": False,
                        },
                        "op": "=",
                        "value": value,
                    })

            payload = {
                "start": start_ms,
                "end": end_ms,
                "step": 60,
                "variables": {},
                "compositeQuery": {
                    "queryType": "builder",
                    "panelType": "list",
                    "builderQueries": {
                        "A": {
                            "dataSource": "traces",
                            "queryName": "A",
                            "expression": "A",
                            "aggregateOperator": "noop",
                            "filters": {"op": "AND", "items": filter_items},
                            "limit": limit,
                            "offset": 0,
                            "pageSize": limit,
                            "orderBy": [{"columnName": "timestamp", "order": "desc"}],
                            "selectColumns": [
                                {"key": "serviceName", "dataType": "string", "type": "tag", "isColumn": True},
                                {"key": "name", "dataType": "string", "type": "tag", "isColumn": True},
                                {"key": "spanID", "dataType": "string", "type": "tag", "isColumn": True},
                                {"key": "traceID", "dataType": "string", "type": "tag", "isColumn": True},
                                {"key": "durationNano", "dataType": "float64", "type": "tag", "isColumn": True},
                                {"key": "hasError", "dataType": "bool", "type": "tag", "isColumn": True},
                                {"key": "statusMessage", "dataType": "string", "type": "tag", "isColumn": True},
                            ],
                        }
                    },
                },
            }

            response = await self.client.post("/api/v3/query_range", json=payload)
            response.raise_for_status()

            data = response.json()
            spans = []
            for result in data.get("data", {}).get("result", []):
                for item in result.get("list", []):
                    span = self._parse_span_summary(item)
                    if span:
                        spans.append(span)

            return spans

        except httpx.HTTPError as e:
            logger.error(f"Failed to search spans in SigNoz: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error searching spans: {e}", exc_info=True)
            raise

    async def list_services(self) -> list[str]:
        """Return all service names visible in SigNoz.

        Queries GET /api/v1/services which returns all services that have
        sent telemetry. Service names are returned exactly as SigNoz reports
        them (case-sensitive).

        Returns:
            Sorted list of service name strings.

        Raises:
            httpx.HTTPError: If the API request fails.
        """
        try:
            response = await self.client.get("/api/v1/services")
            response.raise_for_status()

            data = response.json()

            # SigNoz returns: {"data": [{"serviceName": "...", ...}, ...]}
            services_data = data.get("data", [])
            if not isinstance(services_data, list):
                logger.warning(f"Unexpected /api/v1/services response shape: {type(services_data)}")
                return []

            names = [
                item["serviceName"]
                for item in services_data
                if isinstance(item, dict) and item.get("serviceName")
            ]
            return sorted(set(names))

        except httpx.HTTPError as e:
            logger.error(f"Failed to list services from SigNoz: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error listing services: {e}", exc_info=True)
            raise

    async def get_events_for_signature(
        self, fingerprint: str, limit: int = 5
    ) -> list[ErrorEvent]:
        """Return recent events matching a known fingerprint.

        Filters errors by computing fingerprints locally and matching against
        the requested fingerprint.

        Args:
            fingerprint: Signature fingerprint to match against.
            limit: Maximum number of matching events to return.

        Returns:
            List of ErrorEvent objects with matching fingerprints.
        """
        since = datetime.now(UTC) - timedelta(hours=24)
        all_errors = await self.get_recent_errors(since)

        matching_errors = []
        fingerprinter = self._get_fingerprinter()

        for error in all_errors:
            if fingerprinter(error) == fingerprint:
                matching_errors.append(error)
                if len(matching_errors) >= limit:
                    break

        return matching_errors

    def _parse_error_event(self, item: dict[str, Any]) -> ErrorEvent | None:
        """Parse a v3 query_range list item into an ErrorEvent.

        Item format: {"timestamp": "ISO8601", "data": {span fields}}
        """
        span_id = ""
        try:
            span_data = item.get("data", {})
            span_id = span_data.get("spanID", "")
            trace_id = span_data.get("traceID", "")
            service = span_data.get("serviceName", "")
            operation = span_data.get("name", "")
            status_message = span_data.get("statusMessage", "")

            # ISO8601 timestamp from the list item envelope
            ts_str = item.get("timestamp", "")
            if ts_str:
                timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                timestamp = datetime.now(UTC)

            # Use operation name as error_type when no exception.type attribute
            attributes: dict[str, Any] = span_data.get("attributes", {}) or {}
            error_type = attributes.get("exception.type") or operation or "UnknownError"
            error_message = (
                attributes.get("exception.message")
                or status_message
                or ""
            )

            # Parse stack frames from exception.stacktrace if present
            stack_frames: tuple[StackFrame, ...] = ()
            stack_trace = attributes.get("exception.stacktrace", "")
            if stack_trace:
                stack_frames = self._parse_stack_trace(stack_trace)

            severity_text = span_data.get("severityText", "ERROR") or "ERROR"

            return ErrorEvent(
                trace_id=trace_id,
                span_id=span_id,
                service=service,
                error_type=error_type,
                error_message=error_message,
                stack_frames=stack_frames,
                timestamp=timestamp,
                attributes=attributes,
                severity=self._parse_severity(severity_text),
            )

        except Exception as e:
            logger.warning(
                f"Failed to parse error event from span {span_id}: {e}",
                exc_info=True,
            )
            raise ValueError(
                f"Cannot parse error event from span {span_id}: {e}"
            ) from e

    def _parse_span_summary(self, item: dict[str, Any]) -> SpanSummary | None:
        """Parse a v3 query_range list item into a SpanSummary.

        Item format: {"timestamp": "ISO8601", "data": {span fields}}
        """
        try:
            span_data = item.get("data", {})
            span_id = span_data.get("spanID", "")
            trace_id = span_data.get("traceID", "")
            service = span_data.get("serviceName", "")
            operation = span_data.get("name", "")
            duration_nano = span_data.get("durationNano", 0) or 0
            has_error = bool(span_data.get("hasError", False))

            ts_str = item.get("timestamp", "")
            if ts_str:
                timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                timestamp = datetime.now(UTC)

            attributes: dict[str, Any] = span_data.get("attributes", {}) or {}

            return SpanSummary(
                trace_id=trace_id,
                span_id=span_id,
                service=service,
                operation=operation,
                duration_ms=int(duration_nano) / 1e6,
                has_error=has_error,
                timestamp=timestamp,
                attributes=attributes,
            )
        except Exception as e:
            logger.warning(f"Failed to parse span summary: {e}", exc_info=True)
            return None

    def _parse_log_entry(self, item: dict[str, Any]) -> LogEntry | None:
        """Parse a v3 query_range list item into a LogEntry.

        Item format: {"timestamp": "ISO8601", "data": {log fields}}
        Log data contains: body, severity_text, trace_id, span_id,
        attributes_string, attributes_number, attributes_bool, resources_string.
        """
        try:
            log_data = item.get("data", {})

            ts_str = item.get("timestamp", "")
            if ts_str:
                timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                timestamp = datetime.now(UTC)

            # Merge attribute dicts into a single flat attributes dict
            attributes: dict[str, Any] = {}
            for attr_key in (
                "attributes_string",
                "attributes_number",
                "attributes_bool",
                "resources_string",
            ):
                val = log_data.get(attr_key)
                if isinstance(val, dict):
                    attributes.update(val)

            return LogEntry(
                timestamp=timestamp,
                severity=self._parse_severity(
                    log_data.get("severity_text", "INFO") or "INFO"
                ),
                body=log_data.get("body", ""),
                attributes=attributes,
                trace_id=log_data.get("trace_id") or log_data.get("traceID"),
                span_id=log_data.get("span_id") or log_data.get("spanID"),
            )
        except Exception as e:
            logger.warning(f"Failed to parse log entry: {e}", exc_info=True)
            raise ValueError(f"Cannot parse log entry: {e}") from e

    @staticmethod
    def _parse_trace_response(data: Any) -> list[dict[str, Any]]:
        """Parse the columnar trace response from GET /api/v1/traces/{id}.

        Response format:
        [{"columns": ["__time", "SpanId", "TraceId", "ServiceName", "Name", "Kind",
                       "DurationNano", "TagsKeys", "TagsValues", "References",
                       "Events", "HasError", "StatusMessage", "StatusCodeString",
                       "SpanKind"],
          "events": [[col0_val, col1_val, ...], ...]}]

        Returns list of span dicts with normalized field names.
        """
        if not isinstance(data, list) or not data:
            return []

        result_block = data[0]
        columns: list[str] = result_block.get("columns", [])
        events: list[list[Any]] = result_block.get("events", [])

        if not columns or not events:
            return []

        # Build a column-name → index map (case-insensitive lookup)
        col_index = {col.lower(): i for i, col in enumerate(columns)}

        def get_col(row: list[Any], name: str, default: Any = None) -> Any:
            idx = col_index.get(name.lower())
            if idx is None or idx >= len(row):
                return default
            return row[idx]

        spans = []
        for row in events:
            # TagsKeys and TagsValues are parallel arrays → merge into dict
            tags_keys = get_col(row, "TagsKeys") or []
            tags_values = get_col(row, "TagsValues") or []
            attributes: dict[str, Any] = {}
            if isinstance(tags_keys, list) and isinstance(tags_values, list):
                for k, v in zip(tags_keys, tags_values):
                    attributes[k] = v

            # References encodes parent span ID
            references = get_col(row, "References") or []
            parent_span_id: str | None = None
            if isinstance(references, list):
                for ref in references:
                    if isinstance(ref, dict) and ref.get("traceState") == "child_of":
                        parent_span_id = ref.get("spanID")
                        break

            span = {
                "spanID": get_col(row, "SpanId", ""),
                "traceID": get_col(row, "TraceId", ""),
                "serviceName": get_col(row, "ServiceName", ""),
                "name": get_col(row, "Name", ""),
                "durationNano": get_col(row, "DurationNano", 0),
                "hasError": bool(get_col(row, "HasError", False)),
                "statusMessage": get_col(row, "StatusMessage", ""),
                "parentSpanID": parent_span_id,
                "attributes": attributes,
                "events": get_col(row, "Events") or [],
            }
            spans.append(span)

        return spans

    @staticmethod
    def _parse_stack_trace(stack_trace: str) -> tuple[StackFrame, ...]:
        """Parse a stack trace string into StackFrame objects."""
        frames = []
        for line in stack_trace.split("\n"):
            if not line.strip():
                continue
            parts = line.split(":")
            if len(parts) >= 2:
                try:
                    lineno = int(parts[-1])
                    filename = parts[-2]
                    module = ".".join(parts[:-2]) or "unknown"
                    frames.append(
                        StackFrame(
                            module=module,
                            function="unknown",
                            filename=filename,
                            lineno=lineno,
                        )
                    )
                except (ValueError, IndexError):
                    logger.debug(f"Skipped unparseable stack trace line: {line[:100]}")
        return tuple(frames)

    @staticmethod
    def _is_valid_identifier(name: str) -> bool:
        """Validate that a name is a safe identifier (alphanumeric, dots, hyphens)."""
        if not name:
            return False
        return all(c.isalnum() or c in "._-" for c in name)

    @staticmethod
    def _is_valid_trace_id(trace_id: str) -> bool:
        """Validate that a trace ID is a safe hex string (32 hex chars)."""
        if not trace_id or len(trace_id) != 32:
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
