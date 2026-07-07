"""Domain models for the Rounds diagnostic system.

All models in this module use only Python standard library types,
ensuring zero external dependencies in the core domain.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, TypeAlias


@dataclass(frozen=True)
class StackFrame:
    """A single frame in a stack trace."""

    module: str
    function: str
    filename: str
    lineno: int | None

    def __post_init__(self) -> None:
        """Validate stack frame invariants on creation."""
        if not self.module or not self.module.strip():
            raise ValueError("module must be a non-empty string")
        if not self.function or not self.function.strip():
            raise ValueError("function must be a non-empty string")
        if not self.filename or not self.filename.strip():
            raise ValueError("filename must be a non-empty string")


class Severity(Enum):
    """Log severity levels from OpenTelemetry.

    Declaration order is significant: it defines the severity ordering
    used by `rank` to compare levels (e.g. to track a signature's highest
    observed severity).
    """

    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"

    @property
    def rank(self) -> int:
        """Relative ordering of this severity level, higher is more severe."""
        return list(Severity).index(self)


@dataclass(frozen=True)
class ErrorEvent:
    """A single error occurrence from telemetry.

    The core's normalized representation — not a SigNoz response,
    not a Jaeger span, but the canonical format for error analysis.
    """

    trace_id: str
    span_id: str
    service: str
    error_type: str  # e.g. "ConnectionTimeoutError"
    error_message: str  # raw message
    stack_frames: tuple[StackFrame, ...]  # immutable for frozen dataclass
    timestamp: datetime
    # Union type accepts both dict and MappingProxyType for initialization flexibility.
    # Runtime type is always MappingProxyType after __post_init__ conversion.
    attributes: dict[str, Any] | MappingProxyType[str, Any]
    severity: Severity

    def __post_init__(self) -> None:
        """Convert attributes dict to read-only proxy."""
        if isinstance(self.attributes, dict):
            object.__setattr__(
                self, "attributes", MappingProxyType(self.attributes)
            )


class SignatureStatus(Enum):
    """Lifecycle states for a failure signature.

    State transitions follow a directed workflow:
    - NEW: Initial state when signature is first discovered
    - INVESTIGATING: Signature is actively being analyzed
    - DIAGNOSED: Root cause analysis has been completed
    - RESOLVED: Issue has been fixed (applies to resolved errors)
    - MUTED: Signature is suppressed from notifications

    Note: The DIAGNOSED state represents post-diagnosis classification
    (after root cause analysis), rather than post-triage. This differs
    from traditional triage workflows where TRIAGED would mark pre-diagnosis
    classification. The current design uses DIAGNOSED to indicate that the
    root cause investigation has been completed.
    """

    NEW = "new"
    INVESTIGATING = "investigating"
    DIAGNOSED = "diagnosed"
    RESOLVED = "resolved"
    MUTED = "muted"


Confidence: TypeAlias = Literal["high", "medium", "low"]

# The four stages of a rounds cycle, used to attribute cost tracking
# (see BudgetTracker.record_cost) to the step that incurred it.
RoundStep: TypeAlias = Literal["poll", "fingerprint", "diagnose", "confirm"]


@dataclass(frozen=True)
class Diagnosis:
    """LLM-generated root cause analysis for a signature."""

    root_cause: str
    evidence: tuple[str, ...]  # immutable for frozen dataclass
    suggested_fix: str
    confidence: Confidence
    diagnosed_at: datetime
    model: str  # which model produced this
    cost_usd: float
    summary: str = ""  # one-paragraph overview of the diagnosis findings
    # Suggested auto-close window based on error characteristics: shorter
    # (6-12h) for transient/infra errors, longer (24-48h) for persistent code
    # defects. None means the diagnosis backend was uncertain and the global
    # default resolution window should apply instead.
    suggested_resolution_hours: int | None = None

    def __post_init__(self) -> None:
        """Validate diagnosis invariants on creation."""
        if self.cost_usd < 0:
            raise ValueError(
                f"cost_usd must be non-negative, got {self.cost_usd}"
            )
        if (
            self.suggested_resolution_hours is not None
            and self.suggested_resolution_hours <= 0
        ):
            raise ValueError(
                "suggested_resolution_hours must be positive, got "
                f"{self.suggested_resolution_hours}"
            )


@dataclass
class Signature:
    """A fingerprinted failure pattern.

    Represents a class of errors, not a single occurrence.
    Tracks lifecycle, occurrence count, and optional diagnosis.

    State Transitions:
        Valid state transitions are:
        - NEW → INVESTIGATING (mark_investigating)
        - INVESTIGATING → DIAGNOSED (mark_diagnosed)
        - INVESTIGATING → NEW (revert_to_new, for error recovery)
        - NEW → DIAGNOSED (mark_diagnosed, direct transition)
        - ANY → RESOLVED (mark_resolved)
        - RESOLVED → NEW (record_occurrence, on error recurrence)
        - ANY → MUTED (mark_muted)
        - ANY → NEW (reset_to_new, for management operations)

    Note: This dataclass is intentionally mutable to allow updating
    signature state (status, occurrence_count, last_seen) after creation.
    """

    id: str  # UUID
    fingerprint: str  # hex digest of normalized error
    error_type: str
    service: str
    message_template: str  # parameterized message
    stack_hash: str  # hash of normalized stack structure
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    status: SignatureStatus
    diagnosis: Diagnosis | None = None
    tags: frozenset[str] = field(default_factory=frozenset)  # immutable set
    # Highest severity observed across this signature's ErrorEvents. Drives the
    # severity-{level} issue label and the phone-home severity gate. Defaults to
    # ERROR so existing signatures (persisted before this field existed) don't
    # need a backfill.
    max_severity: Severity = Severity.ERROR
    # Per-signature override for the auto-close window, in hours. Set from
    # Diagnosis.suggested_resolution_hours after diagnosis; None falls back to
    # the global default resolution window.
    resolution_threshold_hours: int | None = None
    # Timestamp of the last phone-home alert sent for this signature, used to
    # enforce the phone-home cooldown. None means no alert has been sent yet.
    last_alerted_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate signature invariants on creation or deserialization."""
        if self.occurrence_count < 1:
            raise ValueError(
                f"occurrence_count must be >= 1, got {self.occurrence_count}"
            )
        if self.last_seen < self.first_seen:
            raise ValueError(
                f"last_seen ({self.last_seen}) cannot be before "
                f"first_seen ({self.first_seen})"
            )
        if (
            self.resolution_threshold_hours is not None
            and self.resolution_threshold_hours <= 0
        ):
            raise ValueError(
                "resolution_threshold_hours must be positive, got "
                f"{self.resolution_threshold_hours}"
            )

    def mark_investigating(self) -> None:
        """Transition signature to investigating status."""
        if self.status not in {SignatureStatus.NEW, SignatureStatus.INVESTIGATING}:
            raise ValueError(
                f"Cannot investigate signature in {self.status} status"
            )
        self.status = SignatureStatus.INVESTIGATING

    def mark_diagnosed(self, diagnosis: Diagnosis) -> None:
        """Transition signature to diagnosed status with diagnosis.

        Also adopts diagnosis.suggested_resolution_hours as the signature's
        resolution_threshold_hours when the diagnosis provides one, keeping
        both mutations atomic.

        Raises:
            ValueError: If signature is already RESOLVED or MUTED, as these are
                terminal states that should not transition to DIAGNOSED.
        """
        if self.status == SignatureStatus.RESOLVED:
            raise ValueError(
                f"Cannot diagnose a resolved signature (id={self.id}). "
                "Use reset_to_new() first if re-diagnosis is needed."
            )
        if self.status == SignatureStatus.MUTED:
            raise ValueError(
                f"Cannot diagnose a muted signature (id={self.id}). "
                "Unmute the signature first if diagnosis is needed."
            )
        self.diagnosis = diagnosis
        if diagnosis.suggested_resolution_hours is not None:
            self.resolution_threshold_hours = diagnosis.suggested_resolution_hours
        self.status = SignatureStatus.DIAGNOSED

    def mark_resolved(self) -> None:
        """Transition signature to resolved status."""
        if self.status == SignatureStatus.RESOLVED:
            raise ValueError("Signature is already resolved")
        self.status = SignatureStatus.RESOLVED

    def mark_muted(self) -> None:
        """Transition signature to muted status."""
        if self.status == SignatureStatus.MUTED:
            raise ValueError("Signature is already muted")
        self.status = SignatureStatus.MUTED

    def record_occurrence(
        self, timestamp: datetime, severity: Severity | None = None
    ) -> None:
        """Record a new occurrence and update time bounds.

        Accepts any timestamp — events may arrive out-of-order when the telemetry
        backend returns results newest-first or when the lookback window overlaps
        with a previous poll cycle. first_seen tracks the earliest observed
        timestamp; last_seen tracks the latest.

        A recurrence against a RESOLVED signature (its issue was already
        auto-closed) transitions it back to NEW so it re-enters the triage and
        investigation pipeline rather than staying dormant under a closed issue.

        Args:
            timestamp: When the new occurrence was observed.
            severity: Severity of the new occurrence. If more severe than the
                signature's current max_severity, max_severity is raised to match.
        """
        if self.status == SignatureStatus.RESOLVED:
            self.status = SignatureStatus.NEW
        self.occurrence_count += 1
        if timestamp < self.first_seen:
            self.first_seen = timestamp
        if timestamp > self.last_seen:
            self.last_seen = timestamp
        if severity is not None and severity.rank > self.max_severity.rank:
            self.max_severity = severity

    def record_alert(self, timestamp: datetime) -> None:
        """Record that a phone-home alert was just sent for this signature.

        Used by the phone-home notification adapter to persist the cooldown
        timestamp so subsequent alerts are suppressed until it elapses.
        """
        self.last_alerted_at = timestamp

    def revert_to_new(self) -> None:
        """Revert signature from INVESTIGATING back to NEW status.

        Used for error recovery when diagnosis fails. Only works from INVESTIGATING status.
        """
        if self.status != SignatureStatus.INVESTIGATING:
            raise ValueError(
                f"Can only revert from INVESTIGATING status, current status: {self.status}"
            )
        self.status = SignatureStatus.NEW

    def reset_to_new(self) -> None:
        """Reset signature to NEW status from any current status.

        Used for management operations (e.g., retriage, reinvestigation) where
        a signature needs to be returned to NEW for reprocessing regardless of
        its current status.
        """
        self.status = SignatureStatus.NEW

    def clear_diagnosis(self) -> None:
        """Clear the diagnosis from this signature.

        Used during retriage or reinvestigation to reset the signature
        for a fresh diagnosis attempt.
        """
        self.diagnosis = None

    def restore_state(self, status: SignatureStatus, diagnosis: Diagnosis | None = None) -> None:
        """Restore signature to a previous state.

        Used for error recovery when a failed operation needs to be reverted.

        Args:
            status: The status to restore to.
            diagnosis: The diagnosis to restore (if any).
        """
        self.status = status
        self.diagnosis = diagnosis


# Type alias for event tuples in SpanNode (runtime type after __post_init__ conversion)
EventTuple: TypeAlias = tuple[MappingProxyType[str, Any], ...]


@dataclass(frozen=True)
class SpanNode:
    """A single span in a distributed trace."""

    span_id: str
    parent_id: str | None
    service: str
    operation: str
    duration_ms: float
    status: str
    attributes: dict[str, Any] | MappingProxyType[str, Any]  # converted to proxy in __post_init__
    events: EventTuple  # converted to proxies in __post_init__
    children: tuple["SpanNode", ...] = ()  # immutable for frozen dataclass

    def __post_init__(self) -> None:
        """Convert attributes and event dicts to read-only proxies."""
        if isinstance(self.attributes, dict):
            object.__setattr__(
                self, "attributes", MappingProxyType(self.attributes)
            )
        # Convert mutable dicts in events tuple to immutable proxies
        if self.events and any(isinstance(e, dict) for e in self.events):
            events_proxies = tuple(
                MappingProxyType(event) if isinstance(event, dict) else event
                for event in self.events
            )
            object.__setattr__(self, "events", events_proxies)


@dataclass(frozen=True)
class TraceTree:
    """A hierarchical view of spans in a single trace."""

    trace_id: str
    root_span: SpanNode
    error_spans: tuple[SpanNode, ...]  # immutable for frozen dataclass


@dataclass(frozen=True)
class SpanSummary:
    """A lightweight span returned from a span search.

    Returned by TelemetryPort.search_spans(). Contains enough context to
    identify the span and decide whether to fetch the full TraceTree.
    """

    trace_id: str
    span_id: str
    service: str
    operation: str
    duration_ms: float
    has_error: bool
    timestamp: datetime
    attributes: dict[str, Any] | MappingProxyType[str, Any]

    def __post_init__(self) -> None:
        """Convert attributes dict to read-only proxy."""
        if isinstance(self.attributes, dict):
            object.__setattr__(
                self, "attributes", MappingProxyType(self.attributes)
            )


@dataclass(frozen=True)
class LogEntry:
    """A single log entry from telemetry."""

    timestamp: datetime
    severity: Severity
    body: str
    attributes: dict[str, Any] | MappingProxyType[str, Any]  # converted to proxy in __post_init__
    trace_id: str | None
    span_id: str | None

    def __post_init__(self) -> None:
        """Convert attributes dict to read-only proxy."""
        if isinstance(self.attributes, dict):
            object.__setattr__(
                self, "attributes", MappingProxyType(self.attributes)
            )


@dataclass(frozen=True)
class PartialResultsInfo:
    """Metadata about partial results returned from telemetry queries.

    Telemetry adapters may return partial results when:
    - Query timeout occurs before all results are retrieved
    - Result set exceeds maximum size limits
    - API rate limits are encountered

    This model provides structured indication to callers that the
    returned results may be incomplete.
    """

    total_requested: int  # Number of results requested
    total_returned: int  # Number of results actually returned
    is_partial: bool  # Whether results were truncated
    reason: str | None = None  # Optional explanation for partial results


@dataclass(frozen=True)
class InvestigationContext:
    """Everything the diagnosis engine needs to analyze a signature.

    Assembled by the core, passed to the diagnosis port.

    WARNING: While this dataclass is frozen (immutable), the contained Signature
    object is mutable. The Signature may be modified externally (e.g., by the
    investigator after context assembly for status transitions). Callers should
    not rely on the Signature state remaining constant after context creation.
    """

    signature: Signature
    recent_events: tuple[ErrorEvent, ...]  # immutable for frozen dataclass
    trace_data: tuple[TraceTree, ...]  # immutable for frozen dataclass
    related_logs: tuple[LogEntry, ...]  # immutable for frozen dataclass
    codebase_path: str
    historical_context: tuple[Signature, ...]  # immutable for frozen dataclass


@dataclass(frozen=True)
class PollResult:
    """Summary of a poll cycle execution."""

    errors_found: int
    new_signatures: int
    updated_signatures: int
    investigations_queued: int
    timestamp: datetime
    errors_failed_to_process: int = 0  # Number of errors that failed during processing


@dataclass(frozen=True)
class InvestigationResult:
    """Summary of an investigation cycle execution."""

    diagnoses_produced: tuple[Diagnosis, ...]  # Successfully completed diagnoses
    investigations_attempted: int  # Number of signatures attempted
    investigations_failed: int = 0  # Number of investigations that failed


@dataclass(frozen=True)
class ResolutionResult:
    """Summary of a resolution-detection cycle execution.

    Default implementation of PollPort.execute_resolution_cycle() returns
    zero counts; implementations that support auto-close override it to
    compare Signature.last_seen against its resolution_threshold_hours.
    """

    signatures_resolved: int
    timestamp: datetime


@dataclass(frozen=True)
class StoreStats:
    """Statistics about the signature store."""

    total_signatures: int
    by_status: Mapping[str, int]  # status -> count (immutable at runtime)
    by_service: Mapping[str, int]  # service -> count (immutable at runtime)
    oldest_signature_age_hours: float | None  # None if no signatures
    avg_occurrence_count: float

    def __post_init__(self) -> None:
        """Convert mutable dicts to immutable proxies.

        Checks isinstance() before wrapping to prevent double-wrapping when
        deserializing or reconstructing StoreStats instances.
        """
        if not isinstance(self.by_status, MappingProxyType):
            object.__setattr__(self, "by_status", MappingProxyType(self.by_status))
        if not isinstance(self.by_service, MappingProxyType):
            object.__setattr__(self, "by_service", MappingProxyType(self.by_service))


@dataclass(frozen=True)
class TraceInvestigation:
    """Result of an ad-hoc trace investigation (code flow analysis).

    Produced by DiagnosisPort.investigate_trace() in response to a
    user-initiated `investigate-trace` command. Focused on explaining
    what the trace *does*, not diagnosing an error.
    """

    trace_id: str
    summary: str  # one-paragraph explanation of the request's end-to-end behaviour
    code_flow: tuple[str, ...]  # ordered steps describing the call chain
    services_involved: tuple[str, ...]
    key_findings: tuple[str, ...]  # notable observations (perf, patterns, issues)
    model: str
    cost_usd: float
    investigated_at: datetime

    def __post_init__(self) -> None:
        """Validate invariants on creation."""
        if self.cost_usd < 0:
            raise ValueError(
                f"cost_usd must be non-negative, got {self.cost_usd}"
            )


@dataclass(frozen=True)
class SignatureDetails:
    """Detailed information about a signature.

    WARNING: While this dataclass is frozen (immutable), the contained Signature
    object is mutable. The Signature may be modified externally after this object
    is created. Callers should not rely on the Signature state remaining constant.
    """

    signature: Signature
    recent_events: tuple[ErrorEvent, ...]  # Recent occurrences
    related_signatures: tuple[Signature, ...]  # Similar errors
