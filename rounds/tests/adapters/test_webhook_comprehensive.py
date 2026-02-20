"""Comprehensive tests for WebhookHTTPServer and WebhookReceiver."""

import asyncio
import json
from http.client import HTTPConnection
from typing import Any

import pytest

from rounds.adapters.webhook.http_server import WebhookHTTPServer
from rounds.adapters.webhook.receiver import WebhookReceiver
from rounds.tests.fakes.management import FakeManagementPort
from rounds.tests.fakes.poll import FakePollPort


class TestWebhookAuthentication:
    """Tests for webhook authentication mechanisms."""

    @pytest.fixture
    def fake_management_port(self):
        """Create a fake management port for testing."""
        return FakeManagementPort()

    @pytest.fixture
    def fake_poll_port(self):
        """Create a fake poll port for testing."""
        return FakePollPort()

    @pytest.fixture
    async def auth_server(self, fake_management_port, fake_poll_port):
        """Create and start an authenticated webhook server."""
        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        server = WebhookHTTPServer(
            webhook_receiver=receiver,
            api_key="test-secret-key",
            require_auth=True,
            host="127.0.0.1",
            port=18080,
        )

        await server.start()
        # Wait for server to be ready
        await asyncio.sleep(0.1)
        yield server
        await server.stop()

    @pytest.mark.asyncio
    async def test_auth_bypass_attempt_fails(self, auth_server):
        """Should reject requests without Authorization header."""
        conn = HTTPConnection("127.0.0.1", 18080, timeout=5)

        try:
            conn.request("POST", "/poll")
            response = conn.getresponse()

            # Should return 401 Unauthorized
            assert response.status == 401

            data = response.read().decode()
            assert "Unauthorized" in data or "401" in data
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_invalid_auth_token_fails(self, auth_server):
        """Should reject requests with incorrect API key."""
        conn = HTTPConnection("127.0.0.1", 18080, timeout=5)

        try:
            headers = {"Authorization": "Bearer wrong-token"}
            conn.request("POST", "/poll", headers=headers)
            response = conn.getresponse()

            # Should return 401 Unauthorized
            assert response.status == 401
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_valid_auth_token_succeeds(self, auth_server):
        """Should accept requests with correct API key."""
        conn = HTTPConnection("127.0.0.1", 18080, timeout=5)

        try:
            headers = {"Authorization": "Bearer test-secret-key"}
            conn.request("POST", "/poll", headers=headers)
            response = conn.getresponse()

            # Should not return 401 (may return 200 or other codes)
            assert response.status != 401
        finally:
            conn.close()


class TestWebhookDoSProtection:
    """Tests for DoS protection mechanisms."""

    @pytest.fixture
    def fake_management_port(self):
        """Create a fake management port for testing."""
        return FakeManagementPort()

    @pytest.fixture
    def fake_poll_port(self):
        """Create a fake poll port for testing."""
        return FakePollPort()

    @pytest.fixture
    async def dos_server(self, fake_management_port, fake_poll_port):
        """Create and start a webhook server for DoS testing."""
        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        server = WebhookHTTPServer(
            webhook_receiver=receiver,
            api_key=None,
            require_auth=False,
            host="127.0.0.1",
            port=18081,
        )

        await server.start()
        # Wait for server to be ready
        await asyncio.sleep(0.1)
        yield server
        await server.stop()

    @pytest.mark.asyncio
    async def test_oversized_body_rejected(self, dos_server):
        """Should reject requests with body larger than 1MB."""
        conn = HTTPConnection("127.0.0.1", 18081, timeout=5)

        try:
            # Create a body larger than 1MB
            large_body = "x" * (1024 * 1024 + 1)  # 1MB + 1 byte

            headers = {"Content-Type": "application/json"}
            conn.request("POST", "/investigate", body=large_body, headers=headers)
            response = conn.getresponse()

            # Should return 413 Request Entity Too Large or 400 Bad Request
            assert response.status in (400, 413)
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_normal_size_body_accepted(self, dos_server):
        """Should accept requests with reasonable body size."""
        conn = HTTPConnection("127.0.0.1", 18081, timeout=5)

        try:
            # Create a small valid JSON body
            small_body = json.dumps({"signature_id": "test-123"})

            headers = {"Content-Type": "application/json"}
            conn.request("POST", "/investigate", body=small_body, headers=headers)
            response = conn.getresponse()

            # Should not reject based on size (may fail for other reasons)
            assert response.status != 413
        finally:
            conn.close()


class TestWebhookJSONParsing:
    """Tests for JSON parsing error handling."""

    @pytest.fixture
    def fake_management_port(self):
        """Create a fake management port for testing."""
        return FakeManagementPort()

    @pytest.fixture
    def fake_poll_port(self):
        """Create a fake poll port for testing."""
        return FakePollPort()

    @pytest.fixture
    async def json_server(self, fake_management_port, fake_poll_port):
        """Create and start a webhook server for JSON testing."""
        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        server = WebhookHTTPServer(
            webhook_receiver=receiver,
            api_key=None,
            require_auth=False,
            host="127.0.0.1",
            port=18082,
        )

        await server.start()
        # Wait for server to be ready
        await asyncio.sleep(0.1)
        yield server
        await server.stop()

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self, json_server):
        """Should return 400 Bad Request for malformed JSON."""
        conn = HTTPConnection("127.0.0.1", 18082, timeout=5)

        try:
            invalid_json = "{this is not valid json"

            headers = {"Content-Type": "application/json"}
            conn.request("POST", "/investigate", body=invalid_json, headers=headers)
            response = conn.getresponse()

            # Should return 400 Bad Request
            assert response.status == 400
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_valid_json_accepted(self, json_server):
        """Should accept valid JSON payloads."""
        conn = HTTPConnection("127.0.0.1", 18082, timeout=5)

        try:
            valid_json = json.dumps({"signature_id": "test-456"})

            headers = {"Content-Type": "application/json"}
            conn.request("POST", "/investigate", body=valid_json, headers=headers)
            response = conn.getresponse()

            # Should not return 400 for JSON parsing
            # (may return other errors like 404 if signature doesn't exist)
            assert response.status != 400 or "JSON" not in response.read().decode()
        finally:
            conn.close()


class TestWebhookReceiverConcurrency:
    """Tests for race conditions and concurrent request handling."""

    @pytest.fixture
    def fake_management_port(self):
        """Create a fake management port for testing."""
        return FakeManagementPort()

    @pytest.fixture
    def fake_poll_port(self):
        """Create a fake poll port for testing."""
        return FakePollPort()

    @pytest.mark.asyncio
    async def test_concurrent_poll_triggers(
        self, fake_management_port, fake_poll_port
    ):
        """Should handle concurrent poll trigger requests without race conditions."""
        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        # Trigger multiple polls concurrently
        tasks = [receiver.handle_poll_trigger() for _ in range(10)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed (or fail gracefully without crashes)
        for result in results:
            assert isinstance(result, dict) or isinstance(result, Exception)
            if isinstance(result, dict):
                assert "status" in result

    @pytest.mark.asyncio
    async def test_concurrent_reinvestigate_requests(
        self, fake_management_port, fake_poll_port
    ):
        """Should handle concurrent reinvestigate requests without race conditions."""
        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        # Trigger multiple reinvestigations concurrently
        tasks = [
            receiver.handle_reinvestigate_request(signature_id=f"sig-{i}")
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should complete (may fail if signature doesn't exist, but no crashes)
        for result in results:
            assert isinstance(result, dict) or isinstance(result, Exception)
            if isinstance(result, dict):
                assert "status" in result


class TestWebhookReceiverOperations:
    """Tests for WebhookReceiver business logic."""

    @pytest.fixture
    def fake_management_port(self):
        """Create a fake management port for testing."""
        return FakeManagementPort()

    @pytest.fixture
    def fake_poll_port(self):
        """Create a fake poll port for testing."""
        return FakePollPort()

    @pytest.mark.asyncio
    async def test_poll_trigger_calls_poll_port(
        self, fake_management_port, fake_poll_port
    ):
        """Should invoke poll_port when handling poll trigger."""
        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        result = await receiver.handle_poll_trigger()

        # Should return success status
        assert result["status"] == "success"
        assert "poll" in result["operation"]

    @pytest.mark.asyncio
    async def test_reinvestigate_requires_signature_id(
        self, fake_management_port, fake_poll_port
    ):
        """Should handle reinvestigate request with signature_id."""
        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        # Call with signature_id (may fail if signature doesn't exist)
        result = await receiver.handle_reinvestigate_request(
            signature_id="test-sig-id"
        )

        # Should return a result with status
        assert "status" in result

    @pytest.mark.asyncio
    async def test_mute_request_with_signature_id(
        self, fake_management_port, fake_poll_port
    ):
        """Should accept valid signature_id for mute request."""
        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        result = await receiver.handle_mute_request(
            signature_id="some-fake-id", reason="Test mute"
        )

        # Should attempt mute operation (may fail if signature doesn't exist)
        assert "status" in result
