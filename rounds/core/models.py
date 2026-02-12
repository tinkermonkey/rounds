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

    module: str  # e.g. "app.services.payment"
    function: str  # e.g. "process_charge"
    filename: str  # e.g. "payment.py"
    lineno: int | None  # present but ignored in fingerprinting


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
    attributes: MappingProxyType  # read-only dict proxy for immutability
    severity: Severity

    def __post_init__(self) -> None:
        """Convert attributes dict to read-only proxy."""
        if isinstance(self.attributes, dict):
            object.__setattr__(
                self, "attributes", MappingProxyType(self.attributes)
            )


class SignatureStatus(Enum):
    """Lifecycle states for a failure signature."""

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


@dataclass(frozen=True)
class SpanNode:
    """A single span in a distributed trace."""

    span_id: str
    parent_id: str | None
    service: str
    operation: str
    duration_ms: float
    status: str
    attributes: MappingProxyType  # read-only dict proxy for immutability
    events: tuple[dict[str, Any], ...]  # immutable for frozen dataclass
    children: tuple["SpanNode", ...] = ()  # immutable for frozen dataclass

    def __post_init__(self) -> None:
        """Convert attributes dict to read-only proxy."""
        if isinstance(self.attributes, dict):
            object.__setattr__(
                self, "attributes", MappingProxyType(self.attributes)
            )


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
    attributes: MappingProxyType  # read-only dict proxy for immutability
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
