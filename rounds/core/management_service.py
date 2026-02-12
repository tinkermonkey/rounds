"""Management service: implements ManagementPort for human-initiated operations.

This is a core service that orchestrates management operations (mute, resolve,
retriage, get details) by interacting with the signature store. It ensures all
state changes are properly logged and auditable.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from rounds.core.models import Signature, SignatureStatus
from rounds.core.ports import ManagementPort, SignatureStorePort

logger = logging.getLogger(__name__)


class ManagementService(ManagementPort):
    """Core implementation of ManagementPort.

    Coordinates management operations with the signature store.
    All operations are logged for audit trails.
    """

    def __init__(self, store: SignatureStorePort):
        """Initialize the management service.

        Args:
            store: SignatureStorePort implementation for persistence.
        """
        self.store = store

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
        signature.last_seen = datetime.now(timezone.utc)

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
        signature.last_seen = datetime.now(timezone.utc)

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
        signature.last_seen = datetime.now(timezone.utc)

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
