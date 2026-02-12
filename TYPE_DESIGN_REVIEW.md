# Type Design Review: Rounds Core Domain Layer

**Date**: February 12, 2026
**Reviewed Files**: `rounds/core/models.py`, `rounds/core/ports.py`, `rounds/config.py`
**Architecture**: Hexagonal (Ports & Adapters)

---

## Executive Summary

The Rounds type system demonstrates **strong foundational design** with effective use of immutability, type aliases, and port abstraction. The core domain is properly isolated and well-encapsulated. However, there is one **critical invariant violation** in the `Signature` class that creates a risk of inconsistent state during mutation, and several **moderate improvements** that would strengthen invariant enforcement.

**Overall Assessment**: Production-ready with recommended improvements to reduce mutation-related bugs.

---

## Detailed Type Analysis

### 1. Type: `Signature`

**File**: `/workspace/rounds/core/models.py` (lines 94-163)

#### Invariants Identified

1. **Occurrence Count Invariant**: `occurrence_count >= 1`
   - A signature represents at least one error occurrence
   - Enforced: `__post_init__` ✓

2. **Temporal Ordering Invariant**: `last_seen >= first_seen`
   - Errors cannot be recorded before they were first seen
   - Enforced: `__post_init__` ✓

3. **Status Transition Invariant**: Valid state machine
   - `NEW` → `INVESTIGATING` → `DIAGNOSED` (or `MUTED`/`RESOLVED`)
   - `MUTED` and `RESOLVED` are terminal states
   - Partially enforced: Methods check entry conditions but not all transitions ⚠

4. **Occurrence Timestamp Ordering**: New occurrences cannot be earlier than `first_seen`
   - Enforced: `record_occurrence()` ✓

5. **Diagnosis Consistency**: If `diagnosis` is not None, status should be `DIAGNOSED`
   - **NOT ENFORCED** ✗ Critical Gap

#### Ratings

- **Encapsulation**: 6/10
  ```
  Justification: The class is mutable (intentional) with public setter methods for state
  transitions. However, the class is missing a frozen variant for immutable use cases,
  and it exposes direct field mutation (e.g., "signature.status = ...") in poll_service.py
  line 113-114, which bypasses method-based transitions and can violate invariants.
  ```

- **Invariant Expression**: 7/10
  ```
  Justification: Occurrence count and temporal ordering are clearly enforced through
  __post_init__ and record_occurrence(). Status transitions are partially expressed
  through methods, but the class design allows direct field mutation that violates
  the transition state machine. The diagnosis-status consistency invariant is invisible.
  ```

- **Invariant Usefulness**: 8/10
  ```
  Justification: The invariants are directly tied to business requirements (error
  deduplication, investigation ordering, diagnosis lifecycle). They prevent real bugs
  (negative occurrence counts, reversed timestamps). Usefulness is high but partially
  undermined by inconsistent enforcement.
  ```

- **Invariant Enforcement**: 5/10
  ```
  Justification: __post_init__ validates on construction (good). However, there are
  THREE critical gaps:

  1. poll_service.py line 113: Direct mutation bypasses method transitions
     signature.last_seen = error.timestamp  # Bypasses record_occurrence()
     signature.occurrence_count += 1

  2. investigator.py line 100, 107, 131: Direct status mutation bypasses transitions
     signature.status = SignatureStatus.INVESTIGATING  # Bypasses mark_investigating()

  3. No enforcement that diagnosis != None implies status == DIAGNOSED

  This creates risk of invalid intermediate states during multi-step operations.
  ```

#### Code Examples

**Direct Mutation in poll_service.py (lines 113-114)**:
```python
signature.last_seen = error.timestamp       # ⚠ Bypasses record_occurrence()
signature.occurrence_count += 1
```

Should use:
```python
signature.record_occurrence(error.timestamp)  # ✓ Enforces invariants
```

**Direct Status Mutation in investigator.py (line 100)**:
```python
signature.status = SignatureStatus.INVESTIGATING  # ⚠ Bypasses mark_investigating()
```

Should use:
```python
signature.mark_investigating()  # ✓ Enforces transition rules
```

#### Strengths

- __post_init__ validation on construction prevents invalid initial states
- Status transition methods exist and check entry conditions
- frozenset for tags prevents accidental tag mutations
- Frozen diagnosis prevents post-diagnosis modifications
- Clear method names express intent (mark_resolved, mark_muted)

#### Concerns

1. **Critical**: Direct field mutation undermines invariant enforcement
   - Callers bypass validation methods in 4+ locations
   - Violates encapsulation principle
   - Creates risk of invalid intermediate states

2. **High**: Diagnosis-Status Consistency Not Enforced
   - Currently possible to have `diagnosis != None` but `status != DIAGNOSED`
   - No compile-time guarantee that these stay synchronized
   - Only enforced through caller discipline

3. **Medium**: Status Transition State Machine Is Partial
   - Methods exist (mark_investigating, mark_resolved, etc.)
   - But direct field access allows any status → any status transition
   - Some invalid transitions have runtime checks; others don't

#### Recommended Improvements

**Priority 1** (Critical - Prevents Invariant Violations):

Make status transitions immutable by removing direct `status` field access:

```python
@dataclass
class Signature:
    """A fingerprinted failure pattern.

    Represents a class of errors, not a single occurrence.
    Tracks lifecycle, occurrence count, and optional diagnosis.
    """

    id: str
    fingerprint: str
    error_type: str
    service: str
    message_template: str
    stack_hash: str
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    status: SignatureStatus  # Keep as field for storage/queries
    diagnosis: Diagnosis | None = None
    tags: frozenset[str] = field(default_factory=frozenset)

    # Internal enforcement through methods only
    def _validate_transition(self, new_status: SignatureStatus) -> None:
        """Validate status transition is legal."""
        if self.status == SignatureStatus.RESOLVED and new_status != SignatureStatus.RESOLVED:
            raise ValueError(f"Cannot transition from RESOLVED to {new_status}")
        if self.status == SignatureStatus.MUTED and new_status != SignatureStatus.MUTED:
            raise ValueError(f"Cannot transition from MUTED to {new_status}")

    def _set_status_and_diagnosis(
        self, new_status: SignatureStatus, diagnosis: Diagnosis | None = None
    ) -> None:
        """Internal method for atomic status+diagnosis updates."""
        self._validate_transition(new_status)
        self.status = new_status
        if diagnosis is not None:
            self.diagnosis = diagnosis
        else if new_status != SignatureStatus.DIAGNOSED:
            self.diagnosis = None

    def mark_diagnosed(self, diagnosis: Diagnosis) -> None:
        """Transition to DIAGNOSED with diagnosis."""
        if not isinstance(diagnosis, Diagnosis):
            raise TypeError(f"diagnosis must be Diagnosis, got {type(diagnosis)}")
        self._set_status_and_diagnosis(SignatureStatus.DIAGNOSED, diagnosis)
```

OR better: Create an immutable builder pattern:

```python
def with_occurrence(self, timestamp: datetime) -> "Signature":
    """Return a new Signature with recorded occurrence (immutable update)."""
    if timestamp < self.first_seen:
        raise ValueError(...)
    return Signature(
        id=self.id,
        fingerprint=self.fingerprint,
        error_type=self.error_type,
        service=self.service,
        message_template=self.message_template,
        stack_hash=self.stack_hash,
        first_seen=self.first_seen,
        last_seen=timestamp,
        occurrence_count=self.occurrence_count + 1,
        status=self.status,
        diagnosis=self.diagnosis,
        tags=self.tags,
    )
```

**Priority 2** (High - Adds Critical Invariant):

Add assertion for diagnosis-status consistency:

```python
def __post_init__(self) -> None:
    """Validate signature invariants on creation or deserialization."""
    # Existing checks...
    if self.occurrence_count < 1:
        raise ValueError(...)
    if self.last_seen < self.first_seen:
        raise ValueError(...)

    # NEW: Diagnosis consistency check
    if self.diagnosis is not None and self.status != SignatureStatus.DIAGNOSED:
        raise ValueError(
            f"Signature has diagnosis but status is {self.status}, "
            f"expected DIAGNOSED"
        )
```

**Priority 3** (Medium - Improves API Surface):

Replace method-only transitions with more defensive API:

```python
def can_transition_to(self, new_status: SignatureStatus) -> bool:
    """Check if transition is allowed without side effects."""
    if self.status == new_status:
        return True
    if self.status in {SignatureStatus.RESOLVED, SignatureStatus.MUTED}:
        return False
    return new_status in {SignatureStatus.INVESTIGATING, SignatureStatus.DIAGNOSED,
                         SignatureStatus.RESOLVED, SignatureStatus.MUTED}
```

---

### 2. Type: `Diagnosis`

**File**: `/workspace/rounds/core/models.py` (lines 74-91)

#### Invariants Identified

1. **Non-negative Cost Invariant**: `cost_usd >= 0`
   - Diagnosis cost cannot be negative
   - Enforced: `__post_init__` ✓

2. **Confidence Invariant**: `confidence` must be one of {"high", "medium", "low"}
   - Expressed via TypeAlias at line 71 ✓

3. **Immutability Invariant**: Once created, a diagnosis cannot change
   - Enforced: `frozen=True` ✓

4. **Evidence Non-empty**: Ideally `evidence` should have at least one item
   - **NOT ENFORCED** ⚠

#### Ratings

- **Encapsulation**: 9/10
  ```
  Justification: Properly frozen, all fields private to instance. No way to mutate
  after creation. Clean interface. Only minor issue: evidence tuple could be empty.
  ```

- **Invariant Expression**: 8/10
  ```
  Justification: Uses TypeAlias for confidence (clear intent). Immutability is clear
  from frozen=True. Non-negative cost is enforced. Only gap: evidence emptiness not
  expressed in the type.
  ```

- **Invariant Usefulness**: 8/10
  ```
  Justification: Cost tracking is critical for LLM budget enforcement. Immutability
  prevents accidental diagnosis corruption. Confidence levels directly impact
  notification decisions. All invariants serve business requirements.
  ```

- **Invariant Enforcement**: 8/10
  ```
  Justification: __post_init__ validates cost. frozen=True prevents mutation.
  TypeAlias expresses confidence constraint (compile-time checked). Only gap:
  evidence tuple emptiness is not validated.
  ```

#### Strengths

- Perfect immutability (frozen=True) makes diagnosis thread-safe
- Cost validation prevents negative/invalid budget impacts
- TypeAlias "Confidence" is self-documenting and type-checked
- Evidence field is a tuple (immutable collection)
- clean, minimal interface

#### Concerns

1. **Minor**: Evidence tuple could be empty
   - Currently nothing prevents `evidence = ()`
   - Empty evidence makes diagnosis seem invalid/incomplete
   - Could fail silently in notification rendering

#### Recommended Improvements

**Priority 3** (Low - Defensive Validation):

Add evidence non-emptiness check:

```python
def __post_init__(self) -> None:
    """Validate diagnosis invariants on creation."""
    if self.cost_usd < 0:
        raise ValueError(f"cost_usd must be non-negative, got {self.cost_usd}")

    if not self.evidence:  # NEW: Check evidence is non-empty
        raise ValueError(
            "Diagnosis must have at least one evidence item. "
            "Evidence tuple cannot be empty."
        )
```

---

### 3. Type: `ErrorEvent`

**File**: `/workspace/rounds/core/models.py` (lines 36-58)

#### Invariants Identified

1. **Immutability Invariant**: Once created, cannot change
   - Enforced: `frozen=True` ✓

2. **Attributes Are Read-Only**: `attributes` dict cannot be modified
   - Enforced: `MappingProxyType` in `__post_init__` ✓

3. **Non-empty Severity**: Severity enum ensures valid values
   - Enforced: `Severity` enum ✓

#### Ratings

- **Encapsulation**: 9/10
  ```
  Justification: Frozen, uses MappingProxyType for read-only attributes dict,
  stack frames are immutable tuple. No way to mutate. Perfect encapsulation.
  ```

- **Invariant Expression**: 9/10
  ```
  Justification: Immutability is clear. Severity enum is self-documenting.
  MappingProxyType signals that attributes cannot be modified. Clean design.
  ```

- **Invariant Usefulness**: 8/10
  ```
  Justification: Immutability ensures telemetry data isn't corrupted after fetch.
  Read-only attributes prevent accidental modifications. Severity enum prevents
  invalid values. All support reliable error diagnosis.
  ```

- **Invariant Enforcement**: 9/10
  ```
  Justification: frozen=True + MappingProxyType + tuple for stack frames = complete
  immutability enforcement at runtime. No loopholes.
  ```

#### Strengths

- Exemplary use of frozen dataclass + MappingProxyType for immutability
- Severity enum prevents invalid values
- Stack frames as immutable tuple collection
- Clear, minimal interface
- Thread-safe by design

#### Concerns

None significant.

#### Recommended Improvements

No changes recommended. This is a well-designed immutable type.

---

### 4. Type: `StackFrame`, `TraceTree`, `LogEntry`, `SpanNode`

**File**: `/workspace/rounds/core/models.py` (lines 14-213)

#### Shared Analysis

All these types are **properly frozen** with excellent immutability design:

- `StackFrame`: frozen, simple struct (lines 14-21)
- `SpanNode`: frozen, MappingProxyType for attributes, tuple for immutable collections (lines 165-184)
- `LogEntry`: frozen, MappingProxyType for attributes (lines 196-212)
- `TraceTree`: frozen, tuple for immutable collections (lines 187-193)

#### Ratings (All Similar)

- **Encapsulation**: 9/10 (All frozen, proper use of immutable collections)
- **Invariant Expression**: 8/10 (Clear structure; could add more doc comments)
- **Invariant Usefulness**: 8/10 (Prevent corruption of observability data)
- **Invariant Enforcement**: 9/10 (Frozen + proper collection types)

#### Concerns

1. **Minor**: No validation of child span hierarchy in SpanNode
   - `children` tuple could contain duplicate span IDs
   - parent_id could be inconsistent with actual parent

2. **Minor**: `duration_ms` could be negative (physically impossible)

#### Recommended Improvements

**Priority 2** (Medium - Defensive):

Add duration validation:

```python
@dataclass(frozen=True)
class SpanNode:
    """A single span in a distributed trace."""

    span_id: str
    parent_id: str | None
    service: str
    operation: str
    duration_ms: float
    status: str
    attributes: MappingProxyType[str, Any]
    events: tuple[dict[str, Any], ...]
    children: tuple["SpanNode", ...] = ()

    def __post_init__(self) -> None:
        """Validate span invariants."""
        if isinstance(self.attributes, dict):
            object.__setattr__(
                self, "attributes", MappingProxyType(self.attributes)
            )
        if self.duration_ms < 0:  # NEW
            raise ValueError(
                f"duration_ms must be non-negative, got {self.duration_ms}"
            )
```

---

### 5. Type: `Confidence` (TypeAlias)

**File**: `/workspace/rounds/core/models.py` (line 71)

#### Analysis

```python
Confidence: TypeAlias = Literal["high", "medium", "low"]
```

#### Ratings

- **Encapsulation**: 10/10 (Immutable, type-safe)
- **Invariant Expression**: 10/10 (Literal type perfectly expresses valid values)
- **Invariant Usefulness**: 10/10 (Prevents invalid confidence levels at compile-time)
- **Invariant Enforcement**: 10/10 (Type checker catches invalid values)

#### Strengths

- Excellent use of Literal for fixed enum values
- More flexible than Python Enum (works with strings in JSON)
- Self-documenting and composable
- Compile-time checked by type checkers

#### Concerns

None.

#### Recommended Improvements

None needed. This is exemplary.

---

### 6. Type: `SignatureStatus` (Enum)

**File**: `/workspace/rounds/core/models.py` (lines 61-68)

#### Analysis

```python
class SignatureStatus(Enum):
    """Lifecycle states for a failure signature."""
    NEW = "new"
    INVESTIGATING = "investigating"
    DIAGNOSED = "diagnosed"
    RESOLVED = "resolved"
    MUTED = "muted"
```

#### Ratings

- **Encapsulation**: 9/10 (Enum prevents invalid values, but doesn't express transitions)
- **Invariant Expression**: 7/10 (Lists valid values, but doesn't express state machine)
- **Invariant Usefulness**: 8/10 (Prevents invalid status values; state machine useful)
- **Invariant Enforcement**: 6/10 (Type prevents invalid enum values, but state machine not enforced)

#### Concerns

1. **Medium**: State machine transitions are not expressed in the type
   - Type allows any status → any status transition
   - Invariant "terminal states are terminal" not captured
   - Only enforced through runtime checks in methods

#### Recommended Improvements

**Priority 3** (Low - Nice-to-Have):

Add machine-readable transition definitions:

```python
class SignatureStatus(Enum):
    """Lifecycle states for a failure signature."""
    NEW = "new"
    INVESTIGATING = "investigating"
    DIAGNOSED = "diagnosed"
    RESOLVED = "resolved"
    MUTED = "muted"

    @classmethod
    def transitions_from(cls, status: "SignatureStatus") -> set["SignatureStatus"]:
        """Return valid next states from the given status.

        NEW → {INVESTIGATING, MUTED}
        INVESTIGATING → {DIAGNOSED, MUTED, NEW}
        DIAGNOSED → {RESOLVED, MUTED, INVESTIGATING}
        RESOLVED → {} (terminal)
        MUTED → {} (terminal)
        """
        transitions = {
            cls.NEW: {cls.INVESTIGATING, cls.MUTED},
            cls.INVESTIGATING: {cls.DIAGNOSED, cls.MUTED, cls.NEW},
            cls.DIAGNOSED: {cls.RESOLVED, cls.MUTED, cls.INVESTIGATING},
            cls.RESOLVED: set(),
            cls.MUTED: set(),
        }
        return transitions[status]
```

---

### 7. Port Classes: TelemetryPort, SignatureStorePort, DiagnosisPort, NotificationPort

**File**: `/workspace/rounds/core/ports.py` (lines 43-366)

#### Invariants Identified

1. **Async Contract**: All methods are async
   - Enforced: `@abstractmethod async def` ✓

2. **No Return Type Violations**: Return types match documented contracts
   - Partially enforced: Type annotations present, but no compile-time guarantee ⚠

3. **Exception Handling Contract**: All methods specify exceptions they may raise
   - Documented: Yes, in docstrings ⚓ (documentation only)

4. **Query Completeness**: Methods have clear preconditions/postconditions
   - Documented: Yes, in docstrings ⚓

#### Ratings

- **Encapsulation**: 9/10
  ```
  Justification: Abstract base classes properly hide implementation. Concrete adapters
  implement interfaces without exposing internals. Dependencies flow through ports,
  not direct implementations. Excellent separation.
  ```

- **Invariant Expression**: 8/10
  ```
  Justification: Method signatures are clear. Return types are specified. Docstrings
  document contracts thoroughly. Only gap: exceptions are documented but not expressed
  in Python type hints (could use @overload or Union[T, Exception]).
  ```

- **Invariant Usefulness**: 9/10
  ```
  Justification: Port abstraction is critical for testability (fakes instead of mocks).
  Contracts prevent adapter implementations from returning invalid data. Enables
  swapping telemetry/store/diagnosis backends without core changes.
  ```

- **Invariant Enforcement**: 7/10
  ```
  Justification: Type annotations enforce return types at runtime (with type checker).
  Abstract methods enforce implementation by concrete classes. However, exceptions
  and preconditions are documented but not enforced (rely on caller discipline).
  ```

#### Strengths

- Clear, minimal port interfaces with single responsibility
- Excellent docstrings documenting behavior, errors, and edge cases
- Async/await throughout for non-blocking I/O
- Return types explicitly specified
- Preconditions and postconditions documented
- Separated into "Driven Ports" and "Driving Ports" with clear roles

#### Concerns

1. **Minor**: Exception handling is documented but not enforced
   - Docstrings say "Raises: Exception if backend unavailable"
   - But Python doesn't enforce documented exceptions
   - Adapters might silently succeed when they should fail

2. **Minor**: Some methods allow partial results without signaling
   - Example: `get_traces()` silently omits failed fetches (line 104-106)
   - Caller must detect partial results by comparing list lengths
   - Could use Optional/Result type or explicit "partial" return marker

3. **Minor**: `TraceTree` is frozen but contains mutable children
   - `SpanNode.children` is tuple (immutable), so this is OK
   - But if `SpanNode` had mutable fields, frozen parent would not protect them

#### Recommended Improvements

**Priority 2** (Medium - Improves Error Handling):

Create a Result type for partial failures:

```python
from dataclasses import dataclass
from typing import TypeAlias

@dataclass(frozen=True)
class FetchResult[T]:
    """Result of a batch fetch operation.

    Signals whether the operation succeeded completely, partially, or failed.
    """
    items: list[T]
    failed_ids: list[str]  # IDs that couldn't be fetched
    is_partial: bool       # True if some items failed

    @property
    def is_complete(self) -> bool:
        return not self.is_partial

class TelemetryPort(ABC):
    @abstractmethod
    async def get_traces(self, trace_ids: list[str]) -> FetchResult[TraceTree]:
        """Return traces with explicit failure tracking.

        Args:
            trace_ids: List of OpenTelemetry trace IDs.

        Returns:
            FetchResult with successful traces and list of failed IDs.
            If some IDs fail, is_partial=True but exception is not raised.

        Raises:
            Exception: Only if ALL traces fail or backend is unreachable.
        """
```

Then callers can be explicit:

```python
result = await telemetry.get_traces(trace_ids)
if result.is_partial:
    logger.warning(f"Incomplete traces: {result.failed_ids}")
traces = result.items  # Use successful traces
```

**Priority 3** (Low - Documentation):

Add docstring section on exception types:

```python
class TelemetryPort(ABC):
    """Port for retrieving errors, traces, and logs.

    Exception Guarantees:
    - TimeoutError: if backend response takes > 30 seconds
    - ConnectionError: if backend is unreachable
    - ValueError: if parameters are invalid (caught before sending)
    - Exception: for any other backend error

    Implementations should be specific about exception types.
    """
```

---

### 8. Type: `Settings` (Configuration)

**File**: `/workspace/rounds/config.py` (lines 16-242)

#### Invariants Identified

1. **Poll Interval Invariant**: `poll_interval_seconds > 0`
   - Enforced: `@field_validator` (line 195-201) ✓

2. **Batch Size Invariant**: `poll_batch_size > 0`
   - Enforced: `@field_validator` (line 203-209) ✓

3. **Budget Invariants**: Non-negative budgets
   - `claude_code_budget_usd >= 0` (line 211-217) ✓
   - `daily_budget_limit >= 0` (line 219-225) ✓

4. **Lookback Window Invariant**: `error_lookback_minutes > 0`
   - Enforced: `@field_validator` (line 227-233) ✓

5. **Port Range Invariant**: `1 <= webhook_port <= 65535`
   - Enforced: `@field_validator` (line 235-241) ✓

6. **Literal Type Constraints**: Backend choices are fixed strings
   - Enforced: `Literal[...]` on 5 fields ✓

#### Ratings

- **Encapsulation**: 9/10
  ```
  Justification: Uses pydantic BaseSettings for proper configuration management.
  Fields are private (no direct mutation after load). Validators run at load time.
  Single source of truth. Only concern: Settings instance is mutable after creation
  (pydantic default), but CLAUDE.md says "never modified" so this is OK if enforced.
  ```

- **Invariant Expression**: 9/10
  ```
  Justification: Excellent use of Literal for fixed choices. Field validators clearly
  express numeric constraints. Docstrings describe purpose. Type annotations are
  complete. Self-documenting.
  ```

- **Invariant Usefulness**: 9/10
  ```
  Justification: All invariants directly support correct system operation:
  - Positive intervals prevent infinite loops/divisions by zero
  - Valid port prevents network errors
  - Budget constraints prevent runaway costs
  - Backend choices prevent configuration errors
  ```

- **Invariant Enforcement**: 9/10
  ```
  Justification: pydantic validators run automatically when Settings() is created.
  Invalid configs are caught at startup (fail-fast). Literal types are enforced by
  type checker. Only minor gap: validators check individually, not cross-field
  relationships.
  ```

#### Strengths

- Exemplary use of pydantic BaseSettings + field validators
- Comprehensive Literal types for fixed-choice fields
- Field descriptions document purpose and defaults
- Validators catch invalid values at startup (fail-fast)
- Supports .env file for environment-based configuration
- Default values provided for all fields (development-friendly)

#### Concerns

1. **Minor**: No cross-field validation
   - Example: Could validate that `claude_code_budget_usd <= daily_budget_limit`
   - Currently possible to set per-diagnosis budget higher than daily limit
   - Would be caught at runtime but could fail unexpectedly

2. **Minor**: GitHub repo configuration has redundancy
   - Both `github_repo` (line 116) and `github_repo_owner`/`github_repo_name` (lines 120-127)
   - Unclear which is used; could cause confusion
   - Should consolidate to single format

#### Recommended Improvements

**Priority 2** (Medium - Prevents Invalid Configurations):

Add cross-field validators:

```python
from pydantic import model_validator

@model_validator(mode='after')
def validate_budget_constraints(self) -> "Settings":
    """Validate cross-field budget relationships."""
    if self.claude_code_budget_usd > self.daily_budget_limit:
        raise ValueError(
            f"Per-diagnosis budget ({self.claude_code_budget_usd}) "
            f"cannot exceed daily limit ({self.daily_budget_limit})"
        )
    if self.openai_budget_usd > self.daily_budget_limit:
        raise ValueError(
            f"OpenAI per-diagnosis budget ({self.openai_budget_usd}) "
            f"cannot exceed daily limit ({self.daily_budget_limit})"
        )
    return self

@model_validator(mode='after')
def validate_github_repo_config(self) -> "Settings":
    """Validate GitHub repository configuration consistency."""
    has_repo_string = bool(self.github_repo)
    has_owner_name = bool(self.github_repo_owner or self.github_repo_name)

    if has_repo_string and has_owner_name:
        logger.warning(
            "Both github_repo and github_repo_owner/name are set. "
            "Using github_repo_owner/name."
        )

    if not has_repo_string and not has_owner_name:
        # Only required if notification_backend is github_issue
        if self.notification_backend == "github_issue":
            raise ValueError(
                "GitHub repository config required for github_issue backend. "
                "Set either github_repo or github_repo_owner + github_repo_name."
            )

    return self
```

**Priority 3** (Low - Consolidates Configuration):

Standardize GitHub repo configuration:

```python
@model_validator(mode='before')
@classmethod
def normalize_github_repo(cls, data: Any) -> Any:
    """Normalize GitHub repo config to owner/name format."""
    if isinstance(data, dict):
        repo = data.get('github_repo', '')
        if repo and '/' in repo:
            owner, name = repo.split('/', 1)
            data['github_repo_owner'] = owner
            data['github_repo_name'] = name
            data['github_repo'] = ''  # Clear to avoid duplication
    return data
```

---

## Cross-Cutting Issues

### Issue 1: Mutation vs. Immutability Design Conflict

**Severity**: High
**Scope**: Signature class primarily

The design has tension between two approaches:
1. **Immutable Design** (preferred): ErrorEvent, Diagnosis, TraceTree
2. **Mutable Design** (pragmatic): Signature

This leads to **direct field mutation in callers**, bypassing validation methods:

```python
# poll_service.py line 113
signature.last_seen = error.timestamp  # ⚠ Bypasses record_occurrence()

# investigator.py line 100
signature.status = SignatureStatus.INVESTIGATING  # ⚠ Bypasses mark_investigating()
```

**Recommendation**:
- Make Signature immutable with builder pattern, OR
- Enforce mutation only through methods (make fields private-ish with property setters)

---

### Issue 2: Incomplete Invariant Documentation

**Severity**: Medium
**Scope**: All types

Some invariants are only documented in code, not in docstrings:
- Diagnosis must not have empty evidence tuple
- Signature diagnosis-status consistency
- SpanNode duration must be non-negative

**Recommendation**:
Add "Invariants" section to all type docstrings:

```python
@dataclass(frozen=True)
class Diagnosis:
    """LLM-generated root cause analysis for a signature.

    Invariants:
    - cost_usd >= 0: Cost cannot be negative
    - confidence in {"high", "medium", "low"}: Must be valid confidence level
    - evidence is non-empty: Must have at least one supporting evidence
    - frozen: Immutable once created to prevent corruption
    """
```

---

### Issue 3: Exception Handling Not Type-Safe

**Severity**: Low
**Scope**: Port interfaces

Port abstract methods document exceptions in docstrings but don't use Python's exception type hints:

```python
async def get_recent_errors(self, since: datetime) -> list[ErrorEvent]:
    """...
    Raises:
        Exception: If telemetry backend is unreachable.
    """
```

**Recommendation**:
Define exception types and use them:

```python
class TelemetryError(Exception):
    """Base exception for telemetry operations."""
    pass

class TelemetryTimeoutError(TelemetryError):
    """Telemetry backend took too long to respond."""
    pass

class TelemetryConnectionError(TelemetryError):
    """Telemetry backend is unreachable."""
    pass

async def get_recent_errors(self, since: datetime) -> list[ErrorEvent]:
    """...
    Raises:
        TelemetryConnectionError: If backend is unreachable.
        TelemetryTimeoutError: If backend doesn't respond in time.
        TelemetryError: For other backend errors.
    """
```

---

## Summary Table

| Type | Encapsulation | Invariant Expression | Usefulness | Enforcement | Status |
|------|---|---|---|---|---|
| Signature | 6/10 | 7/10 | 8/10 | 5/10 | **⚠ Critical issues** |
| Diagnosis | 9/10 | 8/10 | 8/10 | 8/10 | ✓ Good |
| ErrorEvent | 9/10 | 9/10 | 8/10 | 9/10 | ✓ Excellent |
| TraceTree/SpanNode/LogEntry | 9/10 | 8/10 | 8/10 | 9/10 | ✓ Excellent |
| Confidence (TypeAlias) | 10/10 | 10/10 | 10/10 | 10/10 | ✓ Exemplary |
| SignatureStatus (Enum) | 9/10 | 7/10 | 8/10 | 6/10 | ✓ Good |
| Port Interfaces | 9/10 | 8/10 | 9/10 | 7/10 | ✓ Good |
| Settings | 9/10 | 9/10 | 9/10 | 9/10 | ✓ Excellent |

---

## Recommendations Priority List

### Critical (Do First)
1. **Enforce Signature mutations only through methods** - Current direct field mutation violates encapsulation and bypasses validation in 4+ places
2. **Add diagnosis-status consistency check** to Signature.__post_init__()

### High (Before Production)
3. **Add evidence non-emptiness validation** to Diagnosis
4. **Add cross-field budget validation** to Settings (per-diagnosis <= daily limit)
5. **Use dedicated exception types** for ports instead of generic Exception

### Medium (Improve Quality)
6. **Add "Invariants" section** to all type docstrings
7. **Create FetchResult type** for partial fetch operations in ports
8. **Add duration non-negativity check** to SpanNode
9. **Consolidate GitHub repo configuration** in Settings

### Low (Nice-to-Have)
10. **Add transition_from()** method to SignatureStatus
11. **Consider immutable builder pattern** for Signature if mutation remains difficult to manage

---

## Conclusion

The Rounds type system demonstrates **strong foundational design** with excellent use of:
- Frozen dataclasses for immutability
- TypeAlias and Literal for type safety
- Port abstraction for hexagonal architecture
- pydantic for configuration validation

However, the **Signature class has critical encapsulation issues** where callers bypass validation methods via direct field mutation. This creates risk of inconsistent state and violates the principle that types should prevent invalid states.

**Recommendation**: Fix Signature mutation enforcement (Priority 1) before the system reaches production load. The current design is maintainable at small scale but will accumulate state-consistency bugs as the codebase grows.

The configuration, port interfaces, and immutable data types (ErrorEvent, Diagnosis, TraceTree) are exemplary and require no changes.

---

**Report prepared**: February 12, 2026
**Files reviewed**:
- `/workspace/rounds/core/models.py` (259 lines)
- `/workspace/rounds/core/ports.py` (538 lines)
- `/workspace/rounds/config.py` (262 lines)
