"""Tests for GrafanaStackTelemetryAdapter: list_services, search_logs, search_spans."""

from datetime import UTC, datetime

import httpx
import pytest

from rounds.adapters.telemetry.grafana_stack import GrafanaStackTelemetryAdapter


def _make_adapter(
    loki_transport: httpx.MockTransport,
    tempo_transport: httpx.MockTransport | None = None,
) -> GrafanaStackTelemetryAdapter:
    adapter = GrafanaStackTelemetryAdapter(
        tempo_url="http://tempo-test:3200",
        loki_url="http://loki-test:3100",
    )
    adapter.loki_client = httpx.AsyncClient(
        base_url="http://loki-test:3100",
        transport=loki_transport,
    )
    if tempo_transport is not None:
        adapter.tempo_client = httpx.AsyncClient(
            base_url="http://tempo-test:3200",
            transport=tempo_transport,
        )
    return adapter


_SINCE = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_UNTIL = datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC)


class TestListServices:
    """Tests for GrafanaStackTelemetryAdapter.list_services().

    Queries Loki's label values API for service_name label.
    """

    @pytest.mark.asyncio
    async def test_returns_sorted_service_names(self):
        def loki_handler(request: httpx.Request) -> httpx.Response:
            assert "/loki/api/v1/label/service_name/values" in request.url.path
            return httpx.Response(200, json={
                "status": "success",
                "data": ["zebra-api", "alpha-service", "beta-worker"],
            })

        adapter = _make_adapter(httpx.MockTransport(loki_handler))
        services = await adapter.list_services()
        assert services == ["alpha-service", "beta-worker", "zebra-api"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty_list(self):
        def loki_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "success", "data": []})

        adapter = _make_adapter(httpx.MockTransport(loki_handler))
        services = await adapter.list_services()
        assert services == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_malformed_response_returns_empty_list(self):
        def loki_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "success", "data": "not-a-list"})

        adapter = _make_adapter(httpx.MockTransport(loki_handler))
        services = await adapter.list_services()
        assert services == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_skips_empty_service_names(self):
        def loki_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "status": "success",
                "data": ["real-service", "", None],
            })

        adapter = _make_adapter(httpx.MockTransport(loki_handler))
        services = await adapter.list_services()
        assert services == ["real-service"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        def loki_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        adapter = _make_adapter(httpx.MockTransport(loki_handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.list_services()
        await adapter.close()


class TestSearchLogs:
    """Tests for GrafanaStackTelemetryAdapter.search_logs().

    Uses Loki's query_range API with LogQL queries.
    """

    _LOKI_STREAM_RESPONSE = {
        "data": {
            "result": [
                {
                    "stream": {"service_name": "api"},
                    "values": [
                        [str(int(_SINCE.timestamp() * 1e9)), "connection refused to db"],
                        [str(int(_SINCE.timestamp() * 1e9) + 1_000_000_000), "timeout error"],
                    ],
                }
            ]
        }
    }

    @pytest.mark.asyncio
    async def test_returns_log_entries(self):
        def loki_handler(request: httpx.Request) -> httpx.Response:
            if "query_range" in request.url.path:
                return httpx.Response(200, json=self._LOKI_STREAM_RESPONSE)
            return httpx.Response(404)

        adapter = _make_adapter(httpx.MockTransport(loki_handler))
        # Keyword filtering is delegated to Loki via LogQL; mock returns all values
        logs = await adapter.search_logs("connection", since=_SINCE, until=_UNTIL)
        assert len(logs) == 2
        assert logs[0].body == "connection refused to db"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_query_uses_stream_selector_only(self):
        captured_params: list[dict] = []

        def loki_handler(request: httpx.Request) -> httpx.Response:
            if "query_range" in request.url.path:
                captured_params.append(dict(request.url.params))
            return httpx.Response(200, json={"data": {"result": []}})

        adapter = _make_adapter(httpx.MockTransport(loki_handler))
        await adapter.search_logs("", since=_SINCE, until=_UNTIL)
        assert captured_params
        logql = captured_params[0]["query"]
        assert "|=" not in logql
        await adapter.close()

    @pytest.mark.asyncio
    async def test_keyword_filter_uses_logql_substring_match(self):
        captured_params: list[dict] = []

        def loki_handler(request: httpx.Request) -> httpx.Response:
            if "query_range" in request.url.path:
                captured_params.append(dict(request.url.params))
            return httpx.Response(200, json={"data": {"result": []}})

        adapter = _make_adapter(httpx.MockTransport(loki_handler))
        await adapter.search_logs("error keyword", since=_SINCE, until=_UNTIL)
        logql = captured_params[0]["query"]
        assert "|=" in logql
        assert "error keyword" in logql
        await adapter.close()

    @pytest.mark.asyncio
    async def test_service_filter_uses_regex_stream_selector(self):
        captured_params: list[dict] = []

        def loki_handler(request: httpx.Request) -> httpx.Response:
            if "query_range" in request.url.path:
                captured_params.append(dict(request.url.params))
            return httpx.Response(200, json={"data": {"result": []}})

        adapter = _make_adapter(httpx.MockTransport(loki_handler))
        await adapter.search_logs("error", since=_SINCE, services=["api", "worker"])
        logql = captured_params[0]["query"]
        assert "service=~" in logql
        assert "api" in logql
        assert "worker" in logql
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invalid_services_filtered_out_returns_empty(self):
        """If all services are invalid, should return empty without querying Loki."""
        call_count = 0

        def loki_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json={"data": {"result": []}})

        adapter = _make_adapter(httpx.MockTransport(loki_handler))
        logs = await adapter.search_logs("error", since=_SINCE, services=["bad name!", "also bad!"])
        assert logs == []
        # query_range should not have been called since no valid services
        assert call_count == 0
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        def loki_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="Service Unavailable")

        adapter = _make_adapter(httpx.MockTransport(loki_handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.search_logs("error", since=_SINCE)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_respects_limit_parameter(self):
        captured_params: list[dict] = []

        def loki_handler(request: httpx.Request) -> httpx.Response:
            if "query_range" in request.url.path:
                captured_params.append(dict(request.url.params))
            return httpx.Response(200, json={"data": {"result": []}})

        adapter = _make_adapter(httpx.MockTransport(loki_handler))
        await adapter.search_logs("error", since=_SINCE, limit=25)
        assert captured_params[0]["limit"] == "25"
        await adapter.close()


class TestSearchSpans:
    """Tests for GrafanaStackTelemetryAdapter.search_spans().

    Uses Tempo's /api/search API; returns trace-level summaries.
    """

    _TEMPO_RESPONSE = {
        "traces": [
            {
                "traceID": "trace1",
                "rootServiceName": "api",
                "rootName": "GET /users",
                "startTimeUnixNano": str(int(_SINCE.timestamp() * 1e9)),
                "durationMs": 42.0,
                "spanSets": [
                    {
                        "spans": [
                            {"attributes": {"otel.status_code": "OK"}}
                        ]
                    }
                ],
            }
        ]
    }

    @pytest.mark.asyncio
    async def test_returns_span_summaries(self):
        def tempo_handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/search"
            return httpx.Response(200, json=self._TEMPO_RESPONSE)

        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
            httpx.MockTransport(tempo_handler),
        )
        spans = await adapter.search_spans(since=_SINCE, until=_UNTIL)
        assert len(spans) == 1
        assert spans[0].trace_id == "trace1"
        assert spans[0].service == "api"
        assert spans[0].operation == "GET /users"
        assert spans[0].duration_ms == pytest.approx(42.0)
        assert spans[0].has_error is False
        await adapter.close()

    @pytest.mark.asyncio
    async def test_has_error_true_filters_non_error_traces(self):
        tempo_response = {
            "traces": [
                {
                    "traceID": "t-error",
                    "rootServiceName": "svc",
                    "rootName": "op",
                    "startTimeUnixNano": str(int(_SINCE.timestamp() * 1e9)),
                    "durationMs": 1.0,
                    "spanSets": [
                        {"spans": [{"attributes": {"otel.status_code": "ERROR"}}]}
                    ],
                },
                {
                    "traceID": "t-ok",
                    "rootServiceName": "svc",
                    "rootName": "op2",
                    "startTimeUnixNano": str(int(_SINCE.timestamp() * 1e9)),
                    "durationMs": 2.0,
                    "spanSets": [
                        {"spans": [{"attributes": {"otel.status_code": "OK"}}]}
                    ],
                },
            ]
        }

        def tempo_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=tempo_response)

        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
            httpx.MockTransport(tempo_handler),
        )
        spans = await adapter.search_spans(since=_SINCE, has_error=True)
        assert all(s.has_error for s in spans)
        assert any(s.trace_id == "t-error" for s in spans)
        assert not any(s.trace_id == "t-ok" for s in spans)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_has_error_false_filters_error_traces(self):
        tempo_response = {
            "traces": [
                {
                    "traceID": "t-error",
                    "rootServiceName": "svc",
                    "rootName": "op",
                    "startTimeUnixNano": str(int(_SINCE.timestamp() * 1e9)),
                    "durationMs": 1.0,
                    "spanSets": [
                        {"spans": [{"attributes": {"otel.status_code": "ERROR"}}]}
                    ],
                },
                {
                    "traceID": "t-ok",
                    "rootServiceName": "svc",
                    "rootName": "op2",
                    "startTimeUnixNano": str(int(_SINCE.timestamp() * 1e9)),
                    "durationMs": 2.0,
                    "spanSets": [
                        {"spans": [{"attributes": {"otel.status_code": "OK"}}]}
                    ],
                },
            ]
        }

        def tempo_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=tempo_response)

        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
            httpx.MockTransport(tempo_handler),
        )
        spans = await adapter.search_spans(since=_SINCE, has_error=False)
        assert all(not s.has_error for s in spans)
        assert any(s.trace_id == "t-ok" for s in spans)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_service_filter_passed_as_param(self):
        captured_params: list[dict] = []

        def tempo_handler(request: httpx.Request) -> httpx.Response:
            captured_params.append(dict(request.url.params))
            return httpx.Response(200, json={"traces": []})

        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
            httpx.MockTransport(tempo_handler),
        )
        await adapter.search_spans(since=_SINCE, services=["my-service"])
        assert any(p.get("service.name") == "my-service" for p in captured_params)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_operation_filter_passed_as_param(self):
        captured_params: list[dict] = []

        def tempo_handler(request: httpx.Request) -> httpx.Response:
            captured_params.append(dict(request.url.params))
            return httpx.Response(200, json={"traces": []})

        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
            httpx.MockTransport(tempo_handler),
        )
        await adapter.search_spans(since=_SINCE, operation="POST /items")
        assert any(p.get("name") == "POST /items" for p in captured_params)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_has_error_true_adds_error_status_param(self):
        captured_params: list[dict] = []

        def tempo_handler(request: httpx.Request) -> httpx.Response:
            captured_params.append(dict(request.url.params))
            return httpx.Response(200, json={"traces": []})

        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
            httpx.MockTransport(tempo_handler),
        )
        await adapter.search_spans(since=_SINCE, has_error=True)
        assert any(p.get("spanStatus") == "error" for p in captured_params)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        def tempo_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="Service Unavailable")

        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
            httpx.MockTransport(tempo_handler),
        )
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.search_spans(since=_SINCE)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_traces_response_returns_empty_list(self):
        def tempo_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"traces": []})

        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
            httpx.MockTransport(tempo_handler),
        )
        spans = await adapter.search_spans(since=_SINCE)
        assert spans == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_span_set_error_detection_from_otel_status(self):
        """Traces with otel.status_code=ERROR in any spanSet span are marked has_error=True."""
        tempo_response = {
            "traces": [
                {
                    "traceID": "t1",
                    "rootServiceName": "svc",
                    "rootName": "op",
                    "startTimeUnixNano": str(int(_SINCE.timestamp() * 1e9)),
                    "durationMs": 5.0,
                    "spanSets": [
                        {
                            "spans": [
                                {"attributes": {"otel.status_code": "OK"}},
                                {"attributes": {"otel.status_code": "ERROR"}},
                            ]
                        }
                    ],
                }
            ]
        }

        def tempo_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=tempo_response)

        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
            httpx.MockTransport(tempo_handler),
        )
        spans = await adapter.search_spans(since=_SINCE)
        assert len(spans) == 1
        assert spans[0].has_error is True
        await adapter.close()
