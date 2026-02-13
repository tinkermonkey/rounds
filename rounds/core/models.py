"""Domain models for the Rounds diagnostic system.

All models in this module use only Python standard library types,
ensuring zero external dependencies in the core domain.
"""

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


class Severity(Enum):
    """Log severity levels from OpenTelemetry."""

    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"


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
    attributes: MappingProxyType[str, Any]  # read-only dict proxy for immutability
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

    def __post_init__(self) -> None:
        """Validate diagnosis invariants on creation."""
        if self.cost_usd < 0:
            raise ValueError(
                f"cost_usd must be non-negative, got {self.cost_usd}"
            )


@dataclass
class Signature:
    """A fingerprinted failure pattern.

    Represents a class of errors, not a single occurrence.
    Tracks lifecycle, occurrence count, and optional diagnosis.

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

    def mark_investigating(self) -> None:
        """Transition signature to investigating status."""
        if self.status not in {SignatureStatus.NEW, SignatureStatus.INVESTIGATING}:
            raise ValueError(
                f"Cannot investigate signature in {self.status} status"
            )
        self.status = SignatureStatus.INVESTIGATING

    def mark_diagnosed(self, diagnosis: Diagnosis) -> None:
        """Transition signature to diagnosed status with diagnosis."""
        self.diagnosis = diagnosis
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

    def record_occurrence(self, timestamp: datetime) -> None:
        """Record a new occurrence and update last_seen."""
        if timestamp < self.first_seen:
            raise ValueError(
                f"Occurrence timestamp {timestamp} cannot be before first_seen {self.first_seen}"
            )
        self.occurrence_count += 1
        self.last_seen = timestamp

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


@dataclass(frozen=True)
class SpanNode:
    """A single span in a distributed trace."""

    span_id: str
    parent_id: str | None
    service: str
    operation: str
    duration_ms: float
    status: str
    attributes: MappingProxyType[str, Any]  # read-only dict proxy for immutability
    events: tuple[MappingProxyType[str, Any], ...]  # read-only event dicts
    children: tuple["SpanNode", ...] = ()  # immutable for frozen dataclass

    def __post_init__(self) -> None:
        """Convert attributes and event dicts to read-only proxies."""
        if isinstance(self.attributes, dict):
            object.__setattr__(
                self, "attributes", MappingProxyType(self.attributes)
            )
        # Convert mutable dicts in events tuple to immutable proxies
        if self.events and isinstance(self.events[0], dict):
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
class LogEntry:
    """A single log entry from telemetry."""

    timestamp: datetime
    severity: Severity
    body: str
    attributes: MappingProxyType[str, Any]  # read-only dict proxy for immutability
    trace_id: str | None
    span_id: str | None

    def __post_init__(self) -> None:
        """Convert attributes dict to read-only proxy."""
        if isinstance(self.attributes, dict):
            object.__setattr__(
                self, "attributes", MappingProxyType(self.attributes)
            )


@dataclass(frozen=True)
class InvestigationContext:
    """Everything the diagnosis engine needs to analyze a signature.

    Assembled by the core, passed to the diagnosis port.
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
class StoreStats:
    """Statistics about the signature store."""

    total_signatures: int
    by_status: MappingProxyType[str, int]  # status -> count (read-only)
    by_service: MappingProxyType[str, int]  # service -> count (read-only)
    oldest_signature_age_hours: float | None  # None if no signatures
    avg_occurrence_count: float

    def __post_init__(self) -> None:
        """Convert dicts to read-only proxies."""
        if isinstance(self.by_status, dict):
            object.__setattr__(self, "by_status", MappingProxyType(self.by_status))
        if isinstance(self.by_service, dict):
            object.__setattr__(self, "by_service", MappingProxyType(self.by_service))


@dataclass(frozen=True)
class SignatureDetails:
    """Detailed information about a signature."""

    signature: Signature
    recent_events: tuple[ErrorEvent, ...]  # Recent occurrences
    related_signatures: tuple[Signature, ...]  # Similar errors
