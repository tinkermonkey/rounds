"""Tests for WebhookHTTPServer adapter."""

import pytest

from rounds.adapters.webhook.http_server import WebhookHTTPServer
from rounds.tests.fakes.management import FakeManagementPort
from rounds.tests.fakes.poll import FakePollPort


class TestWebhookHTTPServerInitialization:
    """Tests for WebhookHTTPServer initialization and configuration validation."""

    @pytest.fixture
    def fake_management_port(self):
        """Create a fake management port for testing."""
        return FakeManagementPort()

    @pytest.fixture
    def fake_poll_port(self):
        """Create a fake poll port for testing."""
        return FakePollPort()

    def test_require_auth_without_api_key_raises_value_error(
        self, fake_management_port, fake_poll_port
    ):
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
        self, fake_management_port, fake_poll_port
    ):
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
        self, fake_management_port, fake_poll_port
    ):
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
        self, fake_management_port, fake_poll_port
    ):
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
