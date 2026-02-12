"""Management service: implements ManagementPort for human-initiated operations.

This is a core service that orchestrates management operations (mute, resolve,
retriage, get details, list, reinvestigate) by interacting with the signature store
and invoking investigations. It ensures all state changes are properly logged
and auditable.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from rounds.core.models import Diagnosis, InvestigationContext, Signature, SignatureStatus
from rounds.core.ports import (
    DiagnosisPort,
    ManagementPort,
    SignatureStorePort,
    TelemetryPort,
)

logger = logging.getLogger(__name__)


class ManagementService(ManagementPort):
    """Core implementation of ManagementPort.

    Coordinates management operations with the signature store, telemetry,
    and diagnosis engine.
    All operations are logged for audit trails.
    """

    def __init__(
        self,
        store: SignatureStorePort,
        telemetry: TelemetryPort,
        diagnosis_engine: DiagnosisPort,
    ):
        """Initialize the management service.

        Args:
            store: SignatureStorePort implementation for persistence.
            telemetry: TelemetryPort implementation for retrieving errors.
            diagnosis_engine: DiagnosisPort implementation for diagnosis.
        """
        self.store = store
        self.telemetry = telemetry
        self.diagnosis_engine = diagnosis_engine

    async def mute_signature(
        self, signature_id: str, reason: str | None = None
    ) -> None:
        """Mute a signature to suppress further notifications.

        Args:
            signature_id: UUID of the signature to mute.
            reason: Optional reason for muting (logged for audit).

        Raises:
            ValueError: If signature doesn't exist.
            Exception: If database error occurs.
        """
        signature = await self.store.get_by_id(signature_id)
        if signature is None:
            raise ValueError(f"Signature {signature_id} not found")

        # Update signature status
        signature.status = SignatureStatus.MUTED

        await self.store.update(signature)

        logger.info(
            f"Signature {signature_id} muted",
            extra={
                "signature_id": signature_id,
                "reason": reason,
                "fingerprint": signature.fingerprint,
            },
        )

    async def resolve_signature(
        self, signature_id: str, fix_applied: str | None = None
    ) -> None:
        """Mark a signature as resolved.

        Args:
            signature_id: UUID of the signature.
            fix_applied: Optional description of the fix that was applied.

        Raises:
            ValueError: If signature doesn't exist.
            Exception: If database error occurs.
        """
        signature = await self.store.get_by_id(signature_id)
        if signature is None:
            raise ValueError(f"Signature {signature_id} not found")

        # Update signature status
        signature.status = SignatureStatus.RESOLVED

        await self.store.update(signature)

        logger.info(
            f"Signature {signature_id} resolved",
            extra={
                "signature_id": signature_id,
                "fix_applied": fix_applied,
                "fingerprint": signature.fingerprint,
            },
        )

    async def retriage_signature(self, signature_id: str) -> None:
        """Reset a signature to NEW status for re-investigation.

        Used when initial diagnosis was incorrect or needs updating.

        Args:
            signature_id: UUID of the signature.

        Raises:
            ValueError: If signature doesn't exist.
            Exception: If database error occurs.
        """
        signature = await self.store.get_by_id(signature_id)
        if signature is None:
            raise ValueError(f"Signature {signature_id} not found")

        # Reset status and clear diagnosis
        signature.status = SignatureStatus.NEW
        signature.diagnosis = None

        await self.store.update(signature)

        logger.info(
            f"Signature {signature_id} retriaged",
            extra={"signature_id": signature_id, "fingerprint": signature.fingerprint},
        )

    async def get_signature_details(self, signature_id: str) -> dict[str, Any]:
        """Retrieve detailed information about a signature.

        Returns all signature fields plus derived information:
        - signature fields (id, fingerprint, error_type, service, etc.)
        - occurrence_count and time window (first_seen to last_seen)
        - diagnosis (if available) with confidence
        - recent error events (for context)
        - related signatures (similar errors)

        Args:
            signature_id: UUID of the signature.

        Returns:
            Dictionary with all signature details.

        Raises:
            ValueError: If signature doesn't exist.
            Exception: If database error occurs.
        """
        signature = await self.store.get_by_id(signature_id)
        if signature is None:
            raise ValueError(f"Signature {signature_id} not found")

        # Get recent error events for this signature
        recent_events = await self.telemetry.get_events_for_signature(
            signature.fingerprint, limit=5
        )

        # Get related/similar signatures
        related = await self.store.get_similar(signature, limit=5)

        # Build details dictionary
        details: dict[str, Any] = {
            # Basic fields
            "id": signature.id,
            "fingerprint": signature.fingerprint,
            "error_type": signature.error_type,
            "service": signature.service,
            "message_template": signature.message_template,
            "stack_hash": signature.stack_hash,
            # Timestamps and counts
            "first_seen": signature.first_seen.isoformat(),
            "last_seen": signature.last_seen.isoformat(),
            "occurrence_count": signature.occurrence_count,
            # Status
            "status": signature.status.value,
            "tags": sorted(signature.tags),
            # Recent error events
            "recent_error_events": [
                {
                    "trace_id": event.trace_id,
                    "span_id": event.span_id,
                    "timestamp": event.timestamp.isoformat(),
                    "error_message": event.error_message,
                    "severity": event.severity.value,
                }
                for event in recent_events
            ],
            # Diagnosis information
            "diagnosis": None,
            # Related signatures
            "related_signatures": [
                {
                    "id": s.id,
                    "error_type": s.error_type,
                    "service": s.service,
                    "occurrence_count": s.occurrence_count,
                    "status": s.status.value,
                }
                for s in related
            ],
        }

        # Add diagnosis if available
        if signature.diagnosis is not None:
            details["diagnosis"] = {
                "root_cause": signature.diagnosis.root_cause,
                "evidence": signature.diagnosis.evidence,
                "suggested_fix": signature.diagnosis.suggested_fix,
                "confidence": signature.diagnosis.confidence.value,
                "diagnosed_at": signature.diagnosis.diagnosed_at.isoformat(),
                "model": signature.diagnosis.model,
                "cost_usd": signature.diagnosis.cost_usd,
            }

        logger.debug(
            f"Retrieved signature details for {signature_id}",
            extra={"signature_id": signature_id},
        )

        return details

    async def list_signatures(
        self, status: SignatureStatus | None = None
    ) -> list[Signature]:
        """List all signatures, optionally filtered by status.

        Args:
            status: Filter to signatures with this status. If None, return all.

        Returns:
            List of Signature objects matching the criteria.

        Raises:
            Exception: If database error occurs.
        """
        # Get all signatures from the store
        all_signatures = await self.store.get_pending_investigation()

        # If no status filter, return all
        if status is None:
            logger.debug("Listed all signatures")
            return all_signatures

        # Filter by status
        filtered = [sig for sig in all_signatures if sig.status == status]

        logger.debug(
            f"Listed signatures with status filter",
            extra={
                "status": status.value,
                "count": len(filtered),
            },
        )

        return filtered

    async def reinvestigate(self, signature_id: str) -> Diagnosis:
        """Trigger immediate investigation/re-investigation of a signature.

        Resets the signature to NEW status, retrieves recent events,
        performs diagnosis, and returns the diagnosis result.

        Used when a user wants an immediate re-diagnosis of a signature.

        Args:
            signature_id: UUID of the signature.

        Returns:
            Diagnosis object from the investigation.

        Raises:
            ValueError: If signature doesn't exist.
            Exception: If investigation or diagnosis fails.
        """
        signature = await self.store.get_by_id(signature_id)
        if signature is None:
            raise ValueError(f"Signature {signature_id} not found")

        # Reset to NEW status
        signature.status = SignatureStatus.NEW
        signature.diagnosis = None
        await self.store.update(signature)

        logger.info(
            f"Started reinvestigation for signature {signature_id}",
            extra={"signature_id": signature_id, "fingerprint": signature.fingerprint},
        )

        # Retrieve recent events for this signature
        recent_events = await self.telemetry.get_events_for_signature(
            signature.fingerprint, limit=10
        )

        # Get similar signatures for context
        similar = await self.store.get_similar(signature, limit=5)

        # Build investigation context with available data
        # Trace data and logs could be fetched from telemetry for higher-quality diagnosis,
        # but this adds latency. Current approach provides fast reinvestigation with recent events.
        context = InvestigationContext(
            signature=signature,
            recent_events=tuple(recent_events),
            trace_data=(),  # Future: Fetch via telemetry.get_trace() for improved diagnosis
            related_logs=(),  # Future: Fetch via telemetry.get_correlated_logs() for improved diagnosis
            codebase_path=".",
            historical_context=tuple(similar),
        )

        # Invoke diagnosis
        diagnosis = await self.diagnosis_engine.diagnose(context)

        # Update signature with diagnosis and mark as DIAGNOSED
        signature.diagnosis = diagnosis
        signature.status = SignatureStatus.DIAGNOSED
        await self.store.update(signature)

        logger.info(
            f"Completed reinvestigation for signature {signature_id}",
            extra={
                "signature_id": signature_id,
                "confidence": diagnosis.confidence.value,
                "cost_usd": diagnosis.cost_usd,
            },
        )

        return diagnosis
