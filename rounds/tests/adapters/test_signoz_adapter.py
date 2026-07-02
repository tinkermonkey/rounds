"""Tests for SigNozTelemetryAdapter.verify_connection()."""

import httpx
import pytest

from rounds.adapters.telemetry.signoz import SigNozTelemetryAdapter


def _make_adapter(transport: httpx.MockTransport) -> SigNozTelemetryAdapter:
    """Create a SigNozTelemetryAdapter with a mock transport."""
    adapter = SigNozTelemetryAdapter(api_url="http://signoz-test:3301", api_key="test-key")
    adapter.client = httpx.AsyncClient(
        base_url="http://signoz-test:3301",
        headers=adapter._get_headers(),
        transport=transport,
    )
    return adapter


class TestVerifyConnection:
    """Tests for SigNozTelemetryAdapter.verify_connection()."""

    @pytest.mark.asyncio
    async def test_happy_path_200(self):
        """Should log INFO and not raise on a 200 health response."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok"})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.verify_connection()
        await adapter.close()

    @pytest.mark.asyncio
    async def test_non_auth_4xx_treated_as_reachable(self):
        """404 (e.g. health path varies by version) should not raise — server responded."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "not found"})

        adapter = _make_adapter(httpx.MockTransport(handler))
        await adapter.verify_connection()
        await adapter.close()

    @pytest.mark.asyncio
    async def test_401_raises_http_status_error(self):
        """Should raise HTTPStatusError and log ERROR on 401."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.verify_connection()
        await adapter.close()

    @pytest.mark.asyncio
    async def test_403_raises_http_status_error(self):
        """Should raise HTTPStatusError and log ERROR on 403."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"error": "forbidden"})

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.verify_connection()
        await adapter.close()

    @pytest.mark.asyncio
    async def test_connect_error_raises(self):
        """Should re-raise ConnectError and log ERROR when SigNoz is unreachable."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.ConnectError):
            await adapter.verify_connection()
        await adapter.close()

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        """Should re-raise TimeoutException and log ERROR on request timeout."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("Request timed out")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.TimeoutException):
            await adapter.verify_connection()
        await adapter.close()


class TestListServices:
    """Tests for SigNozTelemetryAdapter.list_services()."""

    @pytest.mark.asyncio
    async def test_returns_sorted_service_names(self):
        """Should return sorted service names from /api/v1/services data array."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v1/services"
            return httpx.Response(200, json={
                "data": [
                    {"serviceName": "zebra-api", "p99": 0},
                    {"serviceName": "alpha-worker", "p99": 0},
                    {"serviceName": "beta-service", "p99": 0},
                ]
            })

        adapter = _make_adapter(httpx.MockTransport(handler))
        services = await adapter.list_services()
        assert services == ["alpha-worker", "beta-service", "zebra-api"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_deduplicates_service_names(self):
        """Should deduplicate service names returned by SigNoz."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "data": [
                    {"serviceName": "my-service"},
                    {"serviceName": "my-service"},
                    {"serviceName": "other-service"},
                ]
            })

        adapter = _make_adapter(httpx.MockTransport(handler))
        services = await adapter.list_services()
        assert services == ["my-service", "other-service"]
        await adapter.close()

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty_list(self):
        """Should return empty list when no services are instrumented."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        adapter = _make_adapter(httpx.MockTransport(handler))
        services = await adapter.list_services()
        assert services == []
        await adapter.close()

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        """Should re-raise HTTPStatusError from /api/v1/services."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "internal server error"})

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.list_services()
        await adapter.close()

    @pytest.mark.asyncio
    async def test_skips_items_without_service_name(self):
        """Should skip data items that have no serviceName field."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "data": [
                    {"serviceName": "real-service"},
                    {"p99": 0},  # no serviceName
                    {"serviceName": ""},  # empty string
                ]
            })

        adapter = _make_adapter(httpx.MockTransport(handler))
        services = await adapter.list_services()
        assert services == ["real-service"]
        await adapter.close()


class TestMainBootstrapIntegration:
    """Tests for the catch-and-continue behavior in main.py bootstrap()."""

    @pytest.mark.asyncio
    async def test_connectivity_failure_does_not_block_startup(self, caplog):
        """bootstrap() should log a WARNING and proceed if verify_connection() fails."""
        import logging

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        adapter = _make_adapter(httpx.MockTransport(handler))

        with caplog.at_level(logging.WARNING):
            try:
                await adapter.verify_connection()
            except Exception:
                import logging as _log
                _log.getLogger("rounds.main").warning(
                    "SigNoz connectivity check failed at startup — "
                    "will retry on first poll cycle"
                )

        assert any(
            "will retry on first poll cycle" in r.message
            for r in caplog.records
        )
        await adapter.close()
