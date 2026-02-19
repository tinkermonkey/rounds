"""Fake ManagementPort implementation for testing."""

from datetime import datetime, timezone
from typing import Any

from rounds.core.models import Diagnosis, Signature, SignatureDetails, SignatureStatus
from rounds.core.ports import ManagementPort


class FakeManagementPort(ManagementPort):
    """In-memory management port for testing.

    Tracks all management operations for test assertions.
    """

    def __init__(self) -> None:
        """Initialize with empty operation tracking."""
        self.muted_signatures: dict[str, str | None] = {}
        self.resolved_signatures: dict[str, str | None] = {}
        self.retriaged_signatures: list[str] = []
        self.signature_details: dict[str, SignatureDetails] = {}
        self.reinvestigated_signatures: list[str] = []
        self.stored_signatures: list[Signature] = []
        self.should_fail: bool = False
        self.fail_message: str = "Management operation failed"

    async def mute_signature(
        self, signature_id: str, reason: str | None = None
    ) -> None:
        """Mute a signature.

        Tracks the mute operation for test assertions.
        """
        if self.should_fail:
            raise RuntimeError(self.fail_message)

        self.muted_signatures[signature_id] = reason

    async def resolve_signature(
        self, signature_id: str, fix_applied: str | None = None
    ) -> None:
        """Resolve a signature.

        Tracks the resolution for test assertions.
        """
        if self.should_fail:
            raise RuntimeError(self.fail_message)

        self.resolved_signatures[signature_id] = fix_applied

    async def retriage_signature(self, signature_id: str) -> None:
        """Retriage a signature.

        Tracks the retriage for test assertions.
        """
        if self.should_fail:
            raise RuntimeError(self.fail_message)

        if signature_id not in self.retriaged_signatures:
            self.retriaged_signatures.append(signature_id)

    async def get_signature_details(self, signature_id: str) -> SignatureDetails:
        """Get details for a signature.

        Returns pre-configured details or a default SignatureDetails.
        """
        if self.should_fail:
            raise RuntimeError(self.fail_message)

        details = self.signature_details.get(signature_id)
        if details:
            return details
        # Return default empty SignatureDetails
        return SignatureDetails(
            signature=Signature(
                id=signature_id,
                fingerprint="",
                error_type="",
                service="",
                message_template="",
                stack_hash="",
                first_seen=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
                occurrence_count=1,
                status=SignatureStatus.NEW,
            ),
            recent_events=(),
            related_signatures=(),
        )

    async def list_signatures(
        self, status: SignatureStatus | None = None
    ) -> list[Signature]:
        """List signatures, optionally filtered by status.

        Returns all stored signatures or filtered by status.
        """
        if self.should_fail:
            raise RuntimeError(self.fail_message)

        if status is None:
            return self.stored_signatures
        return [s for s in self.stored_signatures if s.status == status]

    async def reinvestigate(self, signature_id: str) -> Diagnosis:
        """Trigger reinvestigation of a signature.

        Returns a mock diagnosis and tracks the operation.
        """
        if self.should_fail:
            raise RuntimeError(self.fail_message)

        self.reinvestigated_signatures.append(signature_id)
        return Diagnosis(
            root_cause="Fake root cause",
            evidence=("Fake evidence",),
            suggested_fix="Fake fix",
            confidence="medium",
            diagnosed_at=datetime.now(timezone.utc),
            model="fake-model",
            cost_usd=0.0,
        )

    def set_signature_details(
        self, signature_id: str, details: SignatureDetails
    ) -> None:
        """Set details for a specific signature."""
        self.signature_details[signature_id] = details

    def is_signature_muted(self, signature_id: str) -> bool:
        """Check if a signature is muted."""
        return signature_id in self.muted_signatures

    def is_signature_resolved(self, signature_id: str) -> bool:
        """Check if a signature is resolved."""
        return signature_id in self.resolved_signatures

    def is_signature_retriaged(self, signature_id: str) -> bool:
        """Check if a signature was retriaged."""
        return signature_id in self.retriaged_signatures

    def get_mute_reason(self, signature_id: str) -> str | None:
        """Get the mute reason for a signature."""
        return self.muted_signatures.get(signature_id)

    def get_fix_applied(self, signature_id: str) -> str | None:
        """Get the fix applied for a resolved signature."""
        return self.resolved_signatures.get(signature_id)

    def set_should_fail(
        self, should_fail: bool, message: str = "Management operation failed"
    ) -> None:
        """Configure the adapter to fail on the next operation."""
        self.should_fail = should_fail
        self.fail_message = message

    def add_stored_signature(self, signature: Signature) -> None:
        """Add a signature for list_signatures to return."""
        self.stored_signatures.append(signature)

    def reset(self) -> None:
        """Reset all collected data and state."""
        self.muted_signatures.clear()
        self.resolved_signatures.clear()
        self.retriaged_signatures.clear()
        self.signature_details.clear()
        self.reinvestigated_signatures.clear()
        self.stored_signatures.clear()
        self.should_fail = False
        self.fail_message = "Management operation failed"
