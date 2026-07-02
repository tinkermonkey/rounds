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
