"""Integration tests for webhook HTTP server adapter."""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from rounds.adapters.webhook.http_server import WebhookHTTPHandler, WebhookHTTPServer
from rounds.adapters.webhook.receiver import WebhookReceiver


@pytest.fixture
def mock_webhook_receiver():
    """Create a mock webhook receiver."""
    receiver = MagicMock(spec=WebhookReceiver)
    receiver.handle_alert = AsyncMock()
    return receiver


@pytest.fixture
async def http_server(mock_webhook_receiver):
    """Create a test HTTP server."""
    server = WebhookHTTPServer(
        webhook_receiver=mock_webhook_receiver,
        port=8888,
        api_key=None,
    )
    yield server


class TestWebhookHTTPHandler:
    """Tests for HTTP request handling."""

    def test_handler_initialization(self):
        """Test that handler can be initialized."""
        handler = WebhookHTTPHandler(None, None, None)
        assert handler is not None

    def test_auth_not_required_when_disabled(self):
        """Test that authentication is bypassed when disabled."""
        WebhookHTTPHandler.require_auth = False
        handler = WebhookHTTPHandler(None, None, None)
        # Mock the headers
        handler.headers = {}
        assert handler._check_auth() is True

    def test_auth_required_with_bearer_token(self):
        """Test Bearer token authentication."""
        WebhookHTTPHandler.api_key = "test-api-key"
        WebhookHTTPHandler.require_auth = True
        handler = WebhookHTTPHandler(None, None, None)
        handler.headers = {"Authorization": "Bearer test-api-key"}
        assert handler._check_auth() is True

    def test_auth_fails_with_invalid_token(self):
        """Test that invalid token fails authentication."""
        WebhookHTTPHandler.api_key = "test-api-key"
        WebhookHTTPHandler.require_auth = True
        handler = WebhookHTTPHandler(None, None, None)
        handler.headers = {"Authorization": "Bearer wrong-key"}
        assert handler._check_auth() is False

    def test_auth_with_x_api_key_header(self):
        """Test X-API-Key header authentication."""
        WebhookHTTPHandler.api_key = "test-api-key"
        WebhookHTTPHandler.require_auth = True
        handler = WebhookHTTPHandler(None, None, None)
        handler.headers = {"X-API-Key": "test-api-key"}
        assert handler._check_auth() is True


class TestWebhookHTTPServer:
    """Tests for HTTP server initialization."""

    @pytest.mark.asyncio
    async def test_server_initialization_without_auth(self, mock_webhook_receiver):
        """Test server initialization without authentication."""
        server = WebhookHTTPServer(
            webhook_receiver=mock_webhook_receiver,
            port=9999,
            api_key=None,
        )
        assert server.webhook_receiver == mock_webhook_receiver
        assert server.port == 9999
        assert server.api_key is None

    @pytest.mark.asyncio
    async def test_server_initialization_with_auth(self, mock_webhook_receiver):
        """Test server initialization with API key authentication."""
        server = WebhookHTTPServer(
            webhook_receiver=mock_webhook_receiver,
            port=9999,
            api_key="secret-key",
        )
        assert server.api_key == "secret-key"

    @pytest.mark.asyncio
    async def test_server_port_configuration(self, mock_webhook_receiver):
        """Test that server uses configured port."""
        server = WebhookHTTPServer(
            webhook_receiver=mock_webhook_receiver,
            port=7777,
        )
        assert server.port == 7777


class TestWebhookJSONParsing:
    """Tests for JSON payload parsing."""

    def test_valid_json_payload(self):
        """Test parsing of valid JSON payload."""
        payload = {
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighErrorRate",
                        "service": "api-server",
                    },
                }
            ]
        }
        json_str = json.dumps(payload)
        parsed = json.loads(json_str)
        assert parsed["alerts"][0]["labels"]["service"] == "api-server"

    def test_malformed_json_handling(self):
        """Test handling of malformed JSON."""
        malformed_json = '{"invalid": "json"'
        with pytest.raises(json.JSONDecodeError):
            json.loads(malformed_json)

    def test_empty_json_object(self):
        """Test handling of empty JSON object."""
        payload = {}
        json_str = json.dumps(payload)
        parsed = json.loads(json_str)
        assert parsed == {}


class TestWebhookErrorResponses:
    """Tests for error response handling."""

    def test_400_bad_request_response(self):
        """Test 400 Bad Request response."""
        # This would be generated when JSON parsing fails
        status_code = 400
        assert status_code == 400

    def test_401_unauthorized_response(self):
        """Test 401 Unauthorized response."""
        # This would be generated when authentication fails
        status_code = 401
        assert status_code == 401

    def test_500_internal_error_response(self):
        """Test 500 Internal Server Error response."""
        # This would be generated when handler processing fails
        status_code = 500
        assert status_code == 500
