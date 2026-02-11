"""Fake SignatureStorePort implementation for testing."""

from typing import Any

from rounds.core.models import Signature
from rounds.core.ports import SignatureStorePort


class FakeSignatureStorePort(SignatureStorePort):
    """In-memory signature store for testing.

    Provides a simple in-memory implementation of the signature store that
    tracks all operations for test assertions.
    """

    def __init__(self):
        """Initialize with empty signature store."""
        self.signatures: dict[str, Signature] = {}
        self.pending_signatures: list[Signature] = []
        self.saved_signatures: list[Signature] = []
        self.updated_signatures: list[Signature] = []
        self.get_by_fingerprint_calls: list[str] = []
        self.get_pending_investigation_call_count = 0
        self.get_similar_calls: list[tuple[Signature, int]] = []

    async def get_by_fingerprint(self, fingerprint: str) -> Signature | None:
        """Get a signature by fingerprint.

        Returns the signature if found, None otherwise.
        """
        self.get_by_fingerprint_calls.append(fingerprint)
        return self.signatures.get(fingerprint)

    async def save(self, signature: Signature) -> None:
        """Save a new signature.

        Stores the signature and marks it as saved for assertion.
        """
        self.signatures[signature.fingerprint] = signature
        self.saved_signatures.append(signature)

    async def update(self, signature: Signature) -> None:
        """Update an existing signature.

        Updates the signature and marks it as updated for assertion.
        """
        self.signatures[signature.fingerprint] = signature
        self.updated_signatures.append(signature)

    async def get_pending_investigation(self) -> list[Signature]:
        """Get all signatures pending investigation.

        Returns signatures that have been marked as pending.
        """
        self.get_pending_investigation_call_count += 1
        return self.pending_signatures

    async def get_similar(
        self, signature: Signature, limit: int = 5
    ) -> list[Signature]:
        """Get similar signatures.

        Returns up to `limit` similar signatures.
        """
        self.get_similar_calls.append((signature, limit))

        # Simple similarity matching: same error type and service
        similar = [
            sig
            for sig in self.signatures.values()
            if sig.error_type == signature.error_type
            and sig.service == signature.service
            and sig.fingerprint != signature.fingerprint
        ]

        return similar[:limit]

    async def get_stats(self) -> dict[str, Any]:
        """Get store statistics.

        Returns counts of signatures by status.
        """
        stats = {
            "total_signatures": len(self.signatures),
            "saved_count": len(self.saved_signatures),
            "updated_count": len(self.updated_signatures),
        }

        # Group by status
        status_counts: dict[str, int] = {}
        for sig in self.signatures.values():
            status = sig.status.value
            status_counts[status] = status_counts.get(status, 0) + 1

        stats.update(status_counts)

        return stats

    def mark_pending(self, signature: Signature) -> None:
        """Mark a signature as pending investigation."""
        if signature not in self.pending_signatures:
            self.pending_signatures.append(signature)

    def clear_pending(self) -> None:
        """Clear all pending signatures."""
        self.pending_signatures.clear()

    def reset(self) -> None:
        """Reset all collected data and statistics."""
        self.signatures.clear()
        self.pending_signatures.clear()
        self.saved_signatures.clear()
        self.updated_signatures.clear()
        self.get_by_fingerprint_calls.clear()
        self.get_pending_investigation_call_count = 0
        self.get_similar_calls.clear()
