# Type Design Analysis: Rounds Diagnostic System

**Analysis Date**: February 12, 2026
**Branch Analyzed**: `feature/issue-1-sketch-out-the-project-archite` vs `main`
**Focus**: Invariant strength, encapsulation, and type safety

---

## Executive Summary

This PR introduces a comprehensive diagnostic system with 9 new domain models, 6 port interfaces, and 3 core services. **Overall Assessment: Strong type design with excellent invariant expression and good encapsulation, with a few opportunities for improvement.**

The type system demonstrates:
- **Strong**: Immutability, frozen dataclasses, compile-time guarantees
- **Strong**: Invariant validation in constructors (`__post_init__`)
- **Good**: Port abstraction separating concerns
- **Concern**: Mutable `Signature` type creates risk for invariant violation

---

## Type Analyses

### 1. Type: `ErrorEvent`
**File**: `/workspace/rounds/core/models.py` (lines 36-58)

#### Invariants Identified
- `attributes` must be read-only (immutable proxy)
- All fields are permanent once created (frozen)
- Stack frames cannot be empty in practice
- `span_id` and `trace_id` must be non-empty strings
- Severity must be a valid OpenTelemetry level

#### Ratings
- **Encapsulation**: 9/10
  Frozen dataclass + `MappingProxyType` for attributes prevent any mutation. Clear immutability contract. Minor issue: no validation in `__post_init__` that span_ids/trace_ids are non-empty.

- **Invariant Expression**: 9/10
  Type structure clearly shows immutability through `frozen=True` and `MappingProxyType`. The design is self-documenting. Severity enum provides compile-time safety over strings.

- **Invariant Usefulness**: 8/10
  Prevents accidental mutation of telemetry data after normalization. Useful for ensuring data integrity across service boundaries. Stack frames being tuples is practical but could be empty (unlikely in practice but theoretically possible).

- **Invariant Enforcement**: 8/10
  Frozen dataclass enforces immutability at runtime. `__post_init__` converts dict to read-only proxy. **Gap**: No validation that non-empty strings are provided for trace_id/span_id, or that stack_frames is non-empty.

#### Strengths
- Immutable by default via `frozen=True`
- MappingProxyType correctly prevents attribute dict modification
- Enum usage for Severity prevents invalid values
- Tuple for stack_frames ensures frame sequence can't be modified
- Clear separation of concerns (telemetry data container)

#### Concerns
- No validation of non-empty trace_id/span_id strings in constructor
- No validation that stack_frames contains at least one frame when expected
- `attributes` dict could theoretically contain None values or unexpected keys

#### Recommended Improvements
```python
@dataclass(frozen=True)
class ErrorEvent:
    """A single error occurrence from telemetry."""

    trace_id: str
    span_id: str
    service: str
    error_type: str
    error_message: str
    stack_frames: tuple[StackFrame, ...]
    timestamp: datetime
    attributes: MappingProxyType
    severity: Severity

    def __post_init__(self) -> None:
        """Validate invariants and convert attributes to read-only."""
        # Validate non-empty identifiers
        if not self.trace_id or not self.trace_id.strip():
            raise ValueError("trace_id cannot be empty")
        if not self.span_id or not self.span_id.strip():
            raise ValueError("span_id cannot be empty")

        # Convert attributes dict to read-only proxy
        if isinstance(self.attributes, dict):
            object.__setattr__(
                self, "attributes", MappingProxyType(self.attributes)
            )
```

---

### 2. Type: `Signature`
**File**: `/workspace/rounds/core/models.py` (lines 92-127)

#### Invariants Identified
- `occurrence_count` must be >= 1 (at least one occurrence to exist)
- `last_seen` must be >= `first_seen` (temporal ordering)
- `tags` must be immutable (frozenset)
- Status transitions follow a state machine (NEW → INVESTIGATING → DIAGNOSED/MUTED/RESOLVED)
- Diagnosis can only be non-None when status is DIAGNOSED
- ID must be unique across system
- Fingerprint must be stable and unique

#### Ratings
- **Encapsulation**: 6/10
  **MAJOR CONCERN**: Type is mutable (not frozen). Mutable dataclass creates high risk of invariant violation. Code modifies status, diagnosis, last_seen directly (see `/workspace/rounds/core/management_service.py` lines 51-52, 83-84). No setter validation. External code can corrupt state.

- **Invariant Expression**: 7/10
  `__post_init__` validates two key invariants (occurrence_count >= 1, last_seen >= first_seen). Good use of frozenset for tags. Status enum provides compile-time safety. **Gap**: No expression of state machine constraints or diagnosis-status relationship. Nothing prevents setting diagnosis on a NEW signature.

- **Invariant Usefulness**: 9/10
  Invariants directly prevent corruption: occurrence_count < 1 would break fingerprinting logic, last_seen < first_seen breaks causality. Validation at construction prevents bad deserialization. Highly useful for data integrity.

- **Invariant Enforcement**: 5/10
  **CRITICAL GAP**: Validation only at construction time. After creation, any field can be modified without validation. Status transitions not enforced - can set any status at any time. Diagnosis can be assigned to any status. `last_seen` can be set backwards after construction.

#### Strengths
- Constructor validation catches deserialization errors
- Frozenset for tags prevents modification
- Status enum provides type safety
- Clear invariants documented in docstring
- Uses proper datetime objects

#### Concerns
- **Mutable type violates encapsulation principle**: All fields are directly mutable after creation
- **No state machine enforcement**: Can transition from RESOLVED → NEW without constraint
- **Diagnosis-status relationship not enforced**: Nothing prevents setting diagnosis on NEW or MUTED signatures
- **last_seen can be corrupted**: Can be set backwards after construction despite `__post_init__` check
- **Direct field mutation in services**: `management_service.py` lines 51-52 directly modify `signature.status`
- **Mutation happens silently**: No audit trail or logging of state changes through the type itself

#### Recommended Improvements
The most pragmatic fix without breaking existing code:

**Option 1 (Conservative - Add Validation Methods)**
```python
@dataclass
class Signature:
    """A fingerprinted failure pattern."""

    id: str
    fingerprint: str
    error_type: str
    service: str
    message_template: str
    stack_hash: str
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    status: SignatureStatus
    diagnosis: Diagnosis | None = None
    tags: frozenset[str] = field(default_factory=frozenset)

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

    # Add validation methods for mutable operations
    def set_status(self, new_status: SignatureStatus) -> None:
        """Set status with validation."""
        # Prevent invalid transitions
        if self.status == SignatureStatus.RESOLVED:
            raise ValueError(
                f"Cannot change status from RESOLVED, current: {self.status}"
            )
        self.status = new_status

    def set_diagnosis(self, diagnosis: Diagnosis) -> None:
        """Set diagnosis with validation."""
        if self.status not in {SignatureStatus.INVESTIGATING, SignatureStatus.DIAGNOSED}:
            raise ValueError(
                f"Can only set diagnosis on INVESTIGATING/DIAGNOSED signatures, "
                f"got: {self.status}"
            )
        self.diagnosis = diagnosis

    def update_occurrence(self, new_timestamp: datetime) -> None:
        """Update occurrence count and last_seen with validation."""
        if new_timestamp < self.last_seen:
            raise ValueError(
                f"new_timestamp ({new_timestamp}) cannot be before "
                f"last_seen ({self.last_seen})"
            )
        self.occurrence_count += 1
        self.last_seen = new_timestamp
```

**Option 2 (Comprehensive - Frozen Type with Builder)**
```python
@dataclass(frozen=True)
class Signature:
    """A fingerprinted failure pattern (immutable)."""

    id: str
    fingerprint: str
    error_type: str
    service: str
    message_template: str
    stack_hash: str
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    status: SignatureStatus
    diagnosis: Diagnosis | None = None
    tags: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Validate invariants."""
        if self.occurrence_count < 1:
            raise ValueError(...)
        if self.last_seen < self.first_seen:
            raise ValueError(...)

    def with_status(self, status: SignatureStatus) -> "Signature":
        """Return new Signature with updated status."""
        return replace(self, status=status)

    def with_diagnosis(self, diagnosis: Diagnosis) -> "Signature":
        """Return new Signature with diagnosis and DIAGNOSED status."""
        return replace(self, diagnosis=diagnosis, status=SignatureStatus.DIAGNOSED)

    def with_occurrence(self, timestamp: datetime) -> "Signature":
        """Return new Signature with incremented count."""
        if timestamp < self.last_seen:
            raise ValueError(...)
        return replace(
            self,
            occurrence_count=self.occurrence_count + 1,
            last_seen=timestamp
        )
```

Currently recommending **Option 1** as it maintains backward compatibility while improving invariant enforcement.

---

### 3. Type: `Diagnosis`
**File**: `/workspace/rounds/core/models.py` (lines 79-90)

#### Invariants Identified
- All fields immutable once created (frozen)
- confidence must be valid Confidence enum value
- cost_usd must be non-negative
- root_cause and suggested_fix must be non-empty strings
- evidence tuple cannot be modified

#### Ratings
- **Encapsulation**: 9/10
  Frozen dataclass prevents any mutation. Diagnosis is a pure value type representing immutable analysis results. No escape hatches for modification.

- **Invariant Expression**: 8/10
  Frozen type clearly expresses immutability. Confidence enum prevents invalid values. **Gap**: No validation that cost_usd >= 0 or that root_cause/suggested_fix are non-empty.

- **Invariant Usefulness**: 9/10
  Immutability ensures diagnosis results cannot be accidentally corrupted. Useful when diagnosis is used across multiple services and threads.

- **Invariant Enforcement**: 7/10
  Frozen dataclass enforces immutability. **Gap**: No constructor validation for non-negative cost or non-empty strings. Could create Diagnosis with cost_usd=-1.50 (invalid).

#### Strengths
- Perfect immutability through frozen dataclass
- Enum for confidence prevents invalid confidence values
- Tuple for evidence prevents modification
- Clean value type semantics
- Appropriate for passing between services

#### Concerns
- No validation that cost_usd >= 0 (could be negative)
- No validation that root_cause/suggested_fix are non-empty
- No validation that evidence tuple is non-empty

#### Recommended Improvements
```python
@dataclass(frozen=True)
class Diagnosis:
    """LLM-generated root cause analysis for a signature."""

    root_cause: str
    evidence: tuple[str, ...]
    suggested_fix: str
    confidence: Confidence
    diagnosed_at: datetime
    model: str
    cost_usd: float

    def __post_init__(self) -> None:
        """Validate diagnosis invariants."""
        if not self.root_cause or not self.root_cause.strip():
            raise ValueError("root_cause cannot be empty")
        if not self.suggested_fix or not self.suggested_fix.strip():
            raise ValueError("suggested_fix cannot be empty")
        if not self.evidence:
            raise ValueError("evidence tuple cannot be empty")
        if self.cost_usd < 0:
            raise ValueError(f"cost_usd must be non-negative, got {self.cost_usd}")
```

---

### 4. Type: `StackFrame`
**File**: `/workspace/rounds/core/models.py` (lines 14-21)

#### Invariants Identified
- All fields immutable (frozen)
- `lineno` is optional (can be None)
- module, function, filename should be non-empty strings

#### Ratings
- **Encapsulation**: 9/10
  Frozen dataclass, simple structure, no escape hatches.

- **Invariant Expression**: 8/10
  Immutability clear from frozen=True. Optional lineno expressed via union. **Gap**: No validation for empty strings.

- **Invariant Usefulness**: 7/10
  Prevents accidental modification of stack frames in tuples. Less critical than other types since frames are typically read-only after construction.

- **Invariant Enforcement**: 6/10
  Frozen prevents mutation, but no constructor validation. Could create StackFrame with module="" (invalid).

#### Strengths
- Immutable by design
- Simple, focused type
- Correct use of Optional for lineno

#### Concerns
- No validation of non-empty strings for module/function/filename
- Comment about lineno being "present but ignored in fingerprinting" suggests possible design smell

#### Recommended Improvements
```python
@dataclass(frozen=True)
class StackFrame:
    """A single frame in a stack trace."""

    module: str
    function: str
    filename: str
    lineno: int | None

    def __post_init__(self) -> None:
        """Validate stack frame invariants."""
        if not self.module or not self.module.strip():
            raise ValueError("module cannot be empty")
        if not self.function or not self.function.strip():
            raise ValueError("function cannot be empty")
        if not self.filename or not self.filename.strip():
            raise ValueError("filename cannot be empty")
```

---

### 5. Type: `InvestigationContext`
**File**: `/workspace/rounds/core/models.py` (lines 179-192)

#### Invariants Identified
- All fields immutable (frozen)
- All tuple fields cannot be modified
- `codebase_path` should be non-empty
- Tuples can be empty (graceful degradation)

#### Ratings
- **Encapsulation**: 9/10
  Frozen dataclass with all tuples prevents any mutation. Clear separation of concern - pure data container.

- **Invariant Expression**: 8/10
  Immutability clearly expressed. Tuple types signal immutability. **Gap**: No validation that codebase_path is non-empty.

- **Invariant Usefulness**: 8/10
  Prevents accidental corruption of investigation context. Useful when context is passed through multiple services and ports.

- **Invariant Enforcement**: 8/10
  Frozen prevents mutation. **Gap**: No validation of codebase_path non-emptiness.

#### Strengths
- Perfect immutability for data-heavy type
- All collections properly immutable (tuples)
- Graceful handling of empty tuples (optional evidence)

#### Concerns
- No validation that codebase_path is non-empty string
- No validation that signature is not None (though type system prevents this)

#### Recommended Improvements
```python
def __post_init__(self) -> None:
    """Validate investigation context invariants."""
    if not self.codebase_path or not self.codebase_path.strip():
        raise ValueError("codebase_path cannot be empty")
```

---

### 6. Type: `SpanNode`
**File**: `/workspace/rounds/core/models.py` (lines 129-149)

#### Invariants Identified
- All fields immutable (frozen)
- `parent_id` optional (may be None for root spans)
- Attributes must be read-only (MappingProxyType)
- Children are immutable (tuple)
- duration_ms should be non-negative

#### Ratings
- **Encapsulation**: 9/10
  Frozen + MappingProxyType for attributes. Clear immutability contract.

- **Invariant Expression**: 8/10
  Immutability clear. Optional parent_id signals root span possibility. **Gap**: No validation that duration_ms >= 0.

- **Invariant Usefulness**: 8/10
  Prevents modification of distributed trace structures. Useful when building trace trees.

- **Invariant Enforcement**: 7/10
  Frozen + MappingProxyType enforce immutability. **Gap**: No validation of duration_ms >= 0 or non-empty span_id/service/operation.

#### Strengths
- Perfect immutability via frozen + MappingProxyType
- Correct use of Optional for root span detection
- Tuple for children prevents modification

#### Concerns
- No validation that duration_ms >= 0
- No validation of non-empty span_id, service, operation

#### Recommended Improvements
Add validation in `__post_init__`:
```python
def __post_init__(self) -> None:
    """Convert attributes dict to read-only proxy and validate."""
    if isinstance(self.attributes, dict):
        object.__setattr__(
            self, "attributes", MappingProxyType(self.attributes)
        )

    # Validate invariants
    if not self.span_id or not self.span_id.strip():
        raise ValueError("span_id cannot be empty")
    if self.duration_ms < 0:
        raise ValueError(f"duration_ms must be non-negative, got {self.duration_ms}")
```

---

### 7. Type: `TraceTree`
**File**: `/workspace/rounds/core/models.py` (lines 151-158)

#### Invariants Identified
- All fields immutable (frozen)
- error_spans is tuple (cannot be modified)
- root_span must be a valid SpanNode
- trace_id must match OpenTelemetry format

#### Ratings
- **Encapsulation**: 9/10
  Frozen dataclass prevents mutation. Simple container type.

- **Invariant Expression**: 8/10
  Immutability expressed via frozen. Tuple for error_spans signals immutability. **Gap**: No validation of trace_id format.

- **Invariant Usefulness**: 7/10
  Prevents modification of trace hierarchies. Less critical since contained SpanNodes are already immutable.

- **Invariant Enforcement**: 6/10
  Frozen prevents mutation, but no format validation. Could create TraceTree with trace_id="" or invalid format.

#### Strengths
- Immutable value type
- Clear structure
- Tuple for error_spans

#### Concerns
- No validation that trace_id is non-empty or properly formatted

---

### 8. Type: `LogEntry`
**File**: `/workspace/rounds/core/models.py` (lines 160-177)

#### Invariants Identified
- All fields immutable (frozen)
- Attributes must be read-only (MappingProxyType)
- trace_id and span_id are optional (for untraced logs)
- Severity must be valid enum value
- body should be non-empty string

#### Ratings
- **Encapsulation**: 9/10
  Frozen + MappingProxyType enforce full immutability.

- **Invariant Expression**: 8/10
  Immutability clear. Optional trace_id/span_id express possibility of untraced logs. **Gap**: No validation of non-empty body.

- **Invariant Usefulness**: 8/10
  Prevents log entries from being modified after creation.

- **Invariant Enforcement**: 7/10
  Frozen + MappingProxyType enforced. **Gap**: No validation that body is non-empty.

#### Strengths
- Perfect immutability
- Correct use of Optional
- Severity enum prevents invalid values

#### Concerns
- No validation that body is non-empty

---

### 9. Type: `PollResult`
**File**: `/workspace/rounds/core/models.py` (lines 194-203)

#### Invariants Identified
- All fields immutable (frozen)
- Counts must be non-negative (errors_found >= 0, etc.)
- timestamp represents when poll occurred
- All fields represent readonly summary

#### Ratings
- **Encapsulation**: 8/10
  Frozen prevents mutation, but no validation of count constraints.

- **Invariant Expression**: 7/10
  Immutability expressed. Field names suggest non-negative values, but no type constraint (using `int` instead of `PositiveInt`).

- **Invariant Usefulness**: 8/10
  Prevents mutation of poll summary. Useful for audit trails and reporting.

- **Invariant Enforcement**: 6/10
  Frozen prevents mutation, but no validation. Could create PollResult with errors_found=-5 (invalid).

#### Strengths
- Immutable result type
- Clear field semantics
- Timestamp for audit trail

#### Concerns
- No validation that counts are non-negative
- Using plain `int` instead of `PositiveInt` or similar

#### Recommended Improvements
```python
from pydantic import Field

@dataclass(frozen=True)
class PollResult:
    """Summary of a poll cycle execution."""

    errors_found: int
    new_signatures: int
    updated_signatures: int
    investigations_queued: int
    timestamp: datetime

    def __post_init__(self) -> None:
        """Validate poll result invariants."""
        if self.errors_found < 0:
            raise ValueError(f"errors_found must be non-negative, got {self.errors_found}")
        if self.new_signatures < 0:
            raise ValueError(f"new_signatures must be non-negative, got {self.new_signatures}")
        if self.updated_signatures < 0:
            raise ValueError(f"updated_signatures must be non-negative, got {self.updated_signatures}")
        if self.investigations_queued < 0:
            raise ValueError(f"investigations_queued must be non-negative, got {self.investigations_queued}")
```

---

## Port Interface Analysis

### Port: `TelemetryPort`
**File**: `/workspace/rounds/core/ports.py` (lines 41-148)

#### Invariants Identified
- Methods are async and may fail (exception handling required by caller)
- `get_recent_errors()` returns list in descending timestamp order
- `get_traces()` may omit traces that don't exist
- Results may be partial (graceful degradation documented)

#### Ratings
- **Encapsulation**: 9/10
  Clear abstraction boundary. Port defines contract without exposing adapter details. Methods return domain models, not raw telemetry structures.

- **Invariant Expression**: 8/10
  Docstrings clearly express invariants (ordering, partial results possible). **Gap**: Could use better type hints for guaranteed properties (e.g., "returns in descending order" could be enforced with OrderedResult type).

- **Invariant Usefulness**: 9/10
  Clearly documents that results may be partial and implementations should handle gracefully. Prevents assumptions about completeness.

- **Invariant Enforcement**: 7/10
  Docstrings document invariants, but not enforced by type system. Callers must trust documentation.

#### Strengths
- Clear separation from backend implementation
- Graceful degradation patterns documented
- Returns domain models, not raw telemetry
- Good error documentation
- Explicit about optional parameters

#### Concerns
- Ordering guarantee (descending timestamp) not enforced by type
- Partial results documentation could be stronger
- No type-level guarantee that returned objects are valid

---

### Port: `SignatureStorePort`
**File**: `/workspace/rounds/core/ports.py` (lines 150-253)

#### Invariants Identified
- `get_by_*` methods return Optional (None if not found)
- `save` and `update` are asymmetric (update assumes exists)
- Transactional updates expected to be atomic
- `get_stats()` returns untyped dict (opaque interface)

#### Ratings
- **Encapsulation**: 8/10
  Clear port boundary. Domain models used throughout. **Concern**: `get_stats()` returns `dict[str, Any]` which is an opaque escape hatch.

- **Invariant Expression**: 7/10
  Asymmetry between save/update is documented but not enforced. Could use stronger type distinction (SaveOp vs UpdateOp).

- **Invariant Usefulness**: 8/10
  Optional return types force callers to handle not-found case explicitly.

- **Invariant Enforcement**: 7/10
  Documented contracts, but not enforced by types. Adapter could violate contract silently.

#### Strengths
- Clear Optional semantics for queries
- Distinct save/update methods clarify intent
- Transaction support documented
- Get_similar enables related error grouping

#### Concerns
- `get_stats()` returns opaque `dict[str, Any]` (type-unsafe escape hatch)
- No type distinction for uniqueness constraints (save assumes new, update assumes exists)
- Potential for race conditions between get_by_fingerprint + save not documented

---

### Port: `DiagnosisPort`
**File**: `/workspace/rounds/core/ports.py` (lines 255-305)

#### Invariants Identified
- `diagnose()` and `estimate_cost()` take same context
- Cost estimation should be cheaper than actual diagnosis
- May raise exceptions (cost exceeded, backend unavailable)

#### Ratings
- **Encapsulation**: 8/10
  Returns domain Diagnosis model. Clear boundary.

- **Invariant Expression**: 7/10
  Relationship between estimate and actual cost not expressed. No type guarantee that estimate <= actual cost.

- **Invariant Usefulness**: 8/10
  Cost estimation method allows budget enforcement before expensive diagnosis.

- **Invariant Enforcement**: 5/10
  No type constraint that estimate <= actual cost. Adapter could violate this.

#### Strengths
- Clear separation of estimation from diagnosis
- Budget enforcement pre-diagnosis
- Takes full investigation context for analysis

#### Concerns
- No type-level guarantee for cost estimation accuracy
- Cost budget enforcement is caller's responsibility (could be forgotten)

---

### Port: `NotificationPort`
**File**: `/workspace/rounds/core/ports.py` (lines 307-347)

#### Invariants Identified
- Two methods: report (single diagnosis) and report_summary (aggregate)
- Either method may fail and should be retried
- No return value (fire-and-forget semantics)

#### Ratings
- **Encapsulation**: 9/10
  Clean abstraction. Takes domain models. No escape hatches.

- **Invariant Expression**: 8/10
  Two distinct methods for single vs batch. Clear intent. **Gap**: No type expressing "must notify if confidence > threshold".

- **Invariant Usefulness**: 8/10
  Separate channels for immediate and periodic reporting.

- **Invariant Enforcement**: 8/10
  Port contracts clear. Callers responsible for retry logic.

#### Strengths
- Simple, focused interface
- Dual reporting modes (immediate + summary)
- Fire-and-forget allows non-blocking notification

---

### Port: `PollPort` (Driving Port)
**File**: `/workspace/rounds/core/ports.py` (lines 354-411)

#### Invariants Identified
- Both methods may return partial results on transient failures
- `execute_poll_cycle()` returns PollResult summary
- `execute_investigation_cycle()` returns list of Diagnosis
- Only fatal errors should raise exceptions (transient errors logged)

#### Ratings
- **Encapsulation**: 8/10
  Clear contract for polling behavior. Graceful degradation documented.

- **Invariant Expression**: 7/10
  Documentation clear, but "fatal vs transient" distinction not type-enforced. Caller must trust adapter respects this.

- **Invariant Usefulness**: 9/10
  Return types guide behavior - PollResult summarizes what happened, allowing partial success reporting.

- **Invariant Enforcement**: 6/10
  Documented contract, but not enforced. Adapter could raise on transient errors.

#### Strengths
- Clear return types for results vs errors
- PollResult provides visibility into what was processed
- Graceful degradation documented

#### Concerns
- "Only raise on fatal errors" is advisory, not enforced
- Partial success semantics could be stricter

---

### Port: `ManagementPort` (Driving Port)
**File**: `/workspace/rounds/core/ports.py` (lines 413-484)

#### Invariants Identified
- All methods take signature IDs (strings)
- Methods raise ValueError if signature not found
- `get_signature_details()` returns untyped dict (opaque)
- State transitions: MUTED, RESOLVED, NEW
- State changes should be idempotent

#### Ratings
- **Encapsulation**: 7/10
  **Concern**: `get_signature_details()` returns `dict[str, Any]` which is opaque and type-unsafe. Should return structured type.

- **Invariant Expression**: 6/10
  Valid operations per status not type-expressed. Can call resolve_signature() on already-resolved signature. retriage_signature() clears diagnosis but type doesn't express this.

- **Invariant Usefulness**: 8/10
  Methods align with business operations (mute, resolve, retriage).

- **Invariant Enforcement**: 5/10
  **Critical gap**: No type-level enforcement of valid state transitions. Adapter could allow invalid transitions.

#### Strengths
- Clear method names for operations
- ValueError for not-found cases
- Optional metadata (reason, fix_applied) for audit

#### Concerns
- **`get_signature_details()` returns `dict[str, Any]` (type-unsafe)**
- No type constraint on valid state transitions
- No type guarantee that retriage clears diagnosis
- No type constraint on idempotency

#### Recommended Improvements
Define a return type for get_signature_details:
```python
@dataclass(frozen=True)
class SignatureDetails:
    """Full details about a signature."""

    id: str
    fingerprint: str
    error_type: str
    service: str
    message_template: str
    stack_hash: str
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    status: SignatureStatus
    tags: frozenset[str]
    diagnosis: Diagnosis | None
    related_signatures: list["SignatureDetails"]  # Would need recursive typing

class ManagementPort(ABC):
    @abstractmethod
    async def get_signature_details(
        self, signature_id: str
    ) -> SignatureDetails:  # Typed return
        """Retrieve detailed information about a signature."""
```

---

## Cross-Cutting Type Design Observations

### 1. Immutability Strategy
**Status**: Mixed, leaning good

- **Frozen types** (ErrorEvent, Diagnosis, StackFrame, SpanNode, TraceTree, LogEntry, PollResult, InvestigationContext): Excellent immutability guarantee
- **Mutable Signature type**: High risk, violates encapsulation principle
- **Enum types** (Severity, SignatureStatus, Confidence): Excellent, compile-time safety

**Recommendation**: Strongly consider Option 1 or 2 for Signature (adding validation methods or converting to frozen with builders).

### 2. Validation Strategy
**Status**: Incomplete

- **Strong**: Constructor validation in ErrorEvent, Signature (2 invariants), all other types
- **Gaps**: No validation of string non-emptiness, non-negative numerics, format constraints
- **Pattern needed**: All types should validate their invariants in `__post_init__`

### 3. Port Design Quality
**Status**: Good with opportunities

- **Strong**: Clear separation of driven vs driving ports, domain models as arguments/returns
- **Gaps**: Some ports return opaque `dict[str, Any]` (SignatureStorePort.get_stats, ManagementPort.get_signature_details)
- **Recommendation**: Define structured return types instead of dicts

### 4. State Machine Representation
**Status**: Weak

- **Current**: Enum for status, informal state transitions
- **Gap**: No type-level representation of valid transitions
- **Issue**: Signature can be set to any status from any status
- **Recommendation**: Consider state pattern or explicit transition methods (with validation)

### 5. Error Handling
**Status**: Good documentation, incomplete type safety

- **Strong**: Ports document exception behavior clearly
- **Gap**: Type system doesn't express which exceptions are recoverable
- **Recommendation**: Consider custom exception types (RetryableError vs FatalError)

---

## Critical Issues Summary

| Issue | Severity | Type(s) | Impact |
|-------|----------|---------|--------|
| Signature is mutable, violates encapsulation | **CRITICAL** | Signature | Invariants can be violated after construction |
| No validation of string non-emptiness | High | ErrorEvent, StackFrame, SpanNode, etc. | Invalid objects possible (empty service names, etc.) |
| ManagementPort.get_signature_details returns dict[str, Any] | High | ManagementPort | Type-unsafe, no compile-time verification |
| Signature status transitions not enforced | High | Signature | Can transition from RESOLVED to NEW |
| Diagnosis-status relationship not enforced | High | Signature | Can assign diagnosis to MUTED signature |
| SignatureStorePort.get_stats returns dict[str, Any] | Medium | SignatureStorePort | Opaque interface, type-unsafe |
| No validation of non-negative numerics | Medium | PollResult, Diagnosis, SpanNode | Invalid objects possible (negative costs, durations) |
| Cost estimation accuracy not type-expressed | Medium | DiagnosisPort | No guarantee estimate <= actual cost |

---

## Recommendations by Priority

### Priority 1 (Must Address)
1. **Fix Signature mutability**: Convert to frozen dataclass with builder methods OR add validation methods to prevent invariant violation. Currently high risk of data corruption.
2. **Add comprehensive constructor validation**: All types should validate non-empty strings, non-negative values, and format constraints in `__post_init__`.

### Priority 2 (Should Address)
3. **Replace opaque dicts with typed returns**: Define SignatureDetails and StoreStats types instead of `dict[str, Any]`.
4. **Enforce state machine constraints**: Add validation methods to Signature for valid status transitions.
5. **Express cost estimation accuracy**: Either document the guarantee or enforce type-level constraint.

### Priority 3 (Nice to Have)
6. **Use custom exception types**: Define RetryableError, FatalError for better error handling type safety.
7. **Add audit trail type**: Track who made state changes and when (optional metadata fields).
8. **Consider OrderedResult type**: Enforce "results in descending timestamp order" at type level.

---

## Overall Assessment

**Type Design Quality: 7.5/10**

### Summary
This PR demonstrates thoughtful type design with:
- Strong immutability discipline for most types
- Good port abstraction and separation of concerns
- Comprehensive invariant documentation

However, it has notable gaps:
- **Critical**: Mutable Signature type creates high risk of invariant violation
- **High**: Incomplete constructor validation (many types accept invalid values)
- **High**: Opaque `dict` returns in key ports lose type safety benefits
- **Medium**: State machine constraints not enforced

The codebase shows architectural maturity but needs refinement in invariant enforcement. With the Priority 1 and Priority 2 improvements, this would reach 8.5-9.0/10 quality.

### Strengths
✓ Excellent use of frozen dataclasses for immutability
✓ Good enum usage for constrained values
✓ Clear port abstraction
✓ Domain models throughout (not raw telemetry)
✓ Thoughtful error handling documentation

### Risks
✗ Signature mutability allows invariant violations
✗ Many types accept invalid constructor arguments
✗ Opaque dict returns lose type safety
✗ State transitions not constrained
✗ No audit trail for mutations

