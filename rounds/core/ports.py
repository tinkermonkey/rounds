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
    PollResult,
    Signature,
    SignatureStatus,
    TraceTree,
)


# ============================================================================
# DRIVEN PORTS (Core calls out to adapters)
# ============================================================================


class TelemetryPort(ABC):
    """Port for retrieving errors, traces, and logs from telemetry backend.

    How the core retrieves observability data.
    Defined in terms of domain models, not backend-specific queries.

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
        self, since: datetime, services: list[str] | None = None
    ) -> list[ErrorEvent]:
        """Return error events since the given timestamp.

        The adapter translates this to backend-specific queries.

        Args:
            since: Return errors after this timestamp.
            services: Filter to specific services (optional).
                If None, retrieve errors from all services.

        Returns:
            List of normalized ErrorEvent objects in descending timestamp order.
            Empty list if no errors found.

        Raises:
            Exception: If telemetry backend is unreachable or returns error.
                Caller should handle gracefully (e.g., backoff retry).
        """

    @abstractmethod
    async def get_trace(self, trace_id: str) -> TraceTree:
        """Return the full span tree for a trace.

        Args:
            trace_id: OpenTelemetry trace ID (128-bit hex string).

        Returns:
            TraceTree with full span hierarchy.

        Raises:
            Exception: If telemetry backend is unreachable or trace not found.
        """

    @abstractmethod
    async def get_traces(self, trace_ids: list[str]) -> list[TraceTree]:
        """Batch trace retrieval.

        Args:
            trace_ids: List of OpenTelemetry trace IDs.

        Returns:
            List of TraceTree objects in the same order as trace_ids.
            If a trace is not found, it is omitted from results.

        Raises:
            Exception: If telemetry backend is unreachable.
        """

    @abstractmethod
    async def get_correlated_logs(
        self, trace_ids: list[str], window_minutes: int = 5
    ) -> list[LogEntry]:
        """Return logs correlated with the given traces.

        Includes a time window around each trace for context.

        Args:
            trace_ids: List of trace IDs to correlate logs with.
            window_minutes: Time window (minutes) before/after traces to include.

        Returns:
            List of LogEntry objects correlated with the traces.
            Empty list if no logs found.

        Raises:
            Exception: If telemetry backend is unreachable.
        """

    @abstractmethod
    async def get_events_for_signature(
        self, fingerprint: str, limit: int = 5
    ) -> list[ErrorEvent]:
        """Return recent events matching a known fingerprint.

        The adapter may implement this via tag queries, or the core
        may supply trace_ids from the store for the adapter to fetch.

        Args:
            fingerprint: Signature fingerprint hash.
            limit: Maximum number of events to return.

        Returns:
            List of ErrorEvent objects matching the fingerprint.

        Raises:
            Exception: If telemetry backend is unreachable.
        """


class SignatureStorePort(ABC):
    """Port for persisting and querying failure signatures.

    How the core persists and queries failure signatures.

    Adapters implementing this port should provide ACID-compliant storage
    of Signature objects with support for querying, updating, and archival.

    Implementations must handle:
    - Concurrent read/write access
    - Transaction support (for atomic multi-operation updates)
    - Index optimization for query performance
    - Data retention and archival
    """

    @abstractmethod
    async def get_by_fingerprint(self, fingerprint: str) -> Signature | None:
        """Look up a signature by its fingerprint hash.

        Args:
            fingerprint: Hex digest of the normalized error.

        Returns:
            Signature object if found, None otherwise.

        Raises:
            Exception: If database is unavailable.
        """

    @abstractmethod
    async def save(self, signature: Signature) -> None:
        """Create or update a signature.

        Args:
            signature: Signature object to persist.

        Raises:
            Exception: If database is unavailable.
        """

    @abstractmethod
    async def update(self, signature: Signature) -> None:
        """Update an existing signature.

        Args:
            signature: Signature with updated fields.

        Raises:
            Exception: If signature doesn't exist or database is unavailable.
        """

    @abstractmethod
    async def get_pending_investigation(self) -> list[Signature]:
        """Return signatures with status NEW, ordered by priority.

        Returns:
            List of Signature objects with NEW status.

        Raises:
            Exception: If database is unavailable.
        """

    @abstractmethod
    async def get_similar(
        self, signature: Signature, limit: int = 5
    ) -> list[Signature]:
        """Return signatures with similar characteristics.

        Args:
            signature: Reference signature to find similar ones.
            limit: Maximum number of similar signatures to return.

        Returns:
            List of similar Signature objects.

        Raises:
            Exception: If database is unavailable.
        """

    @abstractmethod
    async def get_stats(self) -> dict[str, Any]:
        """Summary statistics for reporting.

        Returns:
            Dictionary with statistics (keys are implementation-defined).

        Raises:
            Exception: If database is unavailable.
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
    """Port for reporting findings to developers.

    How the core reports findings to the developer.

    Adapters implementing this port should deliver findings to the team
    via various channels (Slack, email, GitHub issues, stdout, etc.).

    Implementations must handle:
    - Formatting results appropriately for the medium
    - Rate limiting and deduplication
    - Retry logic for transient failures
    - Graceful degradation if notification channel is unavailable
    """

    @abstractmethod
    async def report(
        self, signature: Signature, diagnosis: Diagnosis
    ) -> None:
        """Report a diagnosed signature through whatever channel the adapter implements.

        Args:
            signature: The signature that was diagnosed.
            diagnosis: The diagnosis results to report.

        Raises:
            Exception: If notification channel is unavailable.
                Caller may choose to queue for retry or log error.
        """

    @abstractmethod
    async def report_summary(self, stats: dict[str, Any]) -> None:
        """Periodic summary report.

        Args:
            stats: Dictionary with summary statistics.

        Raises:
            Exception: If notification channel is unavailable.
        """


# ============================================================================
# DRIVING PORTS (Adapters/external systems call into core)
# ============================================================================


class PollPort(ABC):
    """Entry point for triggering an error-checking cycle.

    The core defines what a poll cycle does;
    driving adapters decide when to trigger it.

    Driving port: the daemon scheduler or CLI invokes these methods to
    perform diagnostic cycles.

    Implementations of this port live in the core (poll_service.py).
    External adapters (daemon scheduler, webhook receiver, CLI) call
    these methods to trigger diagnostics.
    """

    @abstractmethod
    async def execute_poll_cycle(self) -> PollResult:
        """Check for new errors, fingerprint, dedup, and queue investigations.

        Returns a summary of what was found.

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

        Should handle errors gracefully:
        - If telemetry backend is down, backoff and retry
        - If diagnosis exceeds budget, skip and log
        - If notification fails, log but don't fail the cycle

        Returns:
            PollResult with summary of errors found and signatures created.

        Raises:
            Exception: Only for fatal errors (e.g., database corruption).
                Transient errors should be logged and cycle should
                continue with partial results.
        """

    @abstractmethod
    async def execute_investigation_cycle(self) -> list[Diagnosis]:
        """Investigate pending signatures.

        Returns diagnoses produced.

        Returns:
            List of Diagnosis objects for investigated signatures.

        Raises:
            Exception: Only for fatal errors.
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
