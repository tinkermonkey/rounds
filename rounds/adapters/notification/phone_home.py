"""Phone-home notification adapter.

Implements NotificationPort by POSTing self-contained alert messages to an
external phone-home endpoint for newly diagnosed high-severity signatures.
Gated by severity, per-signature cooldown, and mute status to avoid alert
fatigue on a channel intended for a single, non-threading SMS conversation.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from rounds.core.models import Diagnosis, Severity, Signature, SignatureStatus
from rounds.core.ports import NotificationPort

logger = logging.getLogger(__name__)


class PhoneHomeNotificationAdapter(NotificationPort):
    """POSTs a self-contained alert to a phone-home endpoint for qualifying signatures.

    A signature qualifies for an alert only when all of the following hold:
    - It is not muted.
    - Its highest observed severity is in the configured severity gate.
    - It has not been alerted within the configured cooldown window
      (tracked via Signature.last_alerted_at).

    Any one of these failing suppresses the alert without raising. On a
    successful POST, report() returns the alert timestamp; recording it onto
    the signature and persisting it is the calling domain service's
    responsibility (see NotificationPort.report()), so the cooldown holds
    across poll cycles and process restarts without this adapter touching
    the signature store.
    """

    def __init__(
        self,
        endpoint_url: str,
        auth_token: str,
        severity_gate: frozenset[Severity] = frozenset({Severity.ERROR, Severity.FATAL}),
        cooldown_hours: int = 24,
        client: httpx.AsyncClient | None = None,
    ):
        """Initialize the phone-home notification adapter.

        Args:
            endpoint_url: URL of the phone-home endpoint that receives alerts.
            auth_token: Bearer credential for authenticating to the endpoint.
                May be empty for endpoints that don't require authentication.
            severity_gate: Severities that qualify for an alert. Empty means
                no severity ever qualifies (phone-home effectively disabled).
            cooldown_hours: Minimum hours between alerts for the same signature.
            client: Optional pre-configured httpx.AsyncClient (for testing).
        """
        if cooldown_hours <= 0:
            raise ValueError(f"cooldown_hours must be positive, got {cooldown_hours}")
        self.endpoint_url = endpoint_url
        self.auth_token = auth_token
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

    async def report(
        self, signature: Signature, diagnosis: Diagnosis, *, immediate: bool = False
    ) -> datetime | None:
        """POST a self-contained alert for the signature, if it qualifies.

        Phone-home has no batching concept, so `immediate` has no effect —
        qualification is still governed solely by mute status, severity
        gate, and cooldown.

        Returns:
            The timestamp the alert was sent, for the caller to record as
            the signature's cooldown checkpoint. `None` if suppressed by the
            severity gate, cooldown, or mute status.

        Raises:
            Exception: If the phone-home endpoint is unreachable or returns
                an error response. Suppression due to severity gate, cooldown,
                or mute status is not an error and does not raise.
        """
        if not self._qualifies_for_alert(signature):
            return None

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

        logger.info(
            "Sent phone-home alert",
            extra={"signature_id": signature.id, "severity": signature.max_severity.value},
        )
        return datetime.now(UTC)

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
