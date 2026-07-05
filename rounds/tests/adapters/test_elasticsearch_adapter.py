"""Tests for ElasticsearchTelemetryAdapter: all TelemetryPort methods."""

from datetime import UTC, datetime

import httpx
import pytest

from rounds.adapters.telemetry.elasticsearch import (
    ElasticsearchTelemetryAdapter,
    _build_trace_tree,
)


def _make_adapter(transport: httpx.MockTransport) -> ElasticsearchTelemetryAdapter:
    adapter = ElasticsearchTelemetryAdapter(es_url="http://es-test:9200", api_key="test-key")
    adapter.client = httpx.AsyncClient(
        base_url="http://es-test:9200",
        headers={"Content-Type": "application/json", "Authorization": "ApiKey test-key"},
        transport=transport,
    )
    return adapter


_SINCE = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_UNTIL = datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC)


class TestListServices:
    """Tests for ElasticsearchTelemetryAdapter.list_services()."""

    @pytest.mark.asyncio
    async def test_returns_sorted_service_names(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "_search" in request.url.path
            return httpx.Response(200, json={
                "aggregations": {
                    "services": {
                        "buckets": [
                            {"key": "zebra-api", "doc_count": 10},
                            {"key": "alpha-worker", "doc_count": 5},
                            {"key": "beta-service", "doc_count": 3},
                        ]
                    }
                }
            })

        adapter = _make_adapter(httpx.MockTransport(handler))
        services = await adapter.list_services()
        assert services == ["alpha-worker", "beta-service", "zebra-api"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_buckets_returns_empty_list(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"aggregations": {"services": {"buckets": []}}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        services = await adapter.list_services()
        assert services == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_skips_buckets_with_empty_key(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "aggregations": {
                    "services": {
                        "buckets": [
                            {"key": "real-service", "doc_count": 5},
                            {"key": "", "doc_count": 2},
                        ]
                    }
                }
            })

        adapter = _make_adapter(httpx.MockTransport(handler))
        services = await adapter.list_services()
        assert services == ["real-service"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.list_services()
        await adapter.close()

    @pytest.mark.asyncio
    async def test_missing_aggregations_returns_empty_list(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"hits": {"total": 0}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        services = await adapter.list_services()
        assert services == []
        await adapter.close()


class TestSearchLogs:
    """Tests for ElasticsearchTelemetryAdapter.search_logs()."""

    @pytest.mark.asyncio
    async def test_returns_log_entries_for_keyword_query(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "otel-logs" in request.url.path
            return httpx.Response(200, json={
                "hits": {
                    "hits": [
                        {
                            "_id": "log-1",
                            "_source": {
                                "@timestamp": "2024-01-01T12:30:00Z",
                                "body": "connection refused to database",
                                "severityText": "ERROR",
                                "traceId": "abc123",
                                "spanId": "span1",
                                "attributes": {"service": "api"},
                                "resource": {"attributes": {"service.name": "api"}},
                            },
                        }
                    ]
                }
            })

        adapter = _make_adapter(httpx.MockTransport(handler))
        logs = await adapter.search_logs("connection refused", since=_SINCE, until=_UNTIL)
        assert len(logs) == 1
        assert logs[0].body == "connection refused to database"
        assert logs[0].trace_id == "abc123"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_query_matches_all(self):
        """Empty query string should not add a match filter."""
        captured_body: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured_body.append(json.loads(request.content))
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_logs("", since=_SINCE, until=_UNTIL)
        # With empty query, no "match" filter should be added
        filters = captured_body[0]["query"]["bool"]["filter"]
        assert not any("match" in f for f in filters)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_service_filter_adds_terms_clause(self):
        captured_body: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured_body.append(json.loads(request.content))
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_logs("error", since=_SINCE, services=["api", "worker"])
        filters = captured_body[0]["query"]["bool"]["filter"]
        terms_filter = next((f for f in filters if "terms" in f), None)
        assert terms_filter is not None
        assert "api" in terms_filter["terms"]["resource.attributes.service.name"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invalid_service_names_are_skipped(self):
        captured_body: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured_body.append(json.loads(request.content))
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_logs("error", since=_SINCE, services=["valid-svc", "bad svc!"])
        filters = captured_body[0]["query"]["bool"]["filter"]
        terms_filter = next((f for f in filters if "terms" in f), None)
        assert terms_filter is not None
        valid_names = terms_filter["terms"]["resource.attributes.service.name"]
        assert "valid-svc" in valid_names
        assert "bad svc!" not in valid_names
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="Service Unavailable")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.search_logs("error", since=_SINCE)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_respects_limit_parameter(self):
        captured_body: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured_body.append(json.loads(request.content))
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_logs("error", since=_SINCE, limit=42)
        assert captured_body[0]["size"] == 42
        await adapter.close()


class TestSearchSpans:
    """Tests for ElasticsearchTelemetryAdapter.search_spans()."""

    @pytest.mark.asyncio
    async def test_returns_span_summaries(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "otel-traces" in request.url.path
            return httpx.Response(200, json={
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "@timestamp": "2024-01-01T12:30:00Z",
                                "traceId": "trace1",
                                "spanId": "span1",
                                "name": "GET /users",
                                "durationNano": 5_000_000,
                                "status": {"code": "STATUS_CODE_ERROR"},
                                "resource": {"attributes": {"service.name": "api"}},
                                "attributes": {},
                            }
                        }
                    ]
                }
            })

        adapter = _make_adapter(httpx.MockTransport(handler))
        spans = await adapter.search_spans(since=_SINCE, until=_UNTIL)
        assert len(spans) == 1
        assert spans[0].trace_id == "trace1"
        assert spans[0].span_id == "span1"
        assert spans[0].operation == "GET /users"
        assert spans[0].has_error is True
        assert spans[0].duration_ms == pytest.approx(5.0)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_has_error_true_adds_error_filter(self):
        captured_body: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured_body.append(json.loads(request.content))
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_spans(since=_SINCE, has_error=True)
        filters = captured_body[0]["query"]["bool"]["filter"]
        term_filter = next((f for f in filters if "term" in f), None)
        assert term_filter is not None
        assert term_filter["term"]["status.code"] == "STATUS_CODE_ERROR"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_has_error_false_adds_must_not_filter(self):
        captured_body: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured_body.append(json.loads(request.content))
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_spans(since=_SINCE, has_error=False)
        filters = captured_body[0]["query"]["bool"]["filter"]
        bool_filter = next((f for f in filters if "bool" in f), None)
        assert bool_filter is not None
        assert "must_not" in bool_filter["bool"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_operation_filter_adds_match_clause(self):
        captured_body: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured_body.append(json.loads(request.content))
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_spans(since=_SINCE, operation="GET /users")
        filters = captured_body[0]["query"]["bool"]["filter"]
        match_filter = next((f for f in filters if "match" in f), None)
        assert match_filter is not None
        assert match_filter["match"]["name"] == "GET /users"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_attribute_filter_adds_term_clauses(self):
        captured_body: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured_body.append(json.loads(request.content))
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_spans(since=_SINCE, attributes={"http.method": "POST"})
        filters = captured_body[0]["query"]["bool"]["filter"]
        term_filter = next(
            (f for f in filters if "term" in f and "attributes.http.method" in f["term"]),
            None,
        )
        assert term_filter is not None
        assert term_filter["term"]["attributes.http.method"] == "POST"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_service_filter_adds_terms_clause(self):
        captured_body: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured_body.append(json.loads(request.content))
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.search_spans(since=_SINCE, services=["api", "worker"])
        filters = captured_body[0]["query"]["bool"]["filter"]
        terms_filter = next((f for f in filters if "terms" in f), None)
        assert terms_filter is not None
        assert "api" in terms_filter["terms"]["resource.attributes.service.name"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="Bad Request")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.search_spans(since=_SINCE)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_span_without_timestamp_uses_now(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "traceId": "t1",
                                "spanId": "s1",
                                "name": "op",
                                "resource": {"attributes": {"service.name": "svc"}},
                                "attributes": {},
                            }
                        }
                    ]
                }
            })

        adapter = _make_adapter(httpx.MockTransport(handler))
        spans = await adapter.search_spans(since=_SINCE)
        assert len(spans) == 1
        assert spans[0].trace_id == "t1"
        await adapter.close()


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_ERROR_HIT = {
    "_id": "span-err",
    "_source": {
        "@timestamp": "2024-01-01T12:30:00Z",
        "traceId": "abc123",
        "spanId": "span1",
        "name": "GET /users",
        "durationNano": 5_000_000,
        "status": {"code": "STATUS_CODE_ERROR", "message": "connection refused"},
        "attributes": {
            "exception.type": "ConnectionError",
            "exception.message": "connection refused",
        },
        "resource": {"attributes": {"service.name": "api"}},
    },
}

_ROOT_HIT = {
    "_id": "root",
    "_source": {
        "@timestamp": "2024-01-01T12:00:00Z",
        "traceId": "abc111def222abc1",
        "spanId": "root-span",
        "name": "root-op",
        "durationNano": 10_000_000,
        "status": {"code": "STATUS_CODE_OK"},
        "attributes": {},
        "resource": {"attributes": {"service.name": "svc"}},
    },
}

_CHILD_HIT = {
    "_id": "child",
    "_source": {
        "@timestamp": "2024-01-01T12:00:00.001Z",
        "traceId": "abc111def222abc1",
        "spanId": "child-span",
        "parentSpanId": "root-span",
        "name": "child-op",
        "durationNano": 2_000_000,
        "status": {"code": "STATUS_CODE_ERROR"},
        "attributes": {},
        "resource": {"attributes": {"service.name": "svc"}},
    },
}

_LOG_HIT = {
    "_id": "log-1",
    "_source": {
        "@timestamp": "2024-01-01T12:05:00Z",
        "traceId": "abc123",
        "spanId": "span1",
        "body": "database connection pool exhausted",
        "severityText": "ERROR",
        "attributes": {"component": "db"},
        "resource": {"attributes": {"service.name": "api"}},
    },
}


class TestBuildTraceTree:
    """Unit tests for the _build_trace_tree() helper."""

    def test_single_span_becomes_root(self):
        tree = _build_trace_tree("t1", [_ROOT_HIT])
        assert tree.trace_id == "t1"
        assert tree.root_span.span_id == "root-span"
        assert tree.root_span.children == ()

    def test_parent_child_hierarchy(self):
        tree = _build_trace_tree("trace1", [_ROOT_HIT, _CHILD_HIT])
        assert tree.root_span.span_id == "root-span"
        assert len(tree.root_span.children) == 1
        assert tree.root_span.children[0].span_id == "child-span"

    def test_error_spans_collected(self):
        tree = _build_trace_tree("trace1", [_ROOT_HIT, _CHILD_HIT])
        error_ids = {s.span_id for s in tree.error_spans}
        assert "child-span" in error_ids

    def test_no_valid_spans_raises(self):
        with pytest.raises(ValueError, match="No valid spans"):
            _build_trace_tree("t1", [{"_source": {}}])

    def test_cycle_detection_does_not_raise(self):
        # Build a pair of spans that reference each other as parents
        cyclic_a = {
            "_source": {
                "traceId": "t1",
                "spanId": "span-a",
                "parentSpanId": "span-b",
                "name": "op-a",
                "durationNano": 1_000_000,
                "status": {},
                "attributes": {},
                "resource": {"attributes": {"service.name": "svc"}},
            }
        }
        cyclic_b = {
            "_source": {
                "traceId": "t1",
                "spanId": "span-b",
                "parentSpanId": "span-a",
                "name": "op-b",
                "durationNano": 1_000_000,
                "status": {},
                "attributes": {},
                "resource": {"attributes": {"service.name": "svc"}},
            }
        }
        # Should not raise; cycle is broken with a warning
        tree = _build_trace_tree("t1", [cyclic_a, cyclic_b])
        assert tree.root_span is not None

    def test_span_duration_converted_from_ns(self):
        tree = _build_trace_tree("trace1", [_ROOT_HIT])
        assert tree.root_span.duration_ms == pytest.approx(10.0)

    def test_error_status_sets_error_status_field(self):
        tree = _build_trace_tree("trace1", [_ROOT_HIT, _CHILD_HIT])
        child = tree.root_span.children[0]
        assert child.status == "error"


class TestGetRecentErrors:
    """Tests for ElasticsearchTelemetryAdapter.get_recent_errors()."""

    @pytest.mark.asyncio
    async def test_returns_error_events(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "otel-traces" in request.url.path
            return httpx.Response(200, json={"hits": {"hits": [_ERROR_HIT]}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        errors = await adapter.get_recent_errors(_SINCE)
        assert len(errors) == 1
        assert errors[0].error_type == "ConnectionError"
        assert errors[0].service == "api"
        assert errors[0].trace_id == "abc123"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        errors = await adapter.get_recent_errors(_SINCE)
        assert errors == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_service_filter_adds_terms_clause(self):
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.get_recent_errors(_SINCE, services=["api", "worker"])
        filters = captured[0]["query"]["bool"]["filter"]
        terms = next((f for f in filters if "terms" in f), None)
        assert terms is not None
        assert "api" in terms["terms"]["resource.attributes.service.name"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invalid_service_names_skipped(self):
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.get_recent_errors(_SINCE, services=["valid", "bad name!"])
        filters = captured[0]["query"]["bool"]["filter"]
        terms = next((f for f in filters if "terms" in f), None)
        assert terms is not None
        valid_names = terms["terms"]["resource.attributes.service.name"]
        assert "valid" in valid_names
        assert "bad name!" not in valid_names
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="Service Unavailable")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.get_recent_errors(_SINCE)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_span_without_exception_type_uses_name(self):
        hit = {
            "_source": {
                "@timestamp": "2024-01-01T12:00:00Z",
                "traceId": "t1",
                "spanId": "s1",
                "name": "MySpanName",
                "status": {"code": "STATUS_CODE_ERROR"},
                "attributes": {},
                "resource": {"attributes": {"service.name": "svc"}},
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"hits": {"hits": [hit]}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        errors = await adapter.get_recent_errors(_SINCE)
        assert len(errors) == 1
        assert errors[0].error_type == "MySpanName"
        await adapter.close()


class TestGetTrace:
    """Tests for ElasticsearchTelemetryAdapter.get_trace()."""

    @pytest.mark.asyncio
    async def test_returns_trace_tree(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "otel-traces" in request.url.path
            return httpx.Response(200, json={"hits": {"hits": [_ROOT_HIT, _CHILD_HIT]}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        tree = await adapter.get_trace("abc111def222abc1")
        assert tree.trace_id == "abc111def222abc1"
        assert tree.root_span.span_id == "root-span"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invalid_trace_id_raises_value_error(self):
        adapter = _make_adapter(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        with pytest.raises(ValueError, match="Invalid trace ID"):
            await adapter.get_trace("not-hex!")
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_hits_raises_value_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(ValueError, match="No spans found"):
            await adapter.get_trace("abc123def45600ff")
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.get_trace("abc123")
        await adapter.close()

    @pytest.mark.asyncio
    async def test_error_spans_populated(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"hits": {"hits": [_ROOT_HIT, _CHILD_HIT]}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        tree = await adapter.get_trace("abc111def222abc1")
        assert any(s.span_id == "child-span" for s in tree.error_spans)
        await adapter.close()


class TestGetTraces:
    """Tests for ElasticsearchTelemetryAdapter.get_traces()."""

    @pytest.mark.asyncio
    async def test_returns_trace_trees_for_valid_ids(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "hits": {
                    "total": {"value": 2, "relation": "eq"},
                    "hits": [_ROOT_HIT, _CHILD_HIT],
                }
            })

        adapter = _make_adapter(httpx.MockTransport(handler))
        traces, info = await adapter.get_traces(["abc111def222abc1"])
        assert len(traces) == 1
        assert traces[0].trace_id == "abc111def222abc1"
        assert info.total_requested == 1
        assert info.total_returned == 1
        assert not info.is_partial
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self):
        adapter = _make_adapter(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        traces, info = await adapter.get_traces([])
        assert traces == []
        assert info.total_requested == 0
        assert not info.is_partial
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invalid_trace_ids_skipped(self):
        adapter = _make_adapter(httpx.MockTransport(lambda r: httpx.Response(200, json={
            "hits": {"total": {"value": 0}, "hits": []}
        })))
        traces, info = await adapter.get_traces(["not-hex!", "also-bad!"])
        assert traces == []
        assert info.is_partial
        assert "No valid" in (info.reason or "")
        await adapter.close()

    @pytest.mark.asyncio
    async def test_missing_trace_counts_as_partial(self):
        def handler(request: httpx.Request) -> httpx.Response:
            # Return spans for "trace1" but not "trace2"
            return httpx.Response(200, json={
                "hits": {
                    "total": {"value": 2, "relation": "eq"},
                    "hits": [_ROOT_HIT, _CHILD_HIT],  # only trace1 spans
                }
            })

        adapter = _make_adapter(httpx.MockTransport(handler))
        traces, info = await adapter.get_traces(["abc111def222abc1", "abc999def000abc9"])
        assert len(traces) == 1
        assert info.is_partial
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="Service Unavailable")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.get_traces(["abc123"])
        await adapter.close()


class TestGetCorrelatedLogs:
    """Tests for ElasticsearchTelemetryAdapter.get_correlated_logs()."""

    @pytest.mark.asyncio
    async def test_returns_correlated_log_entries(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "otel-logs" in request.url.path
            return httpx.Response(200, json={"hits": {"hits": [_LOG_HIT]}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        logs = await adapter.get_correlated_logs(["abc123"])
        assert len(logs) == 1
        assert logs[0].body == "database connection pool exhausted"
        assert logs[0].trace_id == "abc123"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_trace_ids_returns_empty(self):
        adapter = _make_adapter(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        logs = await adapter.get_correlated_logs([])
        assert logs == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invalid_trace_ids_returns_empty(self):
        adapter = _make_adapter(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        logs = await adapter.get_correlated_logs(["not-hex!"])
        assert logs == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_trace_id_sent_in_query(self):
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            if "otel-logs" in request.url.path:
                captured.append(json.loads(request.content))
            return httpx.Response(200, json={"hits": {"hits": []}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.get_correlated_logs(["abc123"])
        assert captured
        filters = captured[0]["query"]["bool"]["filter"]
        terms = next((f for f in filters if "terms" in f), None)
        assert terms is not None
        assert "abc123" in terms["terms"]["traceId"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.get_correlated_logs(["abc123"])
        await adapter.close()


class TestGetEventsForSignature:
    """Tests for ElasticsearchTelemetryAdapter.get_events_for_signature()."""

    @pytest.mark.asyncio
    async def test_returns_events_matching_fingerprint(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"hits": {"hits": [_ERROR_HIT]}})

        # Use a fingerprinter that always returns "fp-abc"
        adapter = ElasticsearchTelemetryAdapter(
            es_url="http://es-test:9200",
            api_key="test-key",
            fingerprinter=lambda e: "fp-abc",
        )
        adapter.client = httpx.AsyncClient(
            base_url="http://es-test:9200",
            headers={"Content-Type": "application/json"},
            transport=httpx.MockTransport(handler),
        )
        events = await adapter.get_events_for_signature("fp-abc")
        assert len(events) == 1
        await adapter.close()

    @pytest.mark.asyncio
    async def test_non_matching_fingerprint_returns_empty(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"hits": {"hits": [_ERROR_HIT]}})

        adapter = ElasticsearchTelemetryAdapter(
            es_url="http://es-test:9200",
            api_key="test-key",
            fingerprinter=lambda e: "fp-abc",
        )
        adapter.client = httpx.AsyncClient(
            base_url="http://es-test:9200",
            headers={"Content-Type": "application/json"},
            transport=httpx.MockTransport(handler),
        )
        events = await adapter.get_events_for_signature("fp-other")
        assert events == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_limit_is_respected(self):
        hits = [_ERROR_HIT] * 10

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"hits": {"hits": hits}})

        adapter = ElasticsearchTelemetryAdapter(
            es_url="http://es-test:9200",
            api_key="test-key",
            fingerprinter=lambda e: "fp-abc",
        )
        adapter.client = httpx.AsyncClient(
            base_url="http://es-test:9200",
            headers={"Content-Type": "application/json"},
            transport=httpx.MockTransport(handler),
        )
        events = await adapter.get_events_for_signature("fp-abc", limit=3)
        assert len(events) == 3
        await adapter.close()
