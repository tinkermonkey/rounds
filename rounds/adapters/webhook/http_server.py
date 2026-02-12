"""HTTP server adapter for webhook receiver.

Provides a simple async HTTP server using Python's built-in http.server module
and asyncio for handling webhook requests.
"""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from rounds.adapters.webhook.receiver import WebhookReceiver

logger = logging.getLogger(__name__)

# Thread-safe executor for running async operations from sync handlers
_executor = ThreadPoolExecutor(max_workers=4)


class WebhookHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for webhook endpoints.

    Handles incoming HTTP requests and routes them to the webhook receiver.
    """

    # Class variable to hold the WebhookReceiver instance
    webhook_receiver: WebhookReceiver | None = None
    # Class variable to hold the event loop
    event_loop: asyncio.AbstractEventLoop | None = None

    def do_POST(self) -> None:
        """Handle POST requests.

        Routes to appropriate handler based on path.
        """
        if not self.webhook_receiver:
            self.send_error(500, "Webhook receiver not initialized")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        try:
            # Parse JSON body
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON body")
            return

        # Route based on path
        if self.path == "/api/poll":
            self._run_async(self._handle_poll())
        elif self.path == "/api/investigate":
            self._run_async(self._handle_investigate())
        elif self.path == "/api/mute":
            self._run_async(self._handle_mute(data))
        elif self.path == "/api/resolve":
            self._run_async(self._handle_resolve(data))
        elif self.path == "/api/retriage":
            self._run_async(self._handle_retriage(data))
        elif self.path == "/api/reinvestigate":
            self._run_async(self._handle_reinvestigate(data))
        elif self.path == "/api/details":
            self._run_async(self._handle_details(data))
        elif self.path == "/api/list":
            self._run_async(self._handle_list(data))
        elif self.path == "/health":
            self._send_response({"status": "healthy"})
        else:
            self.send_error(404, "Not found")

    def do_GET(self) -> None:
        """Handle GET requests.

        Supports health check via GET.
        """
        if self.path == "/health":
            self._send_response({"status": "healthy"})
        else:
            self.send_error(404, "Not found")

    def _run_async(self, coro: Any) -> None:
        """Run an async coroutine from a sync context.

        Uses the event loop set at class level to schedule the coroutine.
        """
        if not self.event_loop:
            self.send_error(500, "Event loop not available")
            return

        # Schedule coroutine on the event loop
        future = asyncio.run_coroutine_threadsafe(coro, self.event_loop)
        try:
            # Wait for result with timeout
            future.result(timeout=30)
        except Exception as e:
            logger.error(f"Error handling webhook request: {e}", exc_info=True)
            self.send_error(500, f"Internal server error: {str(e)}")

    async def _handle_poll(self) -> None:
        """Handle poll trigger request."""
        if not self.webhook_receiver:
            return
        result = await self.webhook_receiver.handle_poll_trigger()
        self._send_response(result)

    async def _handle_investigate(self) -> None:
        """Handle investigation trigger request."""
        if not self.webhook_receiver:
            return
        result = await self.webhook_receiver.handle_investigation_trigger()
        self._send_response(result)

    async def _handle_mute(self, data: dict[str, Any]) -> None:
        """Handle mute signature request."""
        if not self.webhook_receiver:
            return
        signature_id = data.get("signature_id")
        if not signature_id:
            self.send_error(400, "Missing signature_id")
            return
        reason = data.get("reason")
        result = await self.webhook_receiver.handle_mute_request(signature_id, reason)
        self._send_response(result)

    async def _handle_resolve(self, data: dict[str, Any]) -> None:
        """Handle resolve signature request."""
        if not self.webhook_receiver:
            return
        signature_id = data.get("signature_id")
        if not signature_id:
            self.send_error(400, "Missing signature_id")
            return
        fix_applied = data.get("fix_applied")
        result = await self.webhook_receiver.handle_resolve_request(
            signature_id, fix_applied
        )
        self._send_response(result)

    async def _handle_retriage(self, data: dict[str, Any]) -> None:
        """Handle retriage signature request."""
        if not self.webhook_receiver:
            return
        signature_id = data.get("signature_id")
        if not signature_id:
            self.send_error(400, "Missing signature_id")
            return
        result = await self.webhook_receiver.handle_retriage_request(signature_id)
        self._send_response(result)

    async def _handle_reinvestigate(self, data: dict[str, Any]) -> None:
        """Handle reinvestigate signature request."""
        if not self.webhook_receiver:
            return
        signature_id = data.get("signature_id")
        if not signature_id:
            self.send_error(400, "Missing signature_id")
            return
        result = await self.webhook_receiver.handle_reinvestigate_request(
            signature_id
        )
        self._send_response(result)

    async def _handle_details(self, data: dict[str, Any]) -> None:
        """Handle get details request."""
        if not self.webhook_receiver:
            return
        signature_id = data.get("signature_id")
        if not signature_id:
            self.send_error(400, "Missing signature_id")
            return
        result = await self.webhook_receiver.handle_details_request(signature_id)
        self._send_response(result)

    async def _handle_list(self, data: dict[str, Any]) -> None:
        """Handle list signatures request."""
        if not self.webhook_receiver:
            return
        status = data.get("status")
        result = await self.webhook_receiver.handle_list_request(status)
        self._send_response(result)

    def _send_response(self, data: dict[str, Any]) -> None:
        """Send JSON response."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format: str, *args: Any) -> None:
        """Log HTTP request."""
        logger.debug(f"HTTP {self.client_address[0]}: {format % args}")


class WebhookHTTPServer:
    """Webhook HTTP server adapter.

    Provides HTTP endpoints for webhook-based management operations.
    """

    def __init__(
        self,
        webhook_receiver: WebhookReceiver,
        host: str = "0.0.0.0",
        port: int = 8080,
    ):
        """Initialize the HTTP server.

        Args:
            webhook_receiver: WebhookReceiver instance to handle requests.
            host: Host to listen on (default 0.0.0.0).
            port: Port to listen on (default 8080).
        """
        self.webhook_receiver = webhook_receiver
        self.host = host
        self.port = port
        self.server: HTTPServer | None = None
        self._server_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the HTTP server."""
        logger.info(f"Starting webhook HTTP server on {self.host}:{self.port}")

        # Set the webhook receiver and event loop on the handler class
        WebhookHTTPHandler.webhook_receiver = self.webhook_receiver
        WebhookHTTPHandler.event_loop = asyncio.get_running_loop()

        # Create the HTTP server
        self.server = HTTPServer((self.host, self.port), WebhookHTTPHandler)

        # Run server in a separate thread to avoid blocking
        self._server_task = asyncio.create_task(self._run_server())
        logger.info("Webhook HTTP server started")

    async def _run_server(self) -> None:
        """Run the HTTP server loop."""
        if not self.server:
            return

        loop = asyncio.get_running_loop()
        try:
            while True:
                # Handle one request
                self.server.handle_request()
                # Yield control to other tasks
                await asyncio.sleep(0.01)
        except Exception as e:
            logger.error(f"Webhook HTTP server error: {e}", exc_info=True)

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self.server:
            self.server.server_close()
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
        logger.info("Webhook HTTP server stopped")
