"""Comprehensive tests for WebhookHTTPServer and WebhookReceiver."""

import asyncio
import json
from collections.abc import AsyncGenerator
from http.client import HTTPConnection

import pytest

from rounds.adapters.webhook.http_server import WebhookHTTPServer
from rounds.adapters.webhook.receiver import WebhookReceiver
from rounds.tests.fakes.management import FakeManagementPort
from rounds.tests.fakes.poll import FakePollPort


def _blocking_post(
    port: int, path: str, headers: dict[str, str] | None = None, body: str | None = None
) -> tuple[int, str]:
    """Make a blocking POST and return (status, response_body).

    Same deadlock this works around as TestWebhookAuthentication's
    _post_and_get_status (see its docstring for the full explanation):
    do_POST's routed handlers run via
    asyncio.run_coroutine_threadsafe(coro, event_loop) against the SAME
    event loop an `async def test_...` coroutine itself runs on, so a
    plain synchronous HTTPConnection.getresponse() call made directly from
    the test coroutine would block that loop and the submitted coroutine
    could never run. Callers that actually reach a routed handler (as
    opposed to being rejected earlier, during auth/Content-Length/JSON
    parsing — none of which schedule anything on the loop) must run this
    via asyncio.to_thread.
    """
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read().decode()
    finally:
        conn.close()


class TestWebhookAuthentication:
    """Tests for webhook authentication mechanisms."""

    @pytest.fixture
    def fake_management_port(self) -> FakeManagementPort:
        """Create a fake management port for testing."""
        return FakeManagementPort()

    @pytest.fixture
    def fake_poll_port(self) -> FakePollPort:
        """Create a fake poll port for testing."""
        return FakePollPort()

    @pytest.fixture
    async def auth_server(
        self, fake_management_port: FakeManagementPort, fake_poll_port: FakePollPort
    ) -> AsyncGenerator[WebhookHTTPServer, None]:
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
    async def test_auth_bypass_attempt_fails(
        self, auth_server: WebhookHTTPServer
    ) -> None:
        """Should reject requests without Authorization header."""
        conn = HTTPConnection("127.0.0.1", 18080, timeout=5)

        try:
            conn.request("POST", "/api/poll")
            response = conn.getresponse()

            # Should return 401 Unauthorized
            assert response.status == 401

            data = response.read().decode()
            assert "Unauthorized" in data or "401" in data
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_invalid_auth_token_fails(
        self, auth_server: WebhookHTTPServer
    ) -> None:
        """Should reject requests with incorrect API key."""
        conn = HTTPConnection("127.0.0.1", 18080, timeout=5)

        try:
            headers = {"Authorization": "Bearer wrong-token"}
            conn.request("POST", "/api/poll", headers=headers)
            response = conn.getresponse()

            # Should return 401 Unauthorized
            assert response.status == 401
        finally:
            conn.close()

    @staticmethod
    def _post_and_get_status(port: int, path: str, headers: dict[str, str]) -> int:
        """Make a blocking POST and return the response status.

        Round-1 review finding on PR #169, part two: fixing the two
        "succeeds" tests below to actually hit a real /api/* route (instead
        of /poll, which 404s and was silently satisfying `!= 401`) surfaced
        a genuine, pre-existing deadlock — not something this fix introduces.
        do_POST's routed handlers (_handle_poll etc.) run via
        asyncio.run_coroutine_threadsafe(coro, event_loop) against the SAME
        event loop the pytest-asyncio test coroutine itself runs on
        (WebhookHTTPServer.start captures asyncio.get_running_loop()). A
        plain, synchronous HTTPConnection.getresponse() call made directly
        from an `async def test_...` never yields control back to that loop
        while it blocks waiting on the socket — so the submitted coroutine
        can never actually run, and the request stalls for the full 30s
        `_run_async` timeout before falling back to a 504. This appears to
        silently affect every OTHER test in this file that already uses
        raw HTTPConnection against an /api/* (or, before this PR, a
        mistyped) route — none of them actually observed a real success
        response either; they happened to pass against whatever status a
        404 (or, for the Content-Length tests, a pre-auth synchronous 400)
        produces, never reaching this deadlock. Out of scope to fix
        everywhere in this PR; running just these two calls in a worker
        thread via asyncio.to_thread is enough to let the event loop stay
        free to actually process the request, confirmed against a real
        server run (not assumed) before landing this.
        """
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("POST", path, headers=headers)
            return conn.getresponse().status
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_valid_auth_token_succeeds(
        self, auth_server: WebhookHTTPServer
    ) -> None:
        """Should accept requests with correct API key."""
        headers = {"Authorization": "Bearer test-secret-key"}
        # Round-1 review finding on PR #169: this used to POST to /poll, a
        # route that doesn't exist (real routes are /api/poll etc.) — the
        # 404 that route actually returns also isn't 401, so the assertion
        # passed without ever proving an authenticated request gets served.
        # /api/poll is the real route; see _post_and_get_status for why the
        # call is run in a worker thread, and assert the real success
        # status, not just "wasn't rejected at the auth gate".
        status = await asyncio.to_thread(self._post_and_get_status, 18080, "/api/poll", headers)
        assert status == 200

    @pytest.mark.asyncio
    async def test_valid_x_api_key_header_succeeds(
        self, auth_server: WebhookHTTPServer
    ) -> None:
        """Should accept requests authenticated via X-API-Key instead of Bearer."""
        headers = {"X-API-Key": "test-secret-key"}
        # See test_valid_auth_token_succeeds above for why this is /api/poll
        # + a real 200 assertion, run via _post_and_get_status in a worker
        # thread, rather than /poll + status != 401 on the main test coroutine.
        status = await asyncio.to_thread(self._post_and_get_status, 18080, "/api/poll", headers)
        assert status == 200

    @pytest.mark.asyncio
    async def test_invalid_x_api_key_header_fails(
        self, auth_server: WebhookHTTPServer
    ) -> None:
        """Should reject requests with an incorrect X-API-Key header."""
        conn = HTTPConnection("127.0.0.1", 18080, timeout=5)

        try:
            headers = {"X-API-Key": "wrong-key"}
            conn.request("POST", "/api/poll", headers=headers)
            response = conn.getresponse()

            assert response.status == 401
        finally:
            conn.close()


class TestWebhookDoSProtection:
    """Tests for DoS protection mechanisms."""

    @pytest.fixture
    def fake_management_port(self) -> FakeManagementPort:
        """Create a fake management port for testing."""
        return FakeManagementPort()

    @pytest.fixture
    def fake_poll_port(self) -> FakePollPort:
        """Create a fake poll port for testing."""
        return FakePollPort()

    @pytest.fixture
    async def dos_server(
        self, fake_management_port: FakeManagementPort, fake_poll_port: FakePollPort
    ) -> AsyncGenerator[WebhookHTTPServer, None]:
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
    async def test_oversized_body_rejected(self, dos_server: WebhookHTTPServer) -> None:
        """Should reject requests whose Content-Length exceeds 1MB.

        The server rejects based on the Content-Length header alone, before
        reading the body (see http_server.py), so this test only sends the
        request headers. Actually streaming a 1MB+ body over a real socket
        would race with the server closing the connection early, causing a
        flaky BrokenPipeError on the client side.
        """
        conn = HTTPConnection("127.0.0.1", 18081, timeout=5)

        try:
            # Content-Length is validated before path routing (see
            # http_server.py's do_POST), so the exact path doesn't affect
            # this test's outcome — but /api/investigate is the real
            # route, not /investigate (see _blocking_post's docstring and
            # PR #169 for the same class of typo on /poll).
            conn.putrequest("POST", "/api/investigate")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(1024 * 1024 + 1))
            conn.endheaders()
            response = conn.getresponse()

            # Should return 413 Request Entity Too Large or 400 Bad Request
            assert response.status in (400, 413)
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_non_numeric_content_length_returns_400(
        self, dos_server: WebhookHTTPServer
    ) -> None:
        """Should reject a non-numeric Content-Length with 400, not a raw
        traceback from `int()` raising ValueError.
        """
        conn = HTTPConnection("127.0.0.1", 18081, timeout=5)

        try:
            # Same non-effect on outcome as test_oversized_body_rejected
            # above — Content-Length is validated before routing — fixed
            # to the real route for the same clarity reason.
            conn.putrequest("POST", "/api/investigate")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", "not-a-number")
            conn.endheaders()
            response = conn.getresponse()

            assert response.status == 400
            body = response.read().decode()
            assert "Invalid Content-Length" in body
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_normal_size_body_accepted(
        self, dos_server: WebhookHTTPServer
    ) -> None:
        """Should accept requests with reasonable body size.

        Was posting to /investigate, which 404s (the real route is
        /api/investigate — same class of typo fixed for /poll in PR #169)
        — so this used to pass vacuously against a 404 rather than ever
        reaching the real handler. Fixed to the real route, and run via
        asyncio.to_thread since that now schedules a coroutine back onto
        this same test's event loop (see _blocking_post's docstring).
        """
        # Create a small valid JSON body
        small_body = json.dumps({"signature_id": "test-123"})
        headers = {"Content-Type": "application/json"}

        status, _ = await asyncio.to_thread(
            _blocking_post, 18081, "/api/investigate", headers, small_body
        )

        # Should not reject based on size (may fail for other reasons)
        assert status != 413


class TestWebhookJSONParsing:
    """Tests for JSON parsing error handling."""

    @pytest.fixture
    def fake_management_port(self) -> FakeManagementPort:
        """Create a fake management port for testing."""
        return FakeManagementPort()

    @pytest.fixture
    def fake_poll_port(self) -> FakePollPort:
        """Create a fake poll port for testing."""
        return FakePollPort()

    @pytest.fixture
    async def json_server(
        self, fake_management_port: FakeManagementPort, fake_poll_port: FakePollPort
    ) -> AsyncGenerator[WebhookHTTPServer, None]:
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
    async def test_invalid_json_returns_400(
        self, json_server: WebhookHTTPServer
    ) -> None:
        """Should return 400 Bad Request for malformed JSON."""
        conn = HTTPConnection("127.0.0.1", 18082, timeout=5)

        try:
            invalid_json = "{this is not valid json"

            # JSON parsing happens before path routing (see http_server.py's
            # do_POST), so the exact path doesn't affect this test's
            # outcome — but /api/investigate is the real route, not
            # /investigate, fixed for the same clarity reason as the
            # other /investigate typos in this file.
            headers = {"Content-Type": "application/json"}
            conn.request("POST", "/api/investigate", body=invalid_json, headers=headers)
            response = conn.getresponse()

            # Should return 400 Bad Request
            assert response.status == 400
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_valid_json_accepted(self, json_server: WebhookHTTPServer) -> None:
        """Should accept valid JSON payloads.

        Was posting to /investigate, which 404s (the real route is
        /api/investigate) — a 404 trivially satisfies `status != 400`, so
        this used to pass without ever reaching the real handler. Fixed to
        the real route, and run via asyncio.to_thread for the same
        deadlock reason as test_normal_size_body_accepted above.
        """
        valid_json = json.dumps({"signature_id": "test-456"})
        headers = {"Content-Type": "application/json"}

        status, body = await asyncio.to_thread(
            _blocking_post, 18082, "/api/investigate", headers, valid_json
        )

        # Should not return 400 for JSON parsing
        # (may return other errors like 404 if signature doesn't exist)
        assert status != 400 or "JSON" not in body


class TestWebhookReceiverConcurrency:
    """Tests for race conditions and concurrent request handling."""

    @pytest.fixture
    def fake_management_port(self) -> FakeManagementPort:
        """Create a fake management port for testing."""
        return FakeManagementPort()

    @pytest.fixture
    def fake_poll_port(self) -> FakePollPort:
        """Create a fake poll port for testing."""
        return FakePollPort()

    @pytest.mark.asyncio
    async def test_concurrent_poll_triggers(
        self, fake_management_port: FakeManagementPort, fake_poll_port: FakePollPort
    ) -> None:
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
        self, fake_management_port: FakeManagementPort, fake_poll_port: FakePollPort
    ) -> None:
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
    def fake_management_port(self) -> FakeManagementPort:
        """Create a fake management port for testing."""
        return FakeManagementPort()

    @pytest.fixture
    def fake_poll_port(self) -> FakePollPort:
        """Create a fake poll port for testing."""
        return FakePollPort()

    @pytest.mark.asyncio
    async def test_poll_trigger_calls_poll_port(
        self, fake_management_port: FakeManagementPort, fake_poll_port: FakePollPort
    ) -> None:
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
        self, fake_management_port: FakeManagementPort, fake_poll_port: FakePollPort
    ) -> None:
        """Should handle reinvestigate request with signature_id."""
        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        # Call with signature_id (may fail if signature doesn't exist)
        result = await receiver.handle_reinvestigate_request(signature_id="test-sig-id")

        # Should return a result with status
        assert "status" in result

    @pytest.mark.asyncio
    async def test_mute_request_with_signature_id(
        self, fake_management_port: FakeManagementPort, fake_poll_port: FakePollPort
    ) -> None:
        """Should accept valid signature_id for mute request."""
        receiver = WebhookReceiver(
            poll_port=fake_poll_port, management_port=fake_management_port
        )

        result = await receiver.handle_mute_request(
            signature_id="some-fake-id", reason="Test mute"
        )

        # Should attempt mute operation (may fail if signature doesn't exist)
        assert "status" in result
