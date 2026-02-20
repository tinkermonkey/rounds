"""Management service: implements ManagementPort for human-initiated operations.

This is a core service that orchestrates management operations (mute, resolve,
retriage, get details, list, reinvestigate) by interacting with the signature store
and invoking investigations. It ensures all state changes are properly logged
and auditable.
"""

import logging
from collections.abc import Sequence

from .models import (
    Diagnosis,
    InvestigationContext,
    Signature,
    SignatureDetails,
    SignatureStatus,
)
from .ports import (
    DiagnosisPort,
    ManagementPort,
    NotificationPort,
    SignatureStorePort,
    TelemetryPort,
)
from .triage import TriageEngine

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
        notification: NotificationPort,
        triage: TriageEngine,
        codebase_path: str,
    ):
        """Initialize the management service.

        Args:
            store: SignatureStorePort implementation for persistence.
            telemetry: TelemetryPort implementation for retrieving errors.
            diagnosis_engine: DiagnosisPort implementation for diagnosis.
            notification: NotificationPort implementation for reporting diagnoses.
            triage: TriageEngine for determining if notifications should be sent.
            codebase_path: Path to the codebase for diagnosis context.
        """
        self.store = store
        self.telemetry = telemetry
        self.diagnosis_engine = diagnosis_engine
        self.notification = notification
        self.triage = triage
        self.codebase_path = codebase_path

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

        # Update signature status using domain guard clause
        try:
            signature.mark_muted()
        except ValueError as e:
            raise ValueError(f"Cannot mute signature: {e}") from e

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

        # Update signature status using domain guard clause
        try:
            signature.mark_resolved()
        except ValueError as e:
            raise ValueError(f"Cannot resolve signature: {e}") from e

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

        # Reset signature for re-investigation from any status using domain methods
        signature.reset_to_new()
        signature.clear_diagnosis()

        await self.store.update(signature)

        logger.info(
            f"Signature {signature_id} retriaged",
            extra={"signature_id": signature_id, "fingerprint": signature.fingerprint},
        )

    async def get_signature_details(self, signature_id: str) -> SignatureDetails:
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
            SignatureDetails with signature, recent events, and related signatures.

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

        logger.debug(
            f"Retrieved signature details for {signature_id}",
            extra={"signature_id": signature_id},
        )

        return SignatureDetails(
            signature=signature,
            recent_events=tuple(recent_events),
            related_signatures=tuple(related),
        )

    async def list_signatures(
        self, status: SignatureStatus | None = None
    ) -> Sequence[Signature]:
        """List all signatures, optionally filtered by status.

        Args:
            status: Filter to signatures with this status. If None, return all.

        Returns:
            List of Signature objects matching the criteria.

        Raises:
            Exception: If database error occurs.
        """
        # Get signatures from the store, filtered by status if provided
        signatures = await self.store.get_all(status=status)

        logger.debug(
            "Listed signatures" + (f" with status={status.value}" if status else ""),
            extra={
                "count": len(signatures),
            },
        )

        return signatures

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

        # Preserve the original diagnosis in case diagnosis fails
        original_diagnosis = signature.diagnosis
        original_status = signature.status

        # Reset to NEW status using domain methods for management operations
        signature.reset_to_new()
        signature.clear_diagnosis()

        await self.store.update(signature)

        logger.info(
            f"Started reinvestigation for signature {signature_id}",
            extra={"signature_id": signature_id, "fingerprint": signature.fingerprint},
        )

        # Retrieve recent events for this signature
        recent_events = await self.telemetry.get_events_for_signature(
            signature.fingerprint, limit=10
        )

        # Fetch trace data and logs for higher-quality diagnosis
        trace_ids = [e.trace_id for e in recent_events]
        traces, _partial_info = await self.telemetry.get_traces(trace_ids)

        # Fetch related logs using trace IDs
        logs = await self.telemetry.get_correlated_logs(trace_ids, window_minutes=5)

        # Get similar signatures for context
        similar = await self.store.get_similar(signature, limit=5)

        # Build investigation context with complete data
        context = InvestigationContext(
            signature=signature,
            recent_events=tuple(recent_events),
            trace_data=tuple(traces),
            related_logs=tuple(logs),
            codebase_path=self.codebase_path,
            historical_context=tuple(similar),
        )

        # Invoke diagnosis
        try:
            diagnosis = await self.diagnosis_engine.diagnose(context)
        except Exception as e:
            # Diagnosis failed - restore original diagnosis and status using domain method
            signature.restore_state(original_status, original_diagnosis)
            try:
                await self.store.update(signature)
            except Exception as store_error:
                logger.error(
                    f"Failed to restore signature state after diagnosis failure: "
                    f"{store_error}",
                    exc_info=True,
                )
            # Log the original diagnosis error and re-raise
            logger.error(
                f"Diagnosis failed during reinvestigation for signature {signature_id}: {e}",
                exc_info=True,
            )
            raise

        # Update signature with diagnosis and mark as DIAGNOSED using domain method
        signature.mark_diagnosed(diagnosis)
        await self.store.update(signature)

        logger.info(
            f"Completed reinvestigation for signature {signature_id}",
            extra={
                "signature_id": signature_id,
                "confidence": diagnosis.confidence,
                "cost_usd": diagnosis.cost_usd,
            },
        )

        # Send notification if warranted (failure here should NOT revert the persisted diagnosis)
        # Pass original status to should_notify for correct medium-confidence logic
        try:
            if self.triage.should_notify(signature, diagnosis, original_status=original_status):
                await self.notification.report(signature, diagnosis)
                logger.info(
                    f"Notification sent for reinvestigated signature {signature_id}",
                    extra={"signature_id": signature_id},
                )
        except Exception as e:
            # Log notification failure but don't revert the successful diagnosis
            logger.error(
                f"Failed to notify about diagnosis for signature {signature_id}: {e}",
                exc_info=True,
            )

        return diagnosis
