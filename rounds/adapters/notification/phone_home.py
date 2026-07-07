"""Phone-home notification adapter.

Implements NotificationPort by POSTing self-contained alert messages to an
external phone-home endpoint for newly diagnosed high-severity signatures.
Gated by severity, per-signature cooldown, and mute status to avoid alert
fatigue on a channel intended for a single, non-threading SMS conversation.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from rounds.core.models import Diagnosis, Severity, Signature, SignatureStatus
from rounds.core.ports import NotificationPort, SignatureStorePort

logger = logging.getLogger(__name__)

# The alert has already been sent by the time last_alerted_at is persisted,
# so a store failure here risks duplicate alerts within the cooldown window
# on the next poll cycle. Retry a few times before giving up.
_COOLDOWN_PERSIST_MAX_ATTEMPTS = 3
_COOLDOWN_PERSIST_RETRY_DELAY_SECONDS = 1.0


class PhoneHomeNotificationAdapter(NotificationPort):
    """POSTs a self-contained alert to a phone-home endpoint for qualifying signatures.

    A signature qualifies for an alert only when all of the following hold:
    - It is not muted.
    - Its highest observed severity is in the configured severity gate.
    - It has not been alerted within the configured cooldown window
      (tracked via Signature.last_alerted_at).

    Any one of these failing suppresses the alert without raising. On a
    successful POST, Signature.last_alerted_at is updated and persisted via
    the store so the cooldown holds across poll cycles and process restarts.
    """

    def __init__(
        self,
        endpoint_url: str,
        auth_token: str,
        store: SignatureStorePort,
        severity_gate: frozenset[Severity] = frozenset({Severity.ERROR, Severity.FATAL}),
        cooldown_hours: int = 24,
        client: httpx.AsyncClient | None = None,
    ):
        """Initialize the phone-home notification adapter.

        Args:
            endpoint_url: URL of the phone-home endpoint that receives alerts.
            auth_token: Bearer credential for authenticating to the endpoint.
                May be empty for endpoints that don't require authentication.
            store: Signature store, used to persist last_alerted_at after a
                successful alert.
            severity_gate: Severities that qualify for an alert. Empty means
                no severity ever qualifies (phone-home effectively disabled).
            cooldown_hours: Minimum hours between alerts for the same signature.
            client: Optional pre-configured httpx.AsyncClient (for testing).
        """
        if cooldown_hours <= 0:
            raise ValueError(f"cooldown_hours must be positive, got {cooldown_hours}")
        self.endpoint_url = endpoint_url
        self.auth_token = auth_token
        self.store = store
        self.severity_gate = severity_gate
        self.cooldown_hours = cooldown_hours
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the async HTTP client.

        Returns:
            httpx.AsyncClient configured with bearer authentication, if a
            token is configured.
        """
        if self._client is None:
            headers = {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}
            self._client = httpx.AsyncClient(headers=headers)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client, if this adapter owns it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def report(self, signature: Signature, diagnosis: Diagnosis) -> None:
        """POST a self-contained alert for the signature, if it qualifies.

        Raises:
            Exception: If the phone-home endpoint is unreachable or returns
                an error response. Suppression due to severity gate, cooldown,
                or mute status is not an error and does not raise.
        """
        if not self._qualifies_for_alert(signature):
            return

        message = self._format_alert_message(signature, diagnosis)

        try:
            client = await self._get_client()
            response = await client.post(
                self.endpoint_url,
                json={
                    "message": message,
                    "signature_id": signature.id,
                    "severity": signature.max_severity.value,
                    "service": signature.service,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Phone-home alert failed: {e.response.status_code}",
                extra={"signature_id": signature.id, "response": e.response.text},
                exc_info=True,
            )
            raise
        except httpx.RequestError as e:
            logger.error(
                f"Phone-home alert failed: {e}",
                extra={"signature_id": signature.id},
                exc_info=True,
            )
            raise

        signature.record_alert(datetime.now(UTC))
        await self._persist_cooldown(signature)

        logger.info(
            "Sent phone-home alert",
            extra={"signature_id": signature.id, "severity": signature.max_severity.value},
        )

    async def _persist_cooldown(self, signature: Signature) -> None:
        """Persist the just-sent alert's cooldown timestamp, retrying on failure.

        The phone-home POST has already succeeded by the time this runs, so a
        lost update here means the next poll cycle sees last_alerted_at as
        unset and re-sends a duplicate alert within the cooldown window.
        Retries give transient store errors a chance to clear before that
        happens; a final failure is logged loudly but not re-raised, since a
        duplicate alert is the acceptable worst case and this is not a
        failure of the alert itself — the alert was already delivered.
        """
        for attempt in range(1, _COOLDOWN_PERSIST_MAX_ATTEMPTS + 1):
            try:
                await self.store.update(signature)
                return
            except Exception as e:
                if attempt == _COOLDOWN_PERSIST_MAX_ATTEMPTS:
                    logger.error(
                        "Failed to persist phone-home cooldown after alert was sent; "
                        "duplicate alerts may occur within the cooldown window",
                        extra={"signature_id": signature.id, "attempts": attempt},
                        exc_info=True,
                    )
                    return
                logger.warning(
                    f"Failed to persist phone-home cooldown (attempt {attempt}/"
                    f"{_COOLDOWN_PERSIST_MAX_ATTEMPTS}): {e}",
                    extra={"signature_id": signature.id},
                )
                await asyncio.sleep(_COOLDOWN_PERSIST_RETRY_DELAY_SECONDS)

    def _qualifies_for_alert(self, signature: Signature) -> bool:
        """Apply the mute, severity-gate, and cooldown checks (FR21-23)."""
        if signature.status == SignatureStatus.MUTED:
            return False
        if signature.max_severity not in self.severity_gate:
            return False
        if signature.last_alerted_at is not None:
            cooldown = timedelta(hours=self.cooldown_hours)
            if datetime.now(UTC) - signature.last_alerted_at < cooldown:
                return False
        return True

    @staticmethod
    def _format_alert_message(signature: Signature, diagnosis: Diagnosis) -> str:
        """Format a self-contained alert message (FR20).

        Delivered via a single non-threading SMS conversation, so the message
        must stand alone: no reference to "the above" or prior messages.
        """
        lines = [
            f"[Rounds] {signature.max_severity.value} in {signature.service}: {signature.error_type}",
            f"{signature.message_template}",
            (
                f"Seen {signature.occurrence_count}x "
                f"(first {signature.first_seen.isoformat()}, last {signature.last_seen.isoformat()})"
            ),
            f"Root cause ({diagnosis.confidence} confidence): {diagnosis.root_cause}",
            f"Suggested fix: {diagnosis.suggested_fix}",
            f"Signature: {signature.fingerprint[:16]}",
        ]
        return "\n".join(lines)

    async def report_summary(self, stats: dict[str, Any]) -> None:
        """No-op: phone-home is reserved for individual high-severity alerts, not periodic digests."""
        return

    async def report_alert(self, alert: dict[str, Any]) -> None:
        """No-op: phone-home is reserved for diagnosed-signature alerts, not operational events."""
        return
