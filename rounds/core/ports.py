"""Port interfaces for the Rounds diagnostic system.

These abstract base classes define the boundaries between core
domain logic and external adapters. Implementations live in the
adapters/ package.

Port Interface Categories:

1. **Driven Ports** (core calls out to adapters)
   - TelemetryPort: Retrieve errors, traces, logs
   - SignatureStorePort: Persist and query signatures
   - DiagnosisPort: LLM-powered root cause analysis
   - UsageQueryPort: Query actual diagnosis cost from OTLP usage data
   - NotificationPort: Report findings to developers
   - BudgetTracker: Track accumulated spend per round step
   - HealthCheckPort: Expose daemon poll-cycle health for monitoring

2. **Driving Ports** (adapters/external systems call into core)
   - PollPort: Entry point for poll and investigation cycles
   - ManagementPort: Human-initiated actions (mute, resolve, etc.)
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .models import (
    Diagnosis,
    ErrorEvent,
    HealthSnapshot,
    InvestigationContext,
    InvestigationResult,
    LogEntry,
    PartialResultsInfo,
    PollResult,
    ResolutionResult,
    RoundStep,
    Signature,
    SignatureDetails,
    SignatureStatus,
    SpanSummary,
    StoreStats,
    TraceInvestigation,
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
    ) -> Sequence[ErrorEvent]:
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
    async def get_traces(self, trace_ids: list[str]) -> tuple[Sequence[TraceTree], PartialResultsInfo]:
        """Batch trace retrieval.

        Args:
            trace_ids: List of OpenTelemetry trace IDs.

        Returns:
            Tuple of (traces, partial_info) where:
            - traces: List of TraceTree objects. Network failures and individual
              trace fetch failures are silently omitted from results, so order
              may not match trace_ids.
            - partial_info: Metadata about whether results were truncated/partial.

        Raises:
            ValueError: If trace ID format validation fails (adapter-specific).
            Exception: If telemetry backend is unreachable.
        """

    @abstractmethod
    async def get_correlated_logs(
        self, trace_ids: list[str], window_minutes: int = 5
    ) -> Sequence[LogEntry]:
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
    ) -> Sequence[ErrorEvent]:
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

    @abstractmethod
    async def search_logs(
        self,
        query: str,
        since: datetime,
        until: datetime | None = None,
        services: list[str] | None = None,
        limit: int = 100,
    ) -> Sequence[LogEntry]:
        """Search logs by keyword and optional metadata filters.

        Args:
            query: Keyword or phrase to search in log bodies. Empty string matches all.
            since: Return logs after this timestamp.
            until: Return logs before this timestamp. Defaults to now.
            services: Optional service name filter.
            limit: Maximum results to return.

        Returns:
            List of LogEntry objects in descending timestamp order.

        Raises:
            Exception: If telemetry backend is unreachable.
        """

    @abstractmethod
    async def search_spans(
        self,
        since: datetime,
        until: datetime | None = None,
        services: list[str] | None = None,
        operation: str | None = None,
        attributes: dict[str, str] | None = None,
        has_error: bool | None = None,
        limit: int = 100,
    ) -> Sequence[SpanSummary]:
        """Search spans by metadata filters.

        Args:
            since: Return spans after this timestamp.
            until: Return spans before this timestamp. Defaults to now.
            services: Optional service name filter.
            operation: Optional operation name substring filter.
            attributes: Optional key-value span attribute filters.
            has_error: If set, filter to error or non-error spans only.
            limit: Maximum results to return.

        Returns:
            List of SpanSummary objects in descending timestamp order.

        Raises:
            Exception: If telemetry backend is unreachable.
        """

    @abstractmethod
    async def list_services(self) -> list[str]:
        """Return all service names visible in the telemetry backend.

        Used to enumerate instrumented services for filter configuration
        and service-to-host mapping.

        Returns:
            Sorted list of service name strings, exactly as reported by
            the telemetry backend (case-sensitive).

        Raises:
            Exception: If telemetry backend is unreachable.
        """

    async def close(self) -> None:
        """Close connections and clean up resources.

        Optional lifecycle method for adapters that manage long-lived connections.
        Default implementation does nothing.

        Raises:
            Exception: If resource cleanup fails.
        """
        pass


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
    async def get_by_id(self, signature_id: str) -> Signature | None:
        """Look up a signature by its ID.

        Args:
            signature_id: UUID of the signature.

        Returns:
            Signature object if found, None otherwise.

        Raises:
            Exception: If database is unavailable.
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
    async def get_pending_investigation(self) -> Sequence[Signature]:
        """Return signatures with status NEW, ordered by priority.

        Returns:
            List of Signature objects with NEW status.

        Raises:
            Exception: If database is unavailable.
        """

    @abstractmethod
    async def get_all(
        self, status: SignatureStatus | None = None
    ) -> Sequence[Signature]:
        """Return all signatures, optionally filtered by status.

        Args:
            status: Filter to signatures with this status. If None, return all.

        Returns:
            List of Signature objects matching the criteria.

        Raises:
            Exception: If database is unavailable.
        """

    @abstractmethod
    async def get_similar(
        self, signature: Signature, limit: int = 5
    ) -> Sequence[Signature]:
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
    async def get_stats(self) -> "StoreStats":
        """Summary statistics for reporting.

        Returns:
            StoreStats with counts by status, service, and age information.

        Raises:
            Exception: If database is unavailable.
        """

    async def close_pool(self) -> None:
        """Close database connections and clean up resources.

        Optional lifecycle method for adapters that manage connection pools.
        Default implementation does nothing.

        Raises:
            Exception: If resource cleanup fails.
        """
        pass


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

    @abstractmethod
    async def investigate_trace(
        self,
        trace: TraceTree,
        codebase_path: str,
        correlated_logs: tuple[LogEntry, ...] = (),
    ) -> TraceInvestigation:
        """Explain the code flow for a distributed trace.

        Unlike diagnose(), this is not tied to an error signature. It reads
        the actual source files in codebase_path and produces a human-readable
        walkthrough of the request's end-to-end behaviour.

        Args:
            trace: Full span tree for the trace to investigate.
            codebase_path: Absolute path to the codebase on disk.
            correlated_logs: Log entries correlated with this trace (optional).

        Returns:
            TraceInvestigation with summary, ordered code flow steps,
            services involved, key findings, model name, and cost.

        Raises:
            TimeoutError: If the LLM invocation exceeds the configured timeout.
            RuntimeError: If the LLM backend returns an error.
            ValueError: If the response cannot be parsed.
        """


class UsageQueryPort(ABC):
    """Port for querying actual diagnosis cost from OTLP usage data.

    Cost tracking via OTLP is architecturally separate from observability
    telemetry (TelemetryPort), since it queries usage/cost data rather than
    traces, logs, or errors.

    Adapters implementing this port should query the telemetry backend's
    usage/cost records correlated by trace ID and return the actual cost
    incurred, as reported by the LLM provider (as opposed to a heuristic
    pre-flight estimate).
    """

    @abstractmethod
    async def query_diagnosis_cost(self, trace_id: str) -> float:
        """Query the actual diagnosis cost (in USD) for a given trace.

        Args:
            trace_id: OpenTelemetry trace ID correlating the diagnosis
                invocation to its usage/cost data.

        Returns:
            Actual cost in USD. Returns 0.0 when usage data is unavailable
            (e.g. not yet reported, backend unreachable, or no matching
            records found).
        """


class PartialNotificationError(Exception):
    """A sibling notification channel failed after another already sent an alert.

    Fan-out `NotificationPort` implementations (e.g. a composite adapter
    dispatching to multiple channels concurrently) should raise this instead
    of the sibling's raw exception when at least one channel raised but
    another channel already returned a cooldown timestamp from `report()`.
    That alert has already been delivered, so callers must extract
    `alerted_at` and record it (`Signature.record_alert()` plus a store
    update) before propagating this error - otherwise the cooldown is
    silently lost and the next poll cycle re-sends a duplicate alert.
    """

    def __init__(self, alerted_at: datetime, original_error: BaseException):
        super().__init__(str(original_error))
        self.alerted_at = alerted_at
        self.original_error = original_error


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
    ) -> datetime | None:
        """Report a diagnosed signature through whatever channel the adapter implements.

        Args:
            signature: The signature that was diagnosed.
            diagnosis: The diagnosis results to report.

        Returns:
            The timestamp to record as the signature's alert cooldown
            checkpoint, if this channel performs cooldown-gated alerting and
            just sent one. `None` if the channel has no cooldown concept, or
            if the alert was suppressed by the channel's own gating logic
            (e.g. severity gate, mute status, cooldown window). Adapters must
            not mutate `signature` or persist to a store themselves — the
            calling domain service is responsible for recording the
            cooldown via `Signature.record_alert()` and persisting it.

        Raises:
            Exception: If notification channel is unavailable.
                Caller may choose to queue for retry or log error.
            PartialNotificationError: If this is a fan-out implementation
                and a sibling channel failed after another channel already
                returned an alert timestamp. Callers must handle this
                specially - see the exception's docstring.
        """

    @abstractmethod
    async def report_summary(self, stats: dict[str, Any]) -> None:
        """Periodic summary report.

        Args:
            stats: Dictionary with summary statistics.

        Raises:
            Exception: If notification channel is unavailable.
        """

    @abstractmethod
    async def report_alert(self, alert: dict[str, Any]) -> None:
        """Send an operational alert to the notification channel.

        Used for daemon health events such as investigation pipeline suspension.
        Alert payloads are distinct from summary statistics and may be formatted
        differently by adapters (e.g. paging vs. periodic digest).

        Expected keys vary by alert type. For ``investigation_pipeline_suspended``:
            - ``alert``: alert type identifier string
            - ``consecutive_failures``: int count of consecutive failures
            - ``suspended_for_seconds``: int backoff duration
            - ``message``: human-readable description

        Args:
            alert: Dictionary describing the alert event.

        Raises:
            Exception: If notification channel is unavailable.
        """

    async def close(self) -> None:
        """Close connections and clean up resources.

        Optional lifecycle method for adapters that manage external connections.
        Default implementation does nothing. Subclasses should override
        if they manage connections, file handles, or other resources.

        Raises:
            Exception: If resource cleanup fails.
        """
        pass

    async def close_resolved_issue(self, signature: Signature) -> None:
        """Close whatever open issue/thread was created for this signature.

        Called by the resolution-detection cycle after a signature transitions
        to RESOLVED (no new occurrences within its resolution threshold).
        Optional lifecycle method: adapters with no notion of an "open" item
        to close (stdout, markdown) inherit this no-op default. Adapters that
        track open state (e.g. GitHub issues) should override it to close that
        item with a comment explaining the error has stopped occurring.

        Args:
            signature: The signature that was just auto-resolved.

        Raises:
            Exception: If the notification channel is unavailable.
        """
        pass


class BudgetTracker(Protocol):
    """Protocol for tracking accumulated spend against a budget.

    Costs are attributed per RoundStep so spend can be broken down across
    the full poll -> fingerprint -> diagnose -> confirm cycle, not just the
    diagnose step where LLM calls currently occur. Costs incurred while
    investigating a signature are additionally attributed to that
    signature's service, enabling per-service budget caps alongside the
    global daily budget.
    """

    async def record_cost(
        self, step: RoundStep, cost_usd: float, *, service: str | None = None
    ) -> None:
        """Record a cost incurred by a rounds step towards the daily budget.

        Args:
            step: Which rounds step incurred the cost.
            cost_usd: Cost incurred by that step, in USD.
            service: The service the cost should also be attributed to for
                per-service budget tracking. Optional and keyword-only so
                existing callers are unaffected; omitted for steps (like
                poll/fingerprint) that aren't attributable to a single
                signature's service.
        """
        ...

    async def is_service_budget_exceeded(self, service: str) -> bool:
        """Check whether a service's per-service daily budget cap has been reached.

        Returns False when no cap is configured for the service - uncapped
        services are governed only by the global daily budget.
        """
        ...


class HealthCheckPort(Protocol):
    """Protocol for exposing daemon poll-cycle health to monitoring endpoints.

    Lets adapters such as the webhook HTTP server report daemon health
    (e.g. for /health) without importing the concrete DaemonScheduler
    adapter directly.
    """

    def get_health_snapshot(self) -> HealthSnapshot:
        """Return a read-only snapshot of poll-cycle health state."""
        ...


class DigestFlushPort(Protocol):
    """Protocol for flushing a periodic digest of batched notifications.

    Lets adapters such as DaemonScheduler drive the WARN-digest flush
    schedule without importing the concrete DigestNotificationAdapter
    adapter directly.
    """

    async def flush_if_due(self, now: datetime, window: timedelta) -> bool:
        """Flush the digest if at least `window` has elapsed since it opened.

        Args:
            now: Current timestamp.
            window: Digest window duration.

        Returns:
            True if the window was due and flushed (the buffer may have been
            empty), False if the window hasn't elapsed yet.
        """
        ...


class DashboardMetricsPort(Protocol):
    """Protocol for exposing daemon state backing the self-observability dashboard.

    Lets telemetry.py register the dashboard's observable gauges without
    importing the concrete DaemonScheduler adapter directly.
    """

    @property
    def signature_counts_by_status(self) -> dict[str, int]:
        """Current count of error signatures grouped by status."""
        ...

    @property
    def daily_cost_usd(self) -> float:
        """Today's total accumulated diagnosis cost, in USD."""
        ...

    @property
    def cost_by_step(self) -> dict[RoundStep, float]:
        """Today's accumulated diagnosis cost, broken down by pipeline step."""
        ...


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
    async def execute_investigation_cycle(self) -> InvestigationResult:
        """Investigate pending signatures.

        Returns result with diagnoses produced and failure count.

        Returns:
            InvestigationResult containing diagnoses, attempt count, and failure count.

        Raises:
            Exception: Only for fatal errors.
        """

    async def execute_resolution_cycle(self) -> ResolutionResult:
        """Check diagnosed signatures against their resolution threshold and auto-close stale ones.

        Optional lifecycle method backing the resolution-detection cycle.
        Default implementation is a no-op that reports zero resolutions;
        implementations that support auto-close should override this.

        Returns:
            ResolutionResult summarizing how many signatures were auto-resolved.

        Raises:
            Exception: Only for fatal errors. Transient errors should be logged
                and the cycle should continue with partial results.
        """
        return ResolutionResult(signatures_resolved=0, timestamp=datetime.now(UTC))


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
    async def get_signature_details(self, signature_id: str) -> "SignatureDetails":
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
            Exception: If signature doesn't exist or database error.
        """

    @abstractmethod
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

    @abstractmethod
    async def reinvestigate(self, signature_id: str) -> Diagnosis:
        """Trigger immediate investigation/re-investigation of a signature.

        Resets the signature to NEW status, queues for investigation,
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

    @abstractmethod
    async def investigate_trace(self, trace_id: str) -> TraceInvestigation:
        """Fetch a trace by ID and explain the code flow through the codebase.

        Does not require an existing signature. Fetches the trace from the
        telemetry backend, retrieves correlated logs, and invokes the
        diagnosis engine to produce a code-flow walkthrough.

        Args:
            trace_id: OpenTelemetry trace ID (128-bit hex string).

        Returns:
            TraceInvestigation with summary, code flow steps, services,
            key findings, model name, and cost.

        Raises:
            KeyError: If the trace does not exist in the telemetry backend.
            TimeoutError: If the diagnosis engine times out.
            Exception: If the telemetry backend or diagnosis engine fails.
        """

    @abstractmethod
    async def search_logs(
        self,
        query: str,
        since: datetime,
        until: datetime | None = None,
        services: list[str] | None = None,
        limit: int = 100,
    ) -> Sequence[LogEntry]:
        """Search logs by keyword and optional metadata filters.

        Args:
            query: Keyword or phrase to search in log bodies. Empty string matches all.
            since: Return logs after this timestamp.
            until: Return logs before this timestamp. Defaults to now.
            services: Optional service name filter.
            limit: Maximum results to return.

        Returns:
            List of LogEntry objects in descending timestamp order.

        Raises:
            Exception: If telemetry backend is unreachable.
        """

    @abstractmethod
    async def search_spans(
        self,
        since: datetime,
        until: datetime | None = None,
        services: list[str] | None = None,
        operation: str | None = None,
        attributes: dict[str, str] | None = None,
        has_error: bool | None = None,
        limit: int = 100,
    ) -> Sequence[SpanSummary]:
        """Search spans by metadata filters.

        Args:
            since: Return spans after this timestamp.
            until: Return spans before this timestamp. Defaults to now.
            services: Optional service name filter.
            operation: Optional operation name substring filter.
            attributes: Optional key-value span attribute filters.
            has_error: If set, filter to error or non-error spans only.
            limit: Maximum results to return.

        Returns:
            List of SpanSummary objects in descending timestamp order.

        Raises:
            Exception: If telemetry backend is unreachable.
        """

    @abstractmethod
    async def get_trace_tree(self, trace_id: str) -> TraceTree:
        """Fetch the full span hierarchy for a trace without LLM analysis.

        Returns the raw span tree assembled from the telemetry backend.
        Use this for structural exploration; use investigate_trace() when
        you want LLM-generated code-flow analysis.

        Args:
            trace_id: OpenTelemetry trace ID (128-bit hex string).

        Returns:
            TraceTree with root_span hierarchy and error_spans collection.

        Raises:
            KeyError: If the trace does not exist in the telemetry backend.
            Exception: If the telemetry backend is unreachable.
        """

    @abstractmethod
    async def list_services(self) -> list[str]:
        """Return all service names visible in the telemetry backend.

        Delegates to the underlying TelemetryPort implementation.

        Returns:
            Sorted list of service name strings (exact, case-sensitive).

        Raises:
            Exception: If telemetry backend is unreachable.
        """
