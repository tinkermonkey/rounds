"""HTTP webhook receiver for external triggers.

Provides REST API endpoints for receiving errors and triggering
investigations from external systems.

This adapter implements a simple HTTP server that listens for webhook
requests and forwards them to the ManagementPort for processing.
"""

import logging
from typing import Any

from rounds.core.models import SignatureStatus
from rounds.core.ports import ManagementPort, PollPort

logger = logging.getLogger(__name__)


class WebhookReceiver:
    """HTTP webhook receiver for triggering investigations.

    Listens on an HTTP endpoint and forwards requests to PollPort
    and ManagementPort for processing.
    """

    def __init__(
        self,
        poll_port: PollPort,
        management_port: ManagementPort,
        host: str = "0.0.0.0",
        port: int = 8080,
    ):
        """Initialize the webhook receiver.

        Args:
            poll_port: PollPort implementation for poll cycles.
            management_port: ManagementPort implementation for management operations.
            host: Host to listen on (default 0.0.0.0).
            port: Port to listen on (default 8080).
        """
        self.poll_port = poll_port
        self.management_port = management_port
        self.host = host
        self.port = port

    async def handle_poll_trigger(self) -> dict[str, Any]:
        """Handle a request to trigger a poll cycle.

        Returns:
            Dictionary with poll result summary.

        Raises:
            Exception: If poll cycle fails.
        """
        result = await self.poll_port.execute_poll_cycle()
        logger.info(
            "Poll cycle triggered via webhook",
            extra={
                "errors_found": result.errors_found,
                "new_signatures": result.new_signatures,
            },
        )
        return {
            "status": "success",
            "operation": "poll",
            "result": {
                "errors_found": result.errors_found,
                "new_signatures": result.new_signatures,
                "updated_signatures": result.updated_signatures,
                "investigations_queued": result.investigations_queued,
                "timestamp": result.timestamp.isoformat(),
            },
        }

    async def handle_investigation_trigger(self) -> dict[str, Any]:
        """Handle a request to trigger an investigation cycle.

        Returns:
            Dictionary with investigation result summary.

        Raises:
            Exception: If investigation cycle fails.
        """
        result = await self.poll_port.execute_investigation_cycle()
        logger.info(
            "Investigation cycle triggered via webhook",
            extra={
                "diagnoses_count": len(result.diagnoses_produced),
                "investigations_failed": result.investigations_failed,
            },
        )
        return {
            "status": "success",
            "operation": "investigation",
            "result": {
                "diagnoses_count": len(result.diagnoses_produced),
                "investigations_attempted": result.investigations_attempted,
                "investigations_failed": result.investigations_failed,
            },
        }

    async def handle_mute_request(self, signature_id: str, reason: str | None = None) -> dict[str, Any]:
        """Handle a request to mute a signature.

        Args:
            signature_id: UUID of signature to mute.
            reason: Optional reason for muting.

        Returns:
            Dictionary with operation result.

        Raises:
            ValueError: If signature doesn't exist or invalid state transition.
        """
        await self.management_port.mute_signature(signature_id, reason)
        logger.info(
            "Signature muted via webhook",
            extra={"signature_id": signature_id, "reason": reason},
        )
        return {
            "status": "success",
            "operation": "mute",
            "signature_id": signature_id,
        }

    async def handle_resolve_request(
        self, signature_id: str, fix_applied: str | None = None
    ) -> dict[str, Any]:
        """Handle a request to resolve a signature.

        Args:
            signature_id: UUID of signature to resolve.
            fix_applied: Optional description of fix applied.

        Returns:
            Dictionary with operation result.

        Raises:
            ValueError: If signature doesn't exist or invalid state transition.
        """
        await self.management_port.resolve_signature(signature_id, fix_applied)
        logger.info(
            "Signature resolved via webhook",
            extra={"signature_id": signature_id, "fix_applied": fix_applied},
        )
        return {
            "status": "success",
            "operation": "resolve",
            "signature_id": signature_id,
        }

    async def handle_retriage_request(self, signature_id: str) -> dict[str, Any]:
        """Handle a request to retriage a signature.

        Args:
            signature_id: UUID of signature to retriage.

        Returns:
            Dictionary with operation result.

        Raises:
            ValueError: If signature doesn't exist.
        """
        await self.management_port.retriage_signature(signature_id)
        logger.info(
            "Signature retriaged via webhook",
            extra={"signature_id": signature_id},
        )
        return {
            "status": "success",
            "operation": "retriage",
            "signature_id": signature_id,
        }

    async def handle_reinvestigate_request(self, signature_id: str) -> dict[str, Any]:
        """Handle a request to reinvestigate a signature.

        Args:
            signature_id: UUID of signature to reinvestigate.

        Returns:
            Dictionary with operation result.

        Raises:
            ValueError: If signature doesn't exist.
            Exception: If diagnosis fails.
        """
        diagnosis = await self.management_port.reinvestigate(signature_id)
        logger.info(
            "Signature reinvestigated via webhook",
            extra={
                "signature_id": signature_id,
                "confidence": diagnosis.confidence,
                "cost_usd": diagnosis.cost_usd,
            },
        )
        return {
            "status": "success",
            "operation": "reinvestigate",
            "signature_id": signature_id,
            "diagnosis": {
                "root_cause": diagnosis.root_cause,
                "confidence": diagnosis.confidence,
                "cost_usd": diagnosis.cost_usd,
            },
        }

    async def handle_details_request(self, signature_id: str) -> dict[str, Any]:
        """Handle a request for signature details.

        Args:
            signature_id: UUID of signature.

        Returns:
            Dictionary with signature details.

        Raises:
            ValueError: If signature doesn't exist.
        """
        details = await self.management_port.get_signature_details(signature_id)
        logger.debug(
            "Signature details retrieved via webhook",
            extra={"signature_id": signature_id},
        )

        # Serialize SignatureDetails dataclass to dict for JSON response
        return {
            "status": "success",
            "operation": "get_details",
            "signature_id": signature_id,
            "data": {
                "signature": {
                    "id": details.signature.id,
                    "fingerprint": details.signature.fingerprint,
                    "error_type": details.signature.error_type,
                    "service": details.signature.service,
                    "message_template": details.signature.message_template,
                    "stack_hash": details.signature.stack_hash,
                    "first_seen": details.signature.first_seen.isoformat(),
                    "last_seen": details.signature.last_seen.isoformat(),
                    "occurrence_count": details.signature.occurrence_count,
                    "status": details.signature.status.value,
                    "tags": sorted(details.signature.tags),
                    "diagnosis": (
                        {
                            "root_cause": details.signature.diagnosis.root_cause,
                            "evidence": details.signature.diagnosis.evidence,
                            "suggested_fix": details.signature.diagnosis.suggested_fix,
                            "confidence": details.signature.diagnosis.confidence,
                            "diagnosed_at": details.signature.diagnosis.diagnosed_at.isoformat(),
                            "model": details.signature.diagnosis.model,
                            "cost_usd": details.signature.diagnosis.cost_usd,
                        }
                        if details.signature.diagnosis
                        else None
                    ),
                },
                "recent_events": [
                    {
                        "trace_id": event.trace_id,
                        "span_id": event.span_id,
                        "service": event.service,
                        "error_type": event.error_type,
                        "error_message": event.error_message,
                        "timestamp": event.timestamp.isoformat(),
                        "severity": event.severity.value,
                    }
                    for event in details.recent_events
                ],
                "related_signatures": [
                    {
                        "id": s.id,
                        "fingerprint": s.fingerprint,
                        "error_type": s.error_type,
                        "service": s.service,
                        "occurrence_count": s.occurrence_count,
                        "status": s.status.value,
                    }
                    for s in details.related_signatures
                ],
            },
        }

    async def handle_list_request(self, status: str | None = None) -> dict[str, Any]:
        """Handle a request to list signatures.

        Args:
            status: Optional status filter (new, investigating, diagnosed, resolved, muted).

        Returns:
            Dictionary with list of signatures.

        Raises:
            ValueError: If status filter is invalid.
        """
        # Convert string status to enum
        status_enum = None
        if status:
            status_enum = SignatureStatus(status.lower())

        signatures = await self.management_port.list_signatures(status_enum)
        logger.debug(
            "Signatures listed via webhook",
            extra={"count": len(signatures), "status_filter": status},
        )
        return {
            "status": "success",
            "operation": "list",
            "signatures": [
                {
                    "id": sig.id,
                    "fingerprint": sig.fingerprint,
                    "error_type": sig.error_type,
                    "service": sig.service,
                    "status": sig.status.value,
                    "occurrence_count": sig.occurrence_count,
                    "first_seen": sig.first_seen.isoformat(),
                    "last_seen": sig.last_seen.isoformat(),
                }
                for sig in signatures
            ],
        }
