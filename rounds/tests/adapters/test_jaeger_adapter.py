"""Tests for JaegerTelemetryAdapter: list_services, search_logs, search_spans."""

from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
import pytest

from rounds.adapters.telemetry.jaeger import JaegerTelemetryAdapter


def _make_adapter(transport: httpx.MockTransport) -> JaegerTelemetryAdapter:
    adapter = JaegerTelemetryAdapter(api_url="http://jaeger-test:16686")
    adapter.client = httpx.AsyncClient(
        base_url="http://jaeger-test:16686",
        transport=transport,
    )
    return adapter


_SINCE = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_UNTIL = datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC)

_SERVICES_RESPONSE = {"data": ["alpha-service", "zebra-api", "beta-worker"]}

_TRACE_WITH_LOG = {
    "traceID": "abc123",
    "spans": [
        {
            "spanID": "span1",
            "operationName": "GET /items",
            "startTime": int(_SINCE.timestamp() * 1e6),
            "duration": 10_000,
            "processID": "p1",
            "tags": [{"key": "error", "value": True}],
            "logs": [
                {
                    "timestamp": int(_SINCE.timestamp() * 1e6),
                    "fields": [
                        {"key": "message", "value": "connection refused"},
                        {"key": "level", "value": "error"},
                    ],
                }
            ],
        }
    ],
    "processes": {"p1": {"serviceName": "alpha-service"}},
}


class TestListServices:
    """Tests for JaegerTelemetryAdapter.list_services()."""

    @pytest.mark.asyncio
    async def test_returns_sorted_service_names(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/services"
            return httpx.Response(200, json=_SERVICES_RESPONSE)

        adapter = _make_adapter(httpx.MockTransport(handler))
        services = await adapter.list_services()
        assert services == ["alpha-service", "beta-worker", "zebra-api"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        adapter = _make_adapter(httpx.MockTransport(handler))
        services = await adapter.list_services()
        assert services == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_malformed_response_returns_empty_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": "not-a-list"})

        adapter = _make_adapter(httpx.MockTransport(handler))
        services = await adapter.list_services()
        assert services == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_skips_empty_service_names(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": ["real-service", "", None]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        services = await adapter.list_services()
        assert services == ["real-service"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.list_services()
        await adapter.close()


class TestSearchLogs:
    """Tests for JaegerTelemetryAdapter.search_logs().

    Jaeger extracts logs from span log events, not a dedicated log API.
    """

    @pytest.mark.asyncio
    async def test_extracts_log_entries_from_span_events(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["alpha-service"]})
            call_count += 1
            return httpx.Response(200, json={"data": [_TRACE_WITH_LOG]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        logs = await adapter.search_logs("connection", since=_SINCE, until=_UNTIL)
        assert len(logs) == 1
        assert logs[0].body == "connection refused"
        assert logs[0].trace_id == "abc123"
        assert logs[0].span_id == "span1"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_keyword_filter_excludes_non_matching_logs(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["alpha-service"]})
            return httpx.Response(200, json={"data": [_TRACE_WITH_LOG]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        logs = await adapter.search_logs("timeout", since=_SINCE, until=_UNTIL)
        assert logs == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_query_returns_all_logs(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["alpha-service"]})
            return httpx.Response(200, json={"data": [_TRACE_WITH_LOG]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        logs = await adapter.search_logs("", since=_SINCE, until=_UNTIL)
        assert len(logs) == 1
        await adapter.close()

    @pytest.mark.asyncio
    async def test_service_filter_limits_queried_services(self) -> None:
        queried_services: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["svc-a", "svc-b", "svc-c"]})
            svc = request.url.params.get("service", "")
            queried_services.append(svc)
            return httpx.Response(200, json={"data": []})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_logs("error", since=_SINCE, services=["svc-a"])
        assert queried_services == ["svc-a"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invalid_service_names_skipped(self) -> None:
        queried_services: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["valid-svc"]})
            svc = request.url.params.get("service", "")
            queried_services.append(svc)
            return httpx.Response(200, json={"data": []})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_logs(
            "error", since=_SINCE, services=["valid-svc", "bad name!"]
        )
        assert "bad name!" not in queried_services
        await adapter.close()

    @pytest.mark.asyncio
    async def test_log_entry_timestamp_parsed_from_microseconds(self) -> None:
        ts_us = int(_SINCE.timestamp() * 1e6)
        trace = {
            "traceID": "t1",
            "spans": [
                {
                    "spanID": "s1",
                    "operationName": "op",
                    "startTime": ts_us,
                    "duration": 1000,
                    "processID": "p1",
                    "tags": [],
                    "logs": [
                        {
                            "timestamp": ts_us,
                            "fields": [{"key": "message", "value": "hello"}],
                        }
                    ],
                }
            ],
            "processes": {"p1": {"serviceName": "svc"}},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["svc"]})
            return httpx.Response(200, json={"data": [trace]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        logs = await adapter.search_logs("", since=_SINCE)
        assert len(logs) == 1
        assert logs[0].timestamp == _SINCE
        await adapter.close()


class TestSearchSpans:
    """Tests for JaegerTelemetryAdapter.search_spans()."""

    _BASE_TRACE: ClassVar[dict[str, Any]] = {
        "traceID": "trace1",
        "spans": [
            {
                "spanID": "span1",
                "operationName": "GET /users",
                "startTime": int(_SINCE.timestamp() * 1e6),
                "duration": 20_000,
                "processID": "p1",
                "tags": [
                    {"key": "http.method", "value": "GET"},
                    {"key": "error", "value": True},
                ],
                "logs": [],
            }
        ],
        "processes": {"p1": {"serviceName": "api"}},
    }

    @pytest.mark.asyncio
    async def test_returns_span_summaries(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["api"]})
            return httpx.Response(200, json={"data": [self._BASE_TRACE]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        spans = await adapter.search_spans(since=_SINCE, until=_UNTIL)
        assert len(spans) == 1
        assert spans[0].trace_id == "trace1"
        assert spans[0].span_id == "span1"
        assert spans[0].operation == "GET /users"
        assert spans[0].has_error is True
        assert spans[0].duration_ms == pytest.approx(20.0)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_has_error_true_passes_error_tag(self) -> None:
        captured_params: list[dict[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["api"]})
            captured_params.append(dict(request.url.params))
            return httpx.Response(200, json={"data": []})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_spans(since=_SINCE, services=["api"], has_error=True)
        assert any(p.get("tags") == "error=true" for p in captured_params)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_has_error_false_excludes_error_spans(self) -> None:
        """has_error=False should filter out spans flagged as errors post-fetch."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["api"]})
            return httpx.Response(200, json={"data": [self._BASE_TRACE]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        spans = await adapter.search_spans(
            since=_SINCE, has_error=False, services=["api"]
        )
        # _BASE_TRACE has error=true tag, so it should be filtered out
        assert all(not s.has_error for s in spans)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_operation_filter_passed_as_param(self) -> None:
        captured_params: list[dict[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["api"]})
            captured_params.append(dict(request.url.params))
            return httpx.Response(200, json={"data": []})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_spans(
            since=_SINCE, services=["api"], operation="GET /users"
        )
        assert any(p.get("operation") == "GET /users" for p in captured_params)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_attribute_filter_applied_post_fetch(self) -> None:
        """Attribute filters are applied locally after fetching spans."""
        trace_no_match = {
            "traceID": "trace2",
            "spans": [
                {
                    "spanID": "span2",
                    "operationName": "POST /items",
                    "startTime": int(_SINCE.timestamp() * 1e6),
                    "duration": 5000,
                    "processID": "p1",
                    "tags": [{"key": "http.method", "value": "POST"}],
                    "logs": [],
                }
            ],
            "processes": {"p1": {"serviceName": "api"}},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["api"]})
            return httpx.Response(
                200, json={"data": [self._BASE_TRACE, trace_no_match]}
            )

        adapter = _make_adapter(httpx.MockTransport(handler))
        spans = await adapter.search_spans(
            since=_SINCE, services=["api"], attributes={"http.method": "GET"}
        )
        assert all(s.attributes.get("http.method") == "GET" for s in spans)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_service_filter_limits_queried_services(self) -> None:
        queried_services: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["svc-a", "svc-b"]})
            queried_services.append(request.url.params.get("service", ""))
            return httpx.Response(200, json={"data": []})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_spans(since=_SINCE, services=["svc-a"])
        assert queried_services == ["svc-a"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_results_sorted_descending_by_timestamp(self) -> None:
        earlier = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        later = datetime(2024, 1, 1, 12, 0, 30, tzinfo=UTC)

        trace_multi = {
            "traceID": "t1",
            "spans": [
                {
                    "spanID": "s1",
                    "operationName": "op1",
                    "startTime": int(earlier.timestamp() * 1e6),
                    "duration": 1000,
                    "processID": "p1",
                    "tags": [],
                    "logs": [],
                },
                {
                    "spanID": "s2",
                    "operationName": "op2",
                    "startTime": int(later.timestamp() * 1e6),
                    "duration": 1000,
                    "processID": "p1",
                    "tags": [],
                    "logs": [],
                },
            ],
            "processes": {"p1": {"serviceName": "api"}},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["api"]})
            return httpx.Response(200, json={"data": [trace_multi]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        spans = await adapter.search_spans(since=_SINCE, services=["api"])
        assert spans[0].timestamp >= spans[-1].timestamp
        await adapter.close()


# ---------------------------------------------------------------------------
# Shared fixture for trace-response tests
# ---------------------------------------------------------------------------

_TRACE_WITH_PARENT_CHILD = {
    "traceID": "abc111def222abc1",
    "spans": [
        {
            "spanID": "root-span",
            "operationName": "root-op",
            "startTime": int(_SINCE.timestamp() * 1e6),
            "duration": 20_000,
            "processID": "p1",
            "tags": [],
            "logs": [],
        },
        {
            "spanID": "child-span",
            "parentSpanID": "root-span",
            "operationName": "child-op",
            "startTime": int(_SINCE.timestamp() * 1e6),
            "duration": 5_000,
            "processID": "p1",
            "tags": [{"key": "error", "value": True}],
            "logs": [],
        },
    ],
    "processes": {"p1": {"serviceName": "api"}},
}

_TRACE_SINGLE_SPAN = {
    "traceID": "def333abc444def3",
    "spans": [
        {
            "spanID": "solo-span",
            "operationName": "GET /health",
            "startTime": int(_SINCE.timestamp() * 1e6),
            "duration": 1_000,
            "processID": "p1",
            "tags": [],
            "logs": [
                {
                    "timestamp": int(_SINCE.timestamp() * 1e6),
                    "fields": [{"key": "message", "value": "health check ok"}],
                }
            ],
        }
    ],
    "processes": {"p1": {"serviceName": "api"}},
}


class TestGetRecentErrors:
    """Tests for JaegerTelemetryAdapter.get_recent_errors()."""

    @pytest.mark.asyncio
    async def test_returns_error_events_from_error_spans(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["api"]})
            return httpx.Response(200, json={"data": [_TRACE_WITH_LOG]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        errors = await adapter.get_recent_errors(_SINCE)
        assert len(errors) >= 1
        assert errors[0].trace_id == "abc123"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_no_error_spans_returns_empty(self) -> None:
        trace_no_error = {
            "traceID": "t1",
            "spans": [
                {
                    "spanID": "s1",
                    "operationName": "op",
                    "startTime": int(_SINCE.timestamp() * 1e6),
                    "duration": 1_000,
                    "processID": "p1",
                    "tags": [],
                    "logs": [],
                }
            ],
            "processes": {"p1": {"serviceName": "api"}},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["api"]})
            return httpx.Response(200, json={"data": [trace_no_error]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        errors = await adapter.get_recent_errors(_SINCE)
        assert errors == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_service_filter_limits_queried_services(self) -> None:
        queried: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["api", "worker"]})
            queried.append(request.url.params.get("service", ""))
            return httpx.Response(200, json={"data": []})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.get_recent_errors(_SINCE, services=["api"])
        assert queried == ["api"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invalid_service_names_skipped(self) -> None:
        queried: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["valid-svc"]})
            queried.append(request.url.params.get("service", ""))
            return httpx.Response(200, json={"data": []})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.get_recent_errors(_SINCE, services=["valid-svc", "bad name!"])
        assert "bad name!" not in queried
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["api"]})
            return httpx.Response(500, text="Internal Server Error")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.get_recent_errors(_SINCE)
        await adapter.close()


class TestGetTrace:
    """Tests for JaegerTelemetryAdapter.get_trace()."""

    @pytest.mark.asyncio
    async def test_returns_trace_tree(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/api/traces/abc111def222abc1" in request.url.path
            return httpx.Response(200, json={"data": [_TRACE_WITH_PARENT_CHILD]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        tree = await adapter.get_trace("abc111def222abc1")
        assert tree.trace_id == "abc111def222abc1"
        assert tree.root_span.span_id == "root-span"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_child_span_attached_to_root(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [_TRACE_WITH_PARENT_CHILD]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        tree = await adapter.get_trace("abc111def222abc1")
        child_ids = {c.span_id for c in tree.root_span.children}
        assert "child-span" in child_ids
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invalid_trace_id_raises_value_error(self) -> None:
        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        )
        with pytest.raises(ValueError, match="Invalid trace ID"):
            await adapter.get_trace("not-hex!")
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_data_raises_value_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(ValueError, match="No trace found"):
            await adapter.get_trace("abc123")
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.get_trace("abc123")
        await adapter.close()

    @pytest.mark.asyncio
    async def test_error_spans_collected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [_TRACE_WITH_PARENT_CHILD]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        tree = await adapter.get_trace("abc111def222abc1")
        # child-span has error=True tag
        assert len(tree.error_spans) > 0
        await adapter.close()


class TestGetTraces:
    """Tests for JaegerTelemetryAdapter.get_traces()."""

    @pytest.mark.asyncio
    async def test_returns_traces_for_valid_ids(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [_TRACE_SINGLE_SPAN]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        traces, info = await adapter.get_traces(["def333abc444def3"])
        assert len(traces) == 1
        assert info.total_returned == 1
        assert not info.is_partial
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self) -> None:
        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        )
        traces, info = await adapter.get_traces([])
        assert traces == []
        assert info.total_requested == 0
        assert not info.is_partial
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invalid_ids_counted_as_partial(self) -> None:
        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        )
        traces, info = await adapter.get_traces(["not-hex!"])
        assert traces == []
        assert info.is_partial
        await adapter.close()

    @pytest.mark.asyncio
    async def test_failed_fetch_counted_as_partial(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(200, json={"data": [_TRACE_SINGLE_SPAN]})
            return httpx.Response(500, text="Error")

        adapter = _make_adapter(httpx.MockTransport(handler))
        traces, info = await adapter.get_traces(
            ["def333abc444def3", "abc999def000abc9"]
        )
        assert len(traces) == 1
        assert info.is_partial
        await adapter.close()


class TestGetCorrelatedLogs:
    """Tests for JaegerTelemetryAdapter.get_correlated_logs()."""

    @pytest.mark.asyncio
    async def test_extracts_logs_from_trace_spans(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [_TRACE_SINGLE_SPAN]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        logs = await adapter.get_correlated_logs(["def333abc444def3"])
        assert len(logs) == 1
        assert logs[0].body == "health check ok"
        assert logs[0].trace_id == "def333abc444def3"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_trace_ids_returns_empty(self) -> None:
        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        )
        logs = await adapter.get_correlated_logs([])
        assert logs == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invalid_trace_id_raises_value_error(self) -> None:
        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        )
        with pytest.raises(ValueError, match="Invalid trace ID"):
            await adapter.get_correlated_logs(["not-hex!"])
        await adapter.close()

    @pytest.mark.asyncio
    async def test_span_without_logs_returns_no_entries(self) -> None:
        trace = {
            "traceID": "t1",
            "spans": [
                {
                    "spanID": "s1",
                    "operationName": "op",
                    "startTime": int(_SINCE.timestamp() * 1e6),
                    "duration": 1_000,
                    "processID": "p1",
                    "tags": [],
                    "logs": [],
                }
            ],
            "processes": {"p1": {"serviceName": "svc"}},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [trace]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        logs = await adapter.get_correlated_logs(["abc123"])
        assert logs == []
        await adapter.close()


class TestGetEventsForSignature:
    """Tests for JaegerTelemetryAdapter.get_events_for_signature()."""

    @pytest.mark.asyncio
    async def test_queries_each_service_with_fingerprint_tag(self) -> None:
        queried_tags: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["api"]})
            queried_tags.append(request.url.params.get("tags", ""))
            return httpx.Response(200, json={"data": []})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.get_events_for_signature("fp123")
        assert any("fp123" in t for t in queried_tags)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_no_services_returns_empty(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        adapter = _make_adapter(httpx.MockTransport(handler))
        events = await adapter.get_events_for_signature("fp123")
        assert events == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_limit_caps_results(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/services":
                return httpx.Response(200, json={"data": ["api"]})
            return httpx.Response(200, json={"data": [_TRACE_WITH_LOG]})

        adapter = _make_adapter(httpx.MockTransport(handler))
        # _TRACE_WITH_LOG produces 1 error event; limit=1 should work
        events = await adapter.get_events_for_signature("fp123", limit=1)
        assert len(events) <= 1
        await adapter.close()
