"""Tests for WebhookHTTPServer adapter."""

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from http.client import HTTPConnection

import pytest

from rounds.adapters.webhook.http_server import WebhookHTTPServer
from rounds.core.models import HealthSnapshot
from rounds.tests.fakes.health import FakeFailingHealthCheckPort, FakeHealthCheckPort
from rounds.tests.fakes.management import FakeManagementPort
from rounds.tests.fakes.metrics import (
    FakeDashboardMetricsPort,
    FakeFailingDashboardMetricsPort,
)
from rounds.tests.fakes.poll import FakePollPort


class TestWebhookHTTPServerInitialization:
    """Tests for WebhookHTTPServer initialization and configuration validation."""

    @pytest.fixture
    def fake_management_port(self) -> FakeManagementPort:
        """Create a fake management port for testing."""
        return FakeManagementPort()

    @pytest.fixture
    def fake_poll_port(self) -> FakePollPort:
        """Create a fake poll port for testing."""
        return FakePollPort()

    def test_require_auth_without_api_key_raises_value_error(
        self, fake_management_port: FakeManagementPort, fake_poll_port: FakePollPort
    ) -> None:
        """Should raise ValueError when require_auth=True but api_key is None."""
        # Import here to avoid circular dependency
        from rounds.adapters.webhook.receiver import WebhookReceiver

        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        # Should raise ValueError during initialization
        with pytest.raises(ValueError) as exc_info:
            WebhookHTTPServer(
                webhook_receiver=receiver,
                api_key=None,
                require_auth=True,
            )

        # Verify error message is clear
        assert "require_auth=True" in str(exc_info.value)
        assert "no API key provided" in str(exc_info.value)

    def test_require_auth_with_empty_api_key_raises_value_error(
        self, fake_management_port: FakeManagementPort, fake_poll_port: FakePollPort
    ) -> None:
        """Should raise ValueError when require_auth=True but api_key is empty string."""
        from rounds.adapters.webhook.receiver import WebhookReceiver

        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        # Should raise ValueError during initialization
        with pytest.raises(ValueError) as exc_info:
            WebhookHTTPServer(
                webhook_receiver=receiver,
                api_key="",
                require_auth=True,
            )

        assert "require_auth=True" in str(exc_info.value)
        assert "no API key provided" in str(exc_info.value)

    def test_require_auth_with_api_key_succeeds(
        self, fake_management_port: FakeManagementPort, fake_poll_port: FakePollPort
    ) -> None:
        """Should initialize successfully when require_auth=True and api_key is provided."""
        from rounds.adapters.webhook.receiver import WebhookReceiver

        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        # Should not raise
        server = WebhookHTTPServer(
            webhook_receiver=receiver,
            api_key="test-api-key-123",
            require_auth=True,
        )

        assert server.api_key == "test-api-key-123"
        assert server.require_auth is True

    def test_no_auth_required_without_api_key_succeeds(
        self, fake_management_port: FakeManagementPort, fake_poll_port: FakePollPort
    ) -> None:
        """Should initialize successfully when require_auth=False and no api_key."""
        from rounds.adapters.webhook.receiver import WebhookReceiver

        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        # Should not raise
        server = WebhookHTTPServer(
            webhook_receiver=receiver,
            api_key=None,
            require_auth=False,
        )

        assert server.api_key is None
        assert server.require_auth is False


class TestWebhookHealthEndpoint:
    """Tests for the /health endpoint reflecting daemon poll-cycle health."""

    @pytest.fixture
    async def server_without_health_provider(
        self,
    ) -> AsyncGenerator[WebhookHTTPServer, None]:
        """Server with no health_provider (e.g. webhook mode, no poll loop)."""
        server = WebhookHTTPServer(
            webhook_receiver=None,
            host="127.0.0.1",
            port=18081,
        )
        await server.start()
        await asyncio.sleep(0.1)
        yield server
        await server.stop()

    @pytest.mark.asyncio
    async def test_health_without_provider_is_always_healthy(
        self, server_without_health_provider: WebhookHTTPServer
    ) -> None:
        """/health reports healthy when no health_provider is wired (degenerate case)."""
        conn = HTTPConnection("127.0.0.1", 18081, timeout=5)
        try:
            conn.request("GET", "/health")
            response = conn.getresponse()
            assert response.status == 200
            body = json.loads(response.read().decode())
            assert body["status"] == "healthy"
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_health_get_no_receiver_required(
        self, server_without_health_provider: WebhookHTTPServer
    ) -> None:
        """/health works without a webhook_receiver (daemon mode has none)."""
        conn = HTTPConnection("127.0.0.1", 18081, timeout=5)
        try:
            conn.request("POST", "/health")
            response = conn.getresponse()
            # Health check is public and doesn't need a webhook receiver,
            # unlike other POST endpoints which return 500 without one.
            assert response.status == 200
        finally:
            conn.close()

    async def _start_server_with_snapshot(
        self, snapshot: HealthSnapshot, port: int
    ) -> WebhookHTTPServer:
        server = WebhookHTTPServer(
            webhook_receiver=None,
            host="127.0.0.1",
            port=port,
            health_provider=FakeHealthCheckPort(snapshot),
        )
        await server.start()
        await asyncio.sleep(0.1)
        return server

    @pytest.mark.asyncio
    async def test_health_healthy_snapshot_returns_200(self) -> None:
        """/health returns 200 and status=healthy when the daemon is healthy."""
        last_poll = datetime.now(UTC)
        snapshot = HealthSnapshot(
            last_poll_completed_at=last_poll,
            consecutive_poll_failures=0,
            poll_failure_threshold=5,
        )
        server = await self._start_server_with_snapshot(snapshot, port=18082)
        try:
            conn = HTTPConnection("127.0.0.1", 18082, timeout=5)
            try:
                conn.request("GET", "/health")
                response = conn.getresponse()
                assert response.status == 200
                body = json.loads(response.read().decode())
                assert body["status"] == "healthy"
                assert body["consecutive_poll_failures"] == 0
                assert body["poll_failure_threshold"] == 5
                assert body["last_poll_completed_at"] == last_poll.isoformat()
            finally:
                conn.close()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_health_unhealthy_snapshot_returns_503(self) -> None:
        """/health returns 503 and status=unhealthy once poll failures hit the threshold."""
        snapshot = HealthSnapshot(
            last_poll_completed_at=None,
            consecutive_poll_failures=5,
            poll_failure_threshold=5,
        )
        server = await self._start_server_with_snapshot(snapshot, port=18083)
        try:
            conn = HTTPConnection("127.0.0.1", 18083, timeout=5)
            try:
                conn.request("GET", "/health")
                response = conn.getresponse()
                assert response.status == 503
                body = json.loads(response.read().decode())
                assert body["status"] == "unhealthy"
                assert body["consecutive_poll_failures"] == 5
                assert body["last_poll_completed_at"] is None
            finally:
                conn.close()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_health_unaffected_by_telemetry_backend(self) -> None:
        """Health reflects only the poll-cycle circuit breaker state handed to it —
        it never queries a telemetry backend, so a telemetry outage alone can't
        flip it to unhealthy as long as polling itself keeps succeeding.
        """
        snapshot = HealthSnapshot(
            last_poll_completed_at=datetime.now(UTC),
            consecutive_poll_failures=0,
            poll_failure_threshold=5,
        )
        server = await self._start_server_with_snapshot(snapshot, port=18084)
        try:
            conn = HTTPConnection("127.0.0.1", 18084, timeout=5)
            try:
                conn.request("GET", "/health")
                response = conn.getresponse()
                assert response.status == 200
            finally:
                conn.close()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_health_provider_exception_returns_503_not_connection_reset(
        self,
    ) -> None:
        """A health_provider that raises must still get a proper HTTP response -
        not a dropped connection that monitoring systems misread as "down"
        instead of "reporting unhealthy".
        """
        server = WebhookHTTPServer(
            webhook_receiver=None,
            host="127.0.0.1",
            port=18085,
            health_provider=FakeFailingHealthCheckPort(),
        )
        await server.start()
        await asyncio.sleep(0.1)
        try:
            conn = HTTPConnection("127.0.0.1", 18085, timeout=5)
            try:
                conn.request("GET", "/health")
                response = conn.getresponse()
                assert response.status == 503
                body = json.loads(response.read().decode())
                assert body["status"] == "unhealthy"
            finally:
                conn.close()
        finally:
            await server.stop()


class TestWebhookDashboardCostsEndpoint:
    """Tests for the /dashboard/costs endpoint (FR-3.7 cost accounting)."""

    @pytest.mark.asyncio
    async def test_dashboard_costs_requires_auth(self) -> None:
        """/dashboard/costs rejects unauthenticated requests, unlike /health."""
        server = WebhookHTTPServer(
            webhook_receiver=None,
            host="127.0.0.1",
            port=18086,
            api_key="test-secret-key",
            require_auth=True,
            metrics_provider=FakeDashboardMetricsPort(
                daily_cost_usd=12.5, cost_by_service={"svc-a": 12.5}
            ),
        )
        await server.start()
        await asyncio.sleep(0.1)
        try:
            conn = HTTPConnection("127.0.0.1", 18086, timeout=5)
            try:
                conn.request("GET", "/dashboard/costs")
                response = conn.getresponse()
                assert response.status == 401
                response.read()
            finally:
                conn.close()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_dashboard_costs_without_provider_returns_empty_breakdown(
        self,
    ) -> None:
        """Without a metrics_provider (e.g. webhook mode), reports an empty
        breakdown rather than failing.
        """
        server = WebhookHTTPServer(
            webhook_receiver=None,
            host="127.0.0.1",
            port=18087,
        )
        await server.start()
        await asyncio.sleep(0.1)
        try:
            conn = HTTPConnection("127.0.0.1", 18087, timeout=5)
            try:
                conn.request("GET", "/dashboard/costs")
                response = conn.getresponse()
                assert response.status == 200
                body = json.loads(response.read().decode())
                assert body == {"daily_cost_usd": 0.0, "cost_by_service": {}}
            finally:
                conn.close()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_dashboard_costs_returns_live_data(self) -> None:
        """Returns the live per-service cost breakdown from the metrics_provider."""
        server = WebhookHTTPServer(
            webhook_receiver=None,
            host="127.0.0.1",
            port=18088,
            metrics_provider=FakeDashboardMetricsPort(
                daily_cost_usd=42.75,
                cost_by_service={"api": 30.0, "worker": 12.75},
            ),
        )
        await server.start()
        await asyncio.sleep(0.1)
        try:
            conn = HTTPConnection("127.0.0.1", 18088, timeout=5)
            try:
                conn.request("GET", "/dashboard/costs")
                response = conn.getresponse()
                assert response.status == 200
                body = json.loads(response.read().decode())
                assert body["daily_cost_usd"] == 42.75
                assert body["cost_by_service"] == {"api": 30.0, "worker": 12.75}
            finally:
                conn.close()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_dashboard_costs_provider_exception_returns_503(self) -> None:
        """A metrics_provider that raises must still get a proper HTTP response -
        not a dropped connection with a leaked traceback.
        """
        server = WebhookHTTPServer(
            webhook_receiver=None,
            host="127.0.0.1",
            port=18089,
            metrics_provider=FakeFailingDashboardMetricsPort(),
        )
        await server.start()
        await asyncio.sleep(0.1)
        try:
            conn = HTTPConnection("127.0.0.1", 18089, timeout=5)
            try:
                conn.request("GET", "/dashboard/costs")
                response = conn.getresponse()
                assert response.status == 503
                body = json.loads(response.read().decode())
                assert body["error"] == "dashboard query failed"
            finally:
                conn.close()
        finally:
            await server.stop()
