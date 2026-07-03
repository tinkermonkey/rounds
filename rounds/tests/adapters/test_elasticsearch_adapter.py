"""Tests for ElasticsearchTelemetryAdapter: list_services, search_logs, search_spans."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from rounds.adapters.telemetry.elasticsearch import ElasticsearchTelemetryAdapter


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
