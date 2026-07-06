"""Tests for SigNozUsageQueryAdapter."""

import httpx
import pytest

from rounds.adapters.telemetry.signoz_usage import SigNozUsageQueryAdapter

VALID_TRACE_ID = "a" * 32


def _make_adapter(transport: httpx.MockTransport) -> SigNozUsageQueryAdapter:
    """Create a SigNozUsageQueryAdapter with a mock transport."""
    adapter = SigNozUsageQueryAdapter(api_url="http://signoz-test:3301", api_key="test-key")
    adapter.client = httpx.AsyncClient(
        base_url="http://signoz-test:3301",
        headers=adapter._get_headers(),
        transport=transport,
    )
    return adapter


class TestQueryDiagnosisCost:
    """Tests for SigNozUsageQueryAdapter.query_diagnosis_cost()."""

    @pytest.mark.asyncio
    async def test_returns_cost_from_matching_log_entry(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v3/query_range"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "result": [
                            {
                                "list": [
                                    {
                                        "timestamp": "2026-07-06T12:00:00Z",
                                        "data": {
                                            "trace_id": VALID_TRACE_ID,
                                            "attributes_number": {"cost_usd": 0.42},
                                        },
                                    }
                                ]
                            }
                        ]
                    }
                },
            )

        adapter = _make_adapter(httpx.MockTransport(handler))
        cost = await adapter.query_diagnosis_cost(VALID_TRACE_ID)
        assert cost == pytest.approx(0.42)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_sums_cost_across_multiple_entries(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "result": [
                            {
                                "list": [
                                    {
                                        "timestamp": "2026-07-06T12:00:00Z",
                                        "data": {"attributes_number": {"cost_usd": 0.30}},
                                    },
                                    {
                                        "timestamp": "2026-07-06T12:00:05Z",
                                        "data": {"attributes_number": {"cost_usd": 0.15}},
                                    },
                                ]
                            }
                        ]
                    }
                },
            )

        adapter = _make_adapter(httpx.MockTransport(handler))
        cost = await adapter.query_diagnosis_cost(VALID_TRACE_ID)
        assert cost == pytest.approx(0.45)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_no_matching_entries_returns_zero(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"result": [{"list": []}]}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        cost = await adapter.query_diagnosis_cost(VALID_TRACE_ID)
        assert cost == 0.0
        await adapter.close()

    @pytest.mark.asyncio
    async def test_entries_without_cost_attribute_are_ignored(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "result": [
                            {
                                "list": [
                                    {
                                        "timestamp": "2026-07-06T12:00:00Z",
                                        "data": {"body": "unrelated log entry"},
                                    }
                                ]
                            }
                        ]
                    }
                },
            )

        adapter = _make_adapter(httpx.MockTransport(handler))
        cost = await adapter.query_diagnosis_cost(VALID_TRACE_ID)
        assert cost == 0.0
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invalid_trace_id_returns_zero_without_querying(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("Should not query SigNoz for an invalid trace ID")

        adapter = _make_adapter(httpx.MockTransport(handler))
        cost = await adapter.query_diagnosis_cost("not-a-valid-trace-id")
        assert cost == 0.0
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_returns_zero(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "internal error"})

        adapter = _make_adapter(httpx.MockTransport(handler))
        cost = await adapter.query_diagnosis_cost(VALID_TRACE_ID)
        assert cost == 0.0
        await adapter.close()

    @pytest.mark.asyncio
    async def test_connect_error_returns_zero(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        adapter = _make_adapter(httpx.MockTransport(handler))
        cost = await adapter.query_diagnosis_cost(VALID_TRACE_ID)
        assert cost == 0.0
        await adapter.close()


class TestClientReuse:
    """Tests for SigNozUsageQueryAdapter's optional shared httpx.AsyncClient."""

    @pytest.mark.asyncio
    async def test_default_construction_owns_its_client(self) -> None:
        adapter = SigNozUsageQueryAdapter(api_url="http://signoz-test:3301")
        assert adapter._owns_client is True
        await adapter.client.aclose()

    @pytest.mark.asyncio
    async def test_injected_client_is_reused(self) -> None:
        shared_client = httpx.AsyncClient(base_url="http://signoz-test:3301")
        adapter = SigNozUsageQueryAdapter(
            api_url="http://signoz-test:3301", client=shared_client
        )
        assert adapter.client is shared_client
        assert adapter._owns_client is False
        await shared_client.aclose()

    @pytest.mark.asyncio
    async def test_close_does_not_close_injected_client(self) -> None:
        shared_client = httpx.AsyncClient(base_url="http://signoz-test:3301")
        adapter = SigNozUsageQueryAdapter(
            api_url="http://signoz-test:3301", client=shared_client
        )
        await adapter.close()
        assert shared_client.is_closed is False
        await shared_client.aclose()

    @pytest.mark.asyncio
    async def test_close_closes_owned_client(self) -> None:
        adapter = SigNozUsageQueryAdapter(api_url="http://signoz-test:3301")
        await adapter.close()
        assert adapter.client.is_closed is True
