"""Elasticsearch telemetry adapter.

Implements TelemetryPort by querying Elasticsearch indices populated by
otel-collector's elasticsearchexporter. Reads from otel-traces-* and
otel-logs-* index patterns and normalizes documents into core domain models.

Compatible with otel-collector elasticsearchexporter **otel mapping mode**
(``mapping::mode: otel``). Index patterns are configurable for custom data
stream setups.

Auth: API key (Authorization: ApiKey <key>) or HTTP Basic.
Query API: POST /<index>/_search using Elasticsearch Query DSL.
Trace lookup: queries all spans by traceId, reconstructs parent-child tree.
"""

import base64
import logging
from collections.abc import Callable, Sequence
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

# OTEL status code value written by otel-collector elasticsearchexporter otel mode
_ES_ERROR_STATUS = "STATUS_CODE_ERROR"


def _is_valid_trace_id(trace_id: str) -> bool:
    """Validate trace ID format (hex string, 1-64 chars)."""
    if not isinstance(trace_id, str) or not trace_id:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in trace_id)


def _is_valid_identifier(identifier: str) -> bool:
    """Validate an identifier to prevent ES query injection."""
    if not isinstance(identifier, str):
        return False
    return bool(identifier) and all(c.isalnum() or c in "_-." for c in identifier)


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


def _parse_stack_trace(stack_trace: str) -> tuple[StackFrame, ...]:
    """Attempt to parse a stack trace string into StackFrame objects.

    Expects a colon-delimited format of ``module:filename:lineno``. Lines
    that do not match this pattern (including standard Python ``traceback``
    output and Java ``printStackTrace`` output) are silently skipped, so
    this function will return an empty tuple for most real OTEL payloads.
    Stack trace text is still preserved in the parent ErrorEvent's
    ``attributes["exception.stacktrace"]`` field for diagnosis.
    """
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


def _build_trace_tree(trace_id: str, hits: list[dict[str, Any]]) -> TraceTree:
    """Reconstruct a TraceTree from a flat list of Elasticsearch span hits.

    Builds parent-child relationships from parentSpanId links. The root span
    is selected as the last span in dict-iteration order whose parentSpanId is
    absent from the result set. When multiple spans lack a parent (e.g. because
    the true root was not exported), the selected root depends on the order
    Elasticsearch returned documents and may not be the actual trace root.

    Cycle detection: if a parentSpanId chain forms a loop, the cycle is broken
    at the re-entry point and a warning is logged. The affected node is returned
    with no children rather than causing unbounded recursion.

    Args:
        trace_id: The trace ID these spans belong to.
        hits: Raw Elasticsearch hit dicts, each containing a ``_source`` field.

    Returns:
        TraceTree with full span hierarchy and collected error spans.

    Raises:
        ValueError: If no valid spans are found in the hits.
    """
    span_dicts: dict[str, dict[str, Any]] = {}
    for hit in hits:
        source = hit.get("_source", {})
        span_id = source.get("spanId", "")
        if span_id:
            span_dicts[span_id] = {"source": source, "children": []}

    if not span_dicts:
        raise ValueError(f"No valid spans found for trace {trace_id}")

    # Build parent → children index; last parentless span becomes root candidate
    root_span_id: str | None = None
    for span_id, span_dict in span_dicts.items():
        parent_id = span_dict["source"].get("parentSpanId")
        if parent_id and parent_id in span_dicts:
            span_dicts[parent_id]["children"].append(span_id)
        else:
            root_span_id = span_id

    if root_span_id is None:
        root_span_id = next(iter(span_dicts))

    span_node_cache: dict[str, SpanNode] = {}
    in_progress: set[str] = set()  # cycle detection

    def build_node(sid: str) -> SpanNode:
        if sid in span_node_cache:
            return span_node_cache[sid]
        if sid in in_progress:
            logger.warning(
                "Cyclic parentSpanId detected in trace %s at span %s; "
                "breaking cycle — node returned with no children",
                trace_id,
                sid,
            )
            sd = span_dicts[sid]
            source = sd["source"]
            status_code = (source.get("status") or {}).get("code", "")
            node = SpanNode(
                span_id=sid,
                parent_id=source.get("parentSpanId"),
                service=(
                    source.get("resource", {})
                    .get("attributes", {})
                    .get("service.name", "")
                ),
                operation=source.get("name", ""),
                duration_ms=int(source.get("durationNano", 0) or 0) / 1e6,
                status="error" if status_code == _ES_ERROR_STATUS else "ok",
                attributes=dict(source.get("attributes", {}) or {}),
                events=tuple(source.get("events", []) or []),
                children=(),
            )
            span_node_cache[sid] = node
            return node

        in_progress.add(sid)
        sd = span_dicts[sid]
        source = sd["source"]
        children = tuple(build_node(cid) for cid in sd["children"])
        status_code = (source.get("status") or {}).get("code", "")
        duration_ns = source.get("durationNano", 0) or 0
        node = SpanNode(
            span_id=sid,
            parent_id=source.get("parentSpanId"),
            service=(
                source.get("resource", {})
                .get("attributes", {})
                .get("service.name", "")
            ),
            operation=source.get("name", ""),
            duration_ms=int(duration_ns) / 1e6,
            status="error" if status_code == _ES_ERROR_STATUS else "ok",
            attributes=dict(source.get("attributes", {}) or {}),
            events=tuple(source.get("events", []) or []),
            children=children,
        )
        in_progress.discard(sid)
        span_node_cache[sid] = node
        return node

    root_span = build_node(root_span_id)

    if len(span_node_cache) < len(span_dicts):
        logger.warning(
            "Trace %s: built tree from %d of %d spans; %d span(s) dropped due to cycles",
            trace_id,
            len(span_node_cache),
            len(span_dicts),
            len(span_dicts) - len(span_node_cache),
        )

    error_spans: list[SpanNode] = []

    def collect_errors(node: SpanNode) -> None:
        if node.status == "error":
            error_spans.append(node)
        for child in node.children:
            collect_errors(child)

    collect_errors(root_span)

    return TraceTree(
        trace_id=trace_id,
        root_span=root_span,
        error_spans=tuple(error_spans),
    )


class ElasticsearchTelemetryAdapter(TelemetryPort):
    """Elasticsearch-backed telemetry adapter via REST API.

    Queries otel-collector-populated indices for error events, traces, and
    correlated logs. Supports API key auth and HTTP Basic auth.

    Index naming follows otel-collector elasticsearchexporter otel-mode defaults:
    - Traces: otel-traces-* (configurable via traces_index)
    - Logs:   otel-logs-*   (configurable via logs_index)
    """

    def __init__(
        self,
        es_url: str,
        api_key: str = "",
        username: str = "",
        password: str = "",
        traces_index: str = "otel-traces-*",
        logs_index: str = "otel-logs-*",
        fingerprinter: Callable[[ErrorEvent], str] | None = None,
    ):
        """Initialize Elasticsearch adapter.

        Args:
            es_url: Elasticsearch base URL (e.g., http://localhost:9200).
            api_key: ES API key. Takes precedence over username/password when set.
            username: HTTP Basic auth username.
            password: HTTP Basic auth password.
            traces_index: Index pattern for OTEL traces.
            logs_index: Index pattern for OTEL logs.
            fingerprinter: Function to compute fingerprints from ErrorEvent.
                If None, imported from core.fingerprint at runtime.
        """
        self.es_url = es_url.rstrip("/")
        self.traces_index = traces_index
        self.logs_index = logs_index
        self._fingerprinter = fingerprinter
        self._closed = False
        self.client = httpx.AsyncClient(
            base_url=self.es_url,
            headers=self._build_headers(api_key, username, password),
            timeout=30.0,
        )

    @staticmethod
    def _build_headers(api_key: str, username: str, password: str) -> dict[str, str]:
        """Build authentication headers.

        API key takes precedence over basic auth when both are provided.
        """
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"ApiKey {api_key}"
        elif username and password:
            credentials = base64.b64encode(
                f"{username}:{password}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {credentials}"
        return headers

    def _get_fingerprinter(self) -> Callable[[ErrorEvent], str]:
        """Lazy-load fingerprinter to avoid circular imports."""
        if self._fingerprinter:
            return self._fingerprinter
        from rounds.core.fingerprint import Fingerprinter

        return Fingerprinter().fingerprint

    async def __aenter__(self) -> "ElasticsearchTelemetryAdapter":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the httpx client. Safe to call multiple times."""
        if not self._closed:
            self._closed = True
            await self.client.aclose()

    async def get_recent_errors(
        self, since: datetime, services: list[str] | None = None
    ) -> list[ErrorEvent]:
        """Return error events since the given timestamp.

        Queries otel-traces-* for spans where status.code=STATUS_CODE_ERROR
        or an exception.type attribute is present.

        Args:
            since: Return errors after this timestamp.
            services: Optional list of service names to filter by.
                Invalid names (containing special characters) are skipped.

        Returns:
            List of ErrorEvent objects in descending timestamp order.
            Spans that cannot be parsed are skipped with a warning.

        Raises:
            httpx.HTTPError: If the Elasticsearch request fails.
        """
        try:
            must_filters: list[dict[str, Any]] = [
                {
                    "range": {
                        "@timestamp": {
                            "gte": since.isoformat(),
                            "lte": datetime.now(UTC).isoformat(),
                        }
                    }
                },
                {
                    "bool": {
                        "should": [
                            {"term": {"status.code": _ES_ERROR_STATUS}},
                            {"exists": {"field": "attributes.exception.type"}},
                        ],
                        "minimum_should_match": 1,
                    }
                },
            ]

            if services:
                valid_services = [s for s in services if _is_valid_identifier(s)]
                invalid = [s for s in services if not _is_valid_identifier(s)]
                if invalid:
                    logger.warning(f"Skipping invalid service names: {invalid}")
                if valid_services:
                    must_filters.append(
                        {"terms": {"resource.attributes.service.name": valid_services}}
                    )

            query: dict[str, Any] = {
                "query": {"bool": {"filter": must_filters}},
                "size": 1000,
                "sort": [{"@timestamp": "desc"}, {"_id": "asc"}],
            }

            response = await self.client.post(
                f"/{self.traces_index}/_search", json=query
            )
            response.raise_for_status()

            hits = response.json().get("hits", {}).get("hits", [])
            errors = []
            parse_errors = 0
            for hit in hits:
                try:
                    errors.append(self._parse_error_event(hit))
                except Exception:
                    parse_errors += 1
            if parse_errors:
                logger.error(
                    "Failed to parse %d/%d error event(s) from Elasticsearch; "
                    "check otel-collector document schema",
                    parse_errors,
                    len(hits),
                )
            return errors

        except httpx.HTTPError as e:
            logger.error(
                f"Failed to fetch errors from Elasticsearch: {e}", exc_info=True
            )
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching errors: {e}", exc_info=True)
            raise

    async def get_trace(self, trace_id: str) -> TraceTree:
        """Return the full span tree for a trace.

        Fetches all span documents for the given traceId and reconstructs
        the parent-child hierarchy from parentSpanId links.

        Args:
            trace_id: OpenTelemetry trace ID (hex string).

        Returns:
            TraceTree with full span hierarchy.

        Raises:
            ValueError: If the trace ID is invalid or no spans are found.
            httpx.HTTPError: If the Elasticsearch request fails.
        """
        if not _is_valid_trace_id(trace_id):
            raise ValueError(f"Invalid trace ID format: {trace_id!r}")

        try:
            query: dict[str, Any] = {
                "query": {"term": {"traceId": trace_id}},
                "size": 10000,
            }

            response = await self.client.post(
                f"/{self.traces_index}/_search", json=query
            )
            response.raise_for_status()

            body = response.json()
            hits = body.get("hits", {}).get("hits", [])
            if not hits:
                raise ValueError(f"No spans found for trace {trace_id}")

            return _build_trace_tree(trace_id, hits)

        except httpx.HTTPError as e:
            logger.error(
                f"Failed to fetch trace {trace_id} from Elasticsearch: {e}",
                exc_info=True,
            )
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching trace: {e}", exc_info=True)
            raise

    async def get_traces(
        self, trace_ids: list[str]
    ) -> tuple[Sequence[TraceTree], PartialResultsInfo]:
        """Batch trace retrieval.

        Fetches all span documents for multiple trace IDs in a single query,
        then reconstructs a TraceTree per trace ID.

        Args:
            trace_ids: List of OpenTelemetry trace IDs.

        Returns:
            Tuple of (traces, partial_info).

        Raises:
            httpx.HTTPError: If the Elasticsearch request fails.
        """
        valid_ids = [tid for tid in trace_ids if _is_valid_trace_id(tid)]
        invalid_ids = [tid for tid in trace_ids if not _is_valid_trace_id(tid)]
        if invalid_ids:
            logger.warning(f"Skipping invalid trace IDs: {invalid_ids}")

        if not valid_ids:
            return [], PartialResultsInfo(
                total_requested=len(trace_ids),
                total_returned=0,
                is_partial=bool(trace_ids),
                reason="No valid trace IDs provided" if trace_ids else None,
            )

        try:
            query: dict[str, Any] = {
                "query": {"terms": {"traceId": valid_ids}},
                "size": 10000,
            }

            response = await self.client.post(
                f"/{self.traces_index}/_search", json=query
            )
            response.raise_for_status()

            body = response.json()
            hits = body.get("hits", {}).get("hits", [])
            # ES 7+ returns total as {"value": N, "relation": "eq"};
            # ES 6 and rest_total_hits_as_int=true return total as a plain int.
            total_raw = body.get("hits", {}).get("total", len(hits))
            if isinstance(total_raw, dict):
                total_available = total_raw.get("value", len(hits))
            else:
                total_available = int(total_raw)
            truncated = total_available > len(hits)
            if truncated:
                logger.warning(
                    "Elasticsearch returned %d of %d total span hits for batch of %d "
                    "traces; some trace trees may be incomplete",
                    len(hits),
                    total_available,
                    len(valid_ids),
                )

            by_trace: dict[str, list[dict[str, Any]]] = {}
            for hit in hits:
                tid = hit.get("_source", {}).get("traceId", "")
                if tid:
                    by_trace.setdefault(tid, []).append(hit)

            traces = []
            failed_ids: list[str] = []
            for tid in valid_ids:
                span_hits = by_trace.get(tid, [])
                if not span_hits:
                    logger.warning(f"No spans found for trace {tid}")
                    failed_ids.append(tid)
                    continue
                try:
                    traces.append(_build_trace_tree(tid, span_hits))
                except Exception as e:
                    logger.warning(
                        f"Failed to build trace tree for {tid}: {e}", exc_info=True
                    )
                    failed_ids.append(tid)

            is_partial = truncated or bool(failed_ids) or bool(invalid_ids)
            reason: str | None = None
            if truncated:
                reason = (
                    f"Span result truncated ({len(hits)}/{total_available}); "
                    f"trace trees may be incomplete"
                )
            elif failed_ids or invalid_ids:
                reason = f"Failed to retrieve {len(failed_ids) + len(invalid_ids)} traces"

            return traces, PartialResultsInfo(
                total_requested=len(trace_ids),
                total_returned=len(traces),
                is_partial=is_partial,
                reason=reason,
            )

        except httpx.HTTPError as e:
            logger.error(
                f"Failed to batch fetch traces from Elasticsearch: {e}", exc_info=True
            )
            raise
        except Exception as e:
            logger.error(f"Unexpected error in batch trace fetch: {e}", exc_info=True)
            raise

    async def get_correlated_logs(
        self, trace_ids: list[str], window_minutes: int = 5
    ) -> list[LogEntry]:
        """Return logs correlated with the given traces.

        Queries otel-logs-* for log documents whose traceId matches any of
        the provided trace IDs within the ``window_minutes`` minutes immediately
        preceding now (i.e. ``[now - window_minutes, now]``). Logs from traces
        older than ``window_minutes`` will not be returned regardless of trace ID
        match.

        Args:
            trace_ids: List of trace IDs to correlate logs with.
            window_minutes: Time window in minutes before now to search.

        Returns:
            List of LogEntry objects correlated with the traces.
            Log entries that cannot be parsed are skipped with a warning.

        Raises:
            httpx.HTTPError: If the Elasticsearch request fails.
        """
        if not trace_ids:
            return []

        valid_ids = [tid for tid in trace_ids if _is_valid_trace_id(tid)]
        if not valid_ids:
            logger.warning("No valid trace IDs provided for log correlation")
            return []

        try:
            now = datetime.now(UTC)
            since = now - timedelta(minutes=window_minutes)

            query: dict[str, Any] = {
                "query": {
                    "bool": {
                        "filter": [
                            {
                                "range": {
                                    "@timestamp": {
                                        "gte": since.isoformat(),
                                        "lte": now.isoformat(),
                                    }
                                }
                            },
                            {"terms": {"traceId": valid_ids}},
                        ]
                    }
                },
                "size": 500,
                "sort": [{"@timestamp": "desc"}],
            }

            response = await self.client.post(
                f"/{self.logs_index}/_search", json=query
            )
            response.raise_for_status()

            hits = response.json().get("hits", {}).get("hits", [])
            logs = []
            parse_errors = 0
            for hit in hits:
                try:
                    logs.append(self._parse_log_entry(hit))
                except Exception:
                    parse_errors += 1
            if parse_errors:
                logger.error(
                    "Failed to parse %d/%d log entries from Elasticsearch; "
                    "check otel-collector document schema",
                    parse_errors,
                    len(hits),
                )
            return logs

        except httpx.HTTPError as e:
            logger.error(
                f"Failed to fetch correlated logs from Elasticsearch: {e}",
                exc_info=True,
            )
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error fetching correlated logs: {e}", exc_info=True
            )
            raise

    async def get_events_for_signature(
        self, fingerprint: str, limit: int = 5
    ) -> list[ErrorEvent]:
        """Return recent events matching a known fingerprint.

        Fetches recent errors (24h lookback) and matches fingerprints locally.

        Args:
            fingerprint: Signature fingerprint hash.
            limit: Maximum number of events to return.

        Returns:
            List of ErrorEvent objects with matching fingerprints.

        Raises:
            httpx.HTTPError: If the underlying Elasticsearch request fails.
        """
        since = datetime.now(UTC) - timedelta(hours=24)
        all_errors = await self.get_recent_errors(since)

        fingerprinter = self._get_fingerprinter()
        matching = []
        fingerprint_errors = 0
        for error in all_errors:
            try:
                if fingerprinter(error) == fingerprint:
                    matching.append(error)
                    if len(matching) >= limit:
                        break
            except Exception as exc:
                fingerprint_errors += 1
                logger.error(
                    "Failed to fingerprint error event span=%s trace=%s: %s",
                    error.span_id,
                    error.trace_id,
                    exc,
                    exc_info=True,
                )
        if fingerprint_errors:
            logger.error(
                "Fingerprinting failed for %d/%d error(s) during signature lookup; "
                "results may be incomplete",
                fingerprint_errors,
                len(all_errors),
            )
        return matching

    def _parse_error_event(self, hit: dict[str, Any]) -> ErrorEvent:
        """Parse an Elasticsearch hit from otel-traces-* into an ErrorEvent.

        Document structure (otel mapping mode):
        - service.name: _source.resource.attributes["service.name"]
        - span attributes: _source.attributes (flat dict, dot-separated keys)
        - status: _source.status.code / _source.status.message
        - exception detail: attributes["exception.type|message|stacktrace"]
        """
        source = hit.get("_source", {})
        span_id = source.get("spanId", "")
        try:
            trace_id = source.get("traceId", "")
            service = (
                source.get("resource", {})
                .get("attributes", {})
                .get("service.name", "")
            )
            attributes: dict[str, Any] = dict(source.get("attributes", {}) or {})
            status = source.get("status", {}) or {}

            error_type = (
                attributes.get("exception.type")
                or source.get("name", "")
                or "UnknownError"
            )
            error_message = (
                attributes.get("exception.message")
                or status.get("message", "")
                or ""
            )

            stack_trace = attributes.get("exception.stacktrace", "")
            stack_frames = _parse_stack_trace(stack_trace) if stack_trace else ()

            ts_str = source.get("@timestamp", "")
            if ts_str:
                timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                logger.warning(
                    "Span %s in trace %s has no @timestamp; using current time as "
                    "fallback — check otel-collector configuration",
                    span_id,
                    source.get("traceId", "?"),
                )
                timestamp = datetime.now(UTC)

            return ErrorEvent(
                trace_id=trace_id,
                span_id=span_id,
                service=service,
                error_type=error_type,
                error_message=error_message,
                stack_frames=stack_frames,
                timestamp=timestamp,
                attributes=attributes,
                severity=Severity.ERROR,
            )

        except Exception as e:
            logger.warning(
                f"Failed to parse error event from span {span_id}: {e}",
                exc_info=True,
            )
            raise ValueError(
                f"Cannot parse error event from span {span_id}: {e}"
            ) from e

    async def search_logs(
        self,
        query: str,
        since: datetime,
        until: datetime | None = None,
        services: list[str] | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        """Search logs by keyword and optional metadata filters.

        Queries the otel-logs-* index using Elasticsearch Query DSL.

        Args:
            query: Keyword to search in log bodies. Empty string matches all.
            since: Return logs after this timestamp.
            until: Return logs before this timestamp. Defaults to now.
            services: Optional service name filter.
            limit: Maximum results to return.

        Returns:
            List of LogEntry objects in descending timestamp order.

        Raises:
            httpx.HTTPError: If the Elasticsearch request fails.
        """
        try:
            now = datetime.now(UTC)
            filter_items: list[dict[str, Any]] = [
                {"range": {"@timestamp": {"gte": since.isoformat(), "lte": (until or now).isoformat()}}},
            ]

            if query:
                filter_items.append({"match": {"body": query}})

            if services:
                valid_services = [s for s in services if _is_valid_identifier(s)]
                invalid = [s for s in services if not _is_valid_identifier(s)]
                if invalid:
                    logger.warning(f"Skipping invalid service names: {invalid}")
                if valid_services:
                    filter_items.append(
                        {"terms": {"resource.attributes.service.name": valid_services}}
                    )

            es_query: dict[str, Any] = {
                "query": {"bool": {"filter": filter_items}},
                "size": limit,
                "sort": [{"@timestamp": "desc"}],
            }

            response = await self.client.post(f"/{self.logs_index}/_search", json=es_query)
            response.raise_for_status()

            hits = response.json().get("hits", {}).get("hits", [])
            logs = []
            parse_errors = 0
            for hit in hits:
                try:
                    logs.append(self._parse_log_entry(hit))
                except Exception:
                    parse_errors += 1
            if parse_errors:
                logger.error(
                    "Failed to parse %d/%d log entries from Elasticsearch during search",
                    parse_errors,
                    len(hits),
                )
            return logs

        except httpx.HTTPError as e:
            logger.error(f"Failed to search logs in Elasticsearch: {e}", exc_info=True)
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

        Queries the otel-traces-* index using Elasticsearch Query DSL.

        Args:
            since: Return spans after this timestamp.
            until: Return spans before this timestamp. Defaults to now.
            services: Optional service name filter.
            operation: Optional operation name substring filter.
            attributes: Optional key-value span attribute filters.
            has_error: If set, filter to error or non-error spans only.
            limit: Maximum results to return.

        Returns:
            List of SpanSummary objects in descending timestamp order.

        Raises:
            httpx.HTTPError: If the Elasticsearch request fails.
        """
        try:
            now = datetime.now(UTC)
            filter_items: list[dict[str, Any]] = [
                {"range": {"@timestamp": {"gte": since.isoformat(), "lte": (until or now).isoformat()}}},
            ]

            if services:
                valid_services = [s for s in services if _is_valid_identifier(s)]
                invalid = [s for s in services if not _is_valid_identifier(s)]
                if invalid:
                    logger.warning(f"Skipping invalid service names: {invalid}")
                if valid_services:
                    filter_items.append(
                        {"terms": {"resource.attributes.service.name": valid_services}}
                    )

            if operation:
                filter_items.append({"match": {"name": operation}})

            if has_error is True:
                filter_items.append({"term": {"status.code": _ES_ERROR_STATUS}})
            elif has_error is False:
                filter_items.append(
                    {"bool": {"must_not": {"term": {"status.code": _ES_ERROR_STATUS}}}}
                )

            if attributes:
                for key, value in attributes.items():
                    if _is_valid_identifier(key):
                        filter_items.append({"term": {f"attributes.{key}": value}})

            es_query: dict[str, Any] = {
                "query": {"bool": {"filter": filter_items}},
                "size": limit,
                "sort": [{"@timestamp": "desc"}, {"_id": "asc"}],
            }

            response = await self.client.post(f"/{self.traces_index}/_search", json=es_query)
            response.raise_for_status()

            hits = response.json().get("hits", {}).get("hits", [])
            spans: list[SpanSummary] = []
            parse_errors = 0
            for hit in hits:
                try:
                    source = hit.get("_source", {})
                    ts_str = source.get("@timestamp", "")
                    timestamp = (
                        datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts_str
                        else now
                    )
                    status_code = (source.get("status") or {}).get("code", "")
                    spans.append(SpanSummary(
                        trace_id=source.get("traceId", ""),
                        span_id=source.get("spanId", ""),
                        service=(
                            source.get("resource", {})
                            .get("attributes", {})
                            .get("service.name", "")
                        ),
                        operation=source.get("name", ""),
                        duration_ms=int(source.get("durationNano", 0) or 0) / 1e6,
                        has_error=status_code == _ES_ERROR_STATUS,
                        timestamp=timestamp,
                        attributes=dict(source.get("attributes", {}) or {}),
                    ))
                except Exception:
                    parse_errors += 1
            if parse_errors:
                logger.error(
                    "Failed to parse %d/%d spans from Elasticsearch during search",
                    parse_errors,
                    len(hits),
                )
            return spans

        except httpx.HTTPError as e:
            logger.error(f"Failed to search spans in Elasticsearch: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error searching spans: {e}", exc_info=True)
            raise

    def _parse_log_entry(self, hit: dict[str, Any]) -> LogEntry:
        """Parse an Elasticsearch hit from otel-logs-* into a LogEntry.

        Merges log-level attributes with resource attributes into a single dict.
        """
        source = hit.get("_source", {})
        log_id = hit.get("_id", "?")
        trace_id_for_log = source.get("traceId", "?")
        try:
            ts_str = source.get("@timestamp", "")
            if ts_str:
                timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                logger.warning(
                    "Log entry _id=%s traceId=%s has no @timestamp; using current "
                    "time as fallback — check otel-collector log exporter configuration",
                    log_id,
                    trace_id_for_log,
                )
                timestamp = datetime.now(UTC)

            attributes: dict[str, Any] = {}
            log_attrs = source.get("attributes")
            if isinstance(log_attrs, dict):
                attributes.update(log_attrs)
            resource_attrs = source.get("resource", {}).get("attributes")
            if isinstance(resource_attrs, dict):
                attributes.update(resource_attrs)

            return LogEntry(
                timestamp=timestamp,
                severity=_parse_severity(
                    source.get("severityText", "INFO") or "INFO"
                ),
                body=source.get("body", ""),
                attributes=attributes,
                trace_id=source.get("traceId"),
                span_id=source.get("spanId"),
            )
        except Exception as e:
            logger.warning(
                f"Failed to parse log entry _id={log_id} traceId={trace_id_for_log}: {e}",
                exc_info=True,
            )
            raise ValueError(
                f"Cannot parse log entry _id={log_id} traceId={trace_id_for_log}: {e}"
            ) from e
