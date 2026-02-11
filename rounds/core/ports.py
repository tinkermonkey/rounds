"""Port interfaces for the Rounds diagnostic system.

These abstract base classes define the boundaries between core
domain logic and external adapters. Implementations live in the
adapters/ package.

Port Interface Categories:

1. **Driven Ports** (core calls out to adapters)
   - TelemetryPort: Retrieve errors, traces, logs
   - SignatureStorePort: Persist and query signatures
   - DiagnosisPort: LLM-powered root cause analysis
   - NotificationPort: Report findings to developers

2. **Driving Ports** (adapters/external systems call into core)
   - PollPort: Entry point for poll and investigation cycles
   - ManagementPort: Human-initiated actions (mute, resolve, etc.)
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from .models import (
    Diagnosis,
    ErrorEvent,
    InvestigationContext,
    LogEntry,
    Signature,
    SignatureStatus,
    TraceTree,
)


# ============================================================================
# DRIVEN PORTS (Core calls out to adapters)
# ============================================================================


class TelemetryPort(ABC):
    """Port for retrieving errors, traces, and logs from telemetry backend.

    Adapters implementing this port should retrieve data from external
    telemetry systems (SigNoz, Jaeger, Grafana Loki, etc.) and normalize
    into the core's domain models.

    Implementations must handle:
    - Pagination/batching for large result sets
    - Timestamp normalization across systems
    - Error resilience (timeouts, 5xx responses)
    - Caching (if appropriate for the backend)
    """

    @abstractmethod
    async def get_recent_errors(
        self,
        service: str | None = None,
        since_timestamp: datetime | None = None,
        limit: int = 100,
    ) -> list[ErrorEvent]:
        """Retrieve recent errors from the telemetry backend.

        Args:
            service: Filter to specific service (optional).
                If None, retrieve errors from all services.
            since_timestamp: Only errors after this timestamp (optional).
                If None, use backend's default window (usually 1 hour).
            limit: Maximum number of errors to return (default 100).

        Returns:
            List of normalized ErrorEvent objects in descending timestamp order.
            Empty list if no errors found.

        Raises:
            Exception: If telemetry backend is unreachable or returns error.
                Caller should handle gracefully (e.g., backoff retry).
        """

    @abstractmethod
    async def get_trace(self, trace_id: str) -> TraceTree | None:
        """Retrieve complete trace hierarchy by trace ID.

        Args:
            trace_id: OpenTelemetry trace ID (128-bit hex string).

        Returns:
            TraceTree with full span hierarchy, or None if trace not found.

        Raises:
            Exception: If telemetry backend is unreachable.
        """

    @abstractmethod
    async def get_logs_for_trace(
        self, trace_id: str, limit: int = 50
    ) -> list[LogEntry]:
        """Retrieve all log entries associated with a trace.

        Args:
            trace_id: OpenTelemetry trace ID.
            limit: Maximum number of log entries to return.

        Returns:
            List of LogEntry objects in ascending timestamp order.
            Empty list if no logs found.

        Raises:
            Exception: If telemetry backend is unreachable.
        """

    @abstractmethod
    async def get_related_errors(
        self,
        error_type: str,
        service: str,
        since_timestamp: datetime,
        limit: int = 10,
    ) -> list[ErrorEvent]:
        """Retrieve other errors of the same type in the service.

        Used to provide context for diagnosis. Adapter may implement
        similarity matching based on error message patterns.

        Args:
            error_type: Error class/name (e.g. "ConnectionTimeoutError").
            service: Service name.
            since_timestamp: Time window start (backward from now).
            limit: Maximum number of related errors to return.

        Returns:
            List of ErrorEvent objects (may not include the original error).

        Raises:
            Exception: If telemetry backend is unreachable.
        """


class SignatureStorePort(ABC):
    """Port for persisting and querying signatures in the signature database.

    Adapters implementing this port should provide ACID-compliant storage
    of Signature objects with support for querying, updating, and archival.

    Implementations must handle:
    - Concurrent read/write access
    - Transaction support (for atomic multi-operation updates)
    - Index optimization for query performance
    - Data retention and archival
    """

    @abstractmethod
    async def create(self, signature: Signature) -> str:
        """Create and persist a new signature.

        Args:
            signature: Signature object to create. The id field should be
                a UUID provided by the caller.

        Returns:
            The signature ID (UUID string).

        Raises:
            Exception: If signature with same ID already exists or
                database is unavailable.
        """

    @abstractmethod
    async def get_by_id(self, signature_id: str) -> Signature | None:
        """Retrieve a signature by ID.

        Args:
            signature_id: UUID of the signature.

        Returns:
            Signature object if found, None otherwise.

        Raises:
            Exception: If database is unavailable.
        """

    @abstractmethod
    async def get_by_fingerprint(self, fingerprint: str) -> Signature | None:
        """Retrieve a signature by its fingerprint hash.

        Used during deduplication to check if a signature already exists.

        Args:
            fingerprint: Hex digest of the normalized error.

        Returns:
            Signature object if found, None otherwise.

        Raises:
            Exception: If database is unavailable.
        """

    @abstractmethod
    async def update(self, signature: Signature) -> None:
        """Update an existing signature with new state.

        Updates the entire signature object atomically.

        Args:
            signature: Signature with updated fields
                (usually status, last_seen, occurrence_count, diagnosis).

        Raises:
            Exception: If signature doesn't exist or database is unavailable.
        """

    @abstractmethod
    async def query(
        self,
        service: str | None = None,
        status: SignatureStatus | None = None,
        error_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Signature], int]:
        """Query signatures with optional filters.

        Args:
            service: Filter to signatures from this service (optional).
            status: Filter to signatures with this status (optional).
            error_type: Filter to signatures with this error type (optional).
            limit: Number of results to return.
            offset: Number of results to skip (for pagination).

        Returns:
            Tuple of (signature_list, total_count).
            signature_list is ordered by last_seen descending.
            total_count is the total number of signatures matching filters
            (before limit/offset applied).

        Raises:
            Exception: If database is unavailable.
        """

    @abstractmethod
    async def delete(self, signature_id: str) -> None:
        """Delete a signature (archive or hard delete).

        Args:
            signature_id: UUID of the signature to delete.

        Raises:
            Exception: If signature doesn't exist or database is unavailable.
        """


class DiagnosisPort(ABC):
    """Port for invoking LLM-powered root cause analysis.

    Adapters implementing this port should invoke an LLM (Claude, OpenAI, etc.)
    with the investigation context and return a structured diagnosis.

    Implementations must handle:
    - Context formatting and token limit management
    - Model selection and versioning
    - Cost tracking and budget enforcement
    - Streaming response handling
    - Timeout and retry logic
    """

    @abstractmethod
    async def diagnose(
        self, context: InvestigationContext
    ) -> Diagnosis:
        """Invoke LLM analysis on an investigation context.

        The LLM should analyze the signature, recent errors, traces, logs,
        and codebase to identify the root cause and suggest a fix.

        Args:
            context: InvestigationContext with all data for diagnosis.

        Returns:
            Diagnosis object with root_cause, evidence, suggested_fix,
            confidence level, model name, and cost.

        Raises:
            Exception: If LLM backend is unavailable, times out, or
                cost exceeds configured budget.
        """

    @abstractmethod
    async def estimate_cost(self, context: InvestigationContext) -> float:
        """Estimate the cost (in USD) of diagnosing a signature.

        Used to enforce budget limits before invoking diagnose().

        Args:
            context: InvestigationContext to estimate cost for.

        Returns:
            Estimated cost in USD (float).

        Raises:
            Exception: If cost estimation fails.
        """


class NotificationPort(ABC):
    """Port for reporting diagnosis results to developers.

    Adapters implementing this port should deliver findings to the team
    via various channels (Slack, email, GitHub issues, stdout, etc.).

    Implementations must handle:
    - Formatting results appropriately for the medium
    - Rate limiting and deduplication
    - Retry logic for transient failures
    - Graceful degradation if notification channel is unavailable
    """

    @abstractmethod
    async def notify(
        self, signature: Signature, diagnosis: Diagnosis
    ) -> None:
        """Send a notification with diagnosis results.

        Formatter adapters (e.g., GitHub issue creator) should use the
        signature and diagnosis to create a notification in their medium.

        Args:
            signature: The signature that was diagnosed.
            diagnosis: The diagnosis results to report.

        Raises:
            Exception: If notification channel is unavailable.
                Caller may choose to queue for retry or log error.
        """


# ============================================================================
# DRIVING PORTS (Adapters/external systems call into core)
# ============================================================================


class PollPort(ABC):
    """Port for executing poll and investigation cycles.

    Driving port: the daemon scheduler or CLI invokes these methods to
    perform diagnostic cycles.

    Implementations of this port live in the core (poll_service.py).
    External adapters (daemon scheduler, webhook receiver, CLI) call
    these methods to trigger diagnostics.
    """

    @abstractmethod
    async def poll_and_investigate(self) -> None:
        """Execute one complete poll → fingerprint → deduplicate → diagnose cycle.

        This is the main entry point for the daemon loop.

        High-level flow:
        1. Query telemetry backend for recent errors
        2. Normalize errors into ErrorEvent objects
        3. Fingerprint and deduplicate against signature database
        4. For each new signature:
           - Queue for investigation
           - Estimate diagnosis cost
           - If within budget, invoke diagnosis
           - Store diagnosis result
           - Notify developers
        5. Return summary of work completed

        Should handle errors gracefully:
        - If telemetry backend is down, backoff and retry
        - If diagnosis exceeds budget, skip and log
        - If notification fails, log but don't fail the cycle

        Raises:
            Exception: Only for fatal errors (e.g., database corruption).
                Transient errors should be logged and cycle should
                continue with partial results.
        """

    @abstractmethod
    async def get_poll_summary(self) -> dict[str, Any]:
        """Get summary stats from the last poll cycle.

        Returns information about recent diagnostic activity:
        - errors_found: Total errors in the last poll
        - new_signatures: Signatures created
        - diagnosed_signatures: Signatures with completed diagnosis
        - total_cost_usd: Cost of diagnoses performed
        - last_poll_at: Timestamp of last poll

        Returns:
            Dictionary with poll statistics.
        """


class ManagementPort(ABC):
    """Port for human-initiated management operations.

    Driving port: the CLI or webhook receiver invokes these methods
    to perform manual actions (mute, resolve, retriage, etc.).

    Implementations live in the core. CLI or webhook adapters
    call these to perform user-requested operations.
    """

    @abstractmethod
    async def mute_signature(
        self, signature_id: str, reason: str | None = None
    ) -> None:
        """Mute a signature to suppress further notifications.

        Args:
            signature_id: UUID of the signature to mute.
            reason: Optional reason for muting (logged for audit).

        Raises:
            Exception: If signature doesn't exist or database error.
        """

    @abstractmethod
    async def resolve_signature(
        self, signature_id: str, fix_applied: str | None = None
    ) -> None:
        """Mark a signature as resolved.

        Args:
            signature_id: UUID of the signature.
            fix_applied: Optional description of the fix that was applied.

        Raises:
            Exception: If signature doesn't exist or database error.
        """

    @abstractmethod
    async def retriage_signature(self, signature_id: str) -> None:
        """Reset a signature to NEW status for re-investigation.

        Used when initial diagnosis was incorrect or needs updating.

        Args:
            signature_id: UUID of the signature.

        Raises:
            Exception: If signature doesn't exist or database error.
        """

    @abstractmethod
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
            Exception: If signature doesn't exist or database error.
        """
