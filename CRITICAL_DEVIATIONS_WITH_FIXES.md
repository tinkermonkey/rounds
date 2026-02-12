# Critical Deviations: Code Locations and Fixes

## Overview

This document provides the exact code locations for the 3 critical deviations from parent issue requirements, along with concrete fix implementations.

**Total Fix Time**: 5 hours + 2.5 hours tests = 7.5 hours

---

## CRITICAL DEVIATION #1: Grafana Stack Silent Failure

### Location
**File**: `/workspace/rounds/adapters/telemetry/grafana_stack.py`
**Lines**: 457-460

### Current Code
```python
def get_correlated_logs(self, trace_id: str, window_minutes: int = 5) -> list[LogEntry]:
    """Get logs correlated with a specific trace."""
    try:
        # ... query implementation ...
        logs = query_logs_from_loki(trace_id, window_minutes)
        return logs
    except Exception:  # ❌ DEVIATION: Silent failure
        return []       # Returns empty instead of raising
```

### Problem
- **Requirement Violated**: Error handling must properly propagate failures
- **Port Contract Violated**: `TelemetryPort.get_correlated_logs()` should raise on failure
- **Data Loss**: Orchestration layer can't distinguish between "no logs" and "failed to fetch"
- **Diagnosis Quality**: Incomplete error context silently used

### Corrected Code
```python
def get_correlated_logs(self, trace_id: str, window_minutes: int = 5) -> list[LogEntry]:
    """Get logs correlated with a specific trace.

    Raises:
        RuntimeError: If logs cannot be fetched (connection failure, auth error, etc.)
    """
    try:
        logs = query_logs_from_loki(trace_id, window_minutes)
        return logs
    except Exception as e:
        logger.error(
            f"Failed to fetch correlated logs for trace {trace_id}: {e}",
            extra={"trace_id": trace_id, "window_minutes": window_minutes}
        )
        raise RuntimeError(
            f"Grafana Stack: Failed to fetch correlated logs for trace {trace_id}"
        ) from e
```

### Test Case
```python
def test_get_correlated_logs_raises_on_network_error(self):
    """Verify that network errors are propagated, not masked."""
    adapter = GrafanaStackAdapter(config)

    with patch("query_logs_from_loki", side_effect=ConnectionError("Loki unreachable")):
        with pytest.raises(RuntimeError, match="Failed to fetch correlated logs"):
            adapter.get_correlated_logs("trace-123")
```

### Verification
- [ ] Make change to `grafana_stack.py`
- [ ] Run `pytest tests/ -k "grafana_stack"` - should pass
- [ ] Run `pytest tests/ -k "get_correlated_logs"` - should pass
- [ ] Run full test suite - all 150+ tests should pass
- [ ] Review error message appears in logs when connection fails

---

## CRITICAL DEVIATION #2: Signature Type is Mutable

### Location
**File**: `/workspace/rounds/core/models.py`
**Lines**: 92-127

### Current Code
```python
@dataclass
class Signature:  # ❌ NOT frozen - should be immutable
    """Error signature with stable fingerprint."""
    id: str
    fingerprint_hash: str
    status: SignatureStatus
    diagnosis: str | None = None
    confidence: float = 0.0
    suggested_fix: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    last_occurred: datetime = field(default_factory=datetime.now)
    occurrence_count: int = 0
    last_diagnosed: datetime | None = None
    muted_until: datetime | None = None

    # ❌ PROBLEM: Fields can be mutated after construction
    # Multiple mutation sites in:
    #   - poll_service.py:73-128
    #   - investigator.py:82-127
    #   - management_service.py:33-123
```

### Problem
- **Requirement Violated**: Clean architecture requires proper encapsulation
- **Encapsulation**: Type is mutable - invariants can be violated
- **State Machine**: No enforcement of valid transitions (RESOLVED→NEW is possible)
- **Consistency**: No audit trail of state changes
- **Concurrency**: Multiple systems mutating same signature = race conditions
- **Safety**: `diagnosis` field can exist on non-DIAGNOSED signatures

### Mutation Sites
```python
# rounds/core/poll_service.py:73-128
sig.last_occurred = datetime.now()  # ❌ Direct mutation
sig.occurrence_count += 1           # ❌ Direct mutation
sig.status = SignatureStatus.NEEDS_INVESTIGATION  # ❌ Invalid transition?

# rounds/core/investigator.py:82-127
sig.diagnosis = diagnosis.analysis  # ❌ Direct mutation
sig.confidence = diagnosis.confidence  # ❌ Direct mutation
sig.status = SignatureStatus.DIAGNOSED  # ❌ Direct mutation

# rounds/core/management_service.py:33-123
sig.status = SignatureStatus.RESOLVED  # ❌ Direct mutation
sig.muted_until = until_time  # ❌ Direct mutation
```

### Corrected Code - Step 1: Make Frozen
```python
@dataclass(frozen=True)  # ✅ NOW FROZEN
class Signature:
    """Error signature with stable fingerprint.

    Immutable domain model with validated state transitions.
    Use with_*() methods to create new instances with updated state.
    """
    id: str
    fingerprint_hash: str
    status: SignatureStatus
    diagnosis: str | None = None
    confidence: float = 0.0
    suggested_fix: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    last_occurred: datetime = field(default_factory=datetime.now)
    occurrence_count: int = 0
    last_diagnosed: datetime | None = None
    muted_until: datetime | None = None

    def __post_init__(self):
        """Validate invariants."""
        # Diagnosis should only exist on DIAGNOSED signatures
        if self.diagnosis is not None and self.status != SignatureStatus.DIAGNOSED:
            raise ValueError(
                f"diagnosis can only exist on DIAGNOSED signatures, "
                f"not {self.status.name}"
            )
        # Confidence only meaningful on DIAGNOSED signatures
        if self.confidence > 0 and self.status != SignatureStatus.DIAGNOSED:
            raise ValueError(
                f"confidence > 0 only valid on DIAGNOSED signatures, not {self.status.name}"
            )
        # Muted implies resolved or investigating
        if self.muted_until is not None and self.status not in [
            SignatureStatus.RESOLVED,
            SignatureStatus.INVESTIGATING,
        ]:
            raise ValueError(f"Cannot mute {self.status.name} signature")
```

### Corrected Code - Step 2: Add Validated Transition Methods
```python
    def record_occurrence(self) -> Signature:
        """Record that this signature occurred again."""
        return replace(
            self,
            last_occurred=datetime.now(),
            occurrence_count=self.occurrence_count + 1,
        )

    def mark_needs_investigation(self) -> Signature:
        """Transition to NEEDS_INVESTIGATION status.

        Valid from: NEW, INVESTIGATING
        Invalid from: RESOLVED, MUTED, ARCHIVED
        """
        valid_from = {
            SignatureStatus.NEW,
            SignatureStatus.NEEDS_INVESTIGATION,
            SignatureStatus.INVESTIGATING,
        }
        if self.status not in valid_from:
            raise ValueError(
                f"Cannot mark {self.status.name} signature as needing investigation"
            )
        return replace(self, status=SignatureStatus.NEEDS_INVESTIGATION)

    def mark_investigating(self) -> Signature:
        """Transition to INVESTIGATING status."""
        if self.status != SignatureStatus.NEEDS_INVESTIGATION:
            raise ValueError(
                f"Can only investigate from NEEDS_INVESTIGATION, not {self.status.name}"
            )
        return replace(self, status=SignatureStatus.INVESTIGATING)

    def mark_diagnosed(
        self,
        diagnosis: str,
        confidence: float,
        suggested_fix: str | None = None,
    ) -> Signature:
        """Transition to DIAGNOSED with diagnosis details.

        Args:
            diagnosis: The root cause analysis
            confidence: Confidence score 0.0-1.0
            suggested_fix: Optional suggested fix
        """
        if self.status != SignatureStatus.INVESTIGATING:
            raise ValueError(
                f"Can only diagnose from INVESTIGATING, not {self.status.name}"
            )
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {confidence}")

        return replace(
            self,
            status=SignatureStatus.DIAGNOSED,
            diagnosis=diagnosis,
            confidence=confidence,
            suggested_fix=suggested_fix,
            last_diagnosed=datetime.now(),
        )

    def mark_resolved(self) -> Signature:
        """Transition to RESOLVED status."""
        if self.status not in {
            SignatureStatus.DIAGNOSED,
            SignatureStatus.INVESTIGATING,
        }:
            raise ValueError(
                f"Can only resolve DIAGNOSED or INVESTIGATING signatures, "
                f"not {self.status.name}"
            )
        return replace(
            self,
            status=SignatureStatus.RESOLVED,
            muted_until=None,  # Clear mute when resolved
        )

    def mark_muted(self, until: datetime) -> Signature:
        """Mute this signature until the given time."""
        if self.status not in {
            SignatureStatus.DIAGNOSED,
            SignatureStatus.INVESTIGATING,
            SignatureStatus.NEEDS_INVESTIGATION,
        }:
            raise ValueError(f"Cannot mute {self.status.name} signature")
        if until <= datetime.now():
            raise ValueError("Mute until time must be in the future")

        return replace(self, muted_until=until)

    def unmute(self) -> Signature:
        """Remove mute."""
        return replace(self, muted_until=None)

    def mark_archived(self) -> Signature:
        """Transition to ARCHIVED status."""
        return replace(self, status=SignatureStatus.ARCHIVED)
```

### Corrected Code - Step 3: Update Mutation Sites

#### In `poll_service.py:73-128`
**Before**:
```python
def poll_and_deduplicate(self, ...) -> PollResult:
    for error in errors:
        sig = self.store.get_signature(fingerprint)
        sig.last_occurred = datetime.now()  # ❌ Direct mutation
        sig.occurrence_count += 1           # ❌ Direct mutation
```

**After**:
```python
def poll_and_deduplicate(self, ...) -> PollResult:
    for error in errors:
        sig = self.store.get_signature(fingerprint)
        sig = sig.record_occurrence()  # ✅ Validated update
        self.store.update_signature(sig)  # ✅ Persist change
```

#### In `investigator.py:82-127`
**Before**:
```python
def diagnose(self, sig: Signature) -> Diagnosis:
    result = self.diagnosis_port.diagnose(context)
    sig.diagnosis = result.analysis     # ❌ Direct mutation
    sig.confidence = result.confidence  # ❌ Direct mutation
    sig.status = SignatureStatus.DIAGNOSED  # ❌ Direct mutation
```

**After**:
```python
def diagnose(self, sig: Signature) -> Diagnosis:
    result = self.diagnosis_port.diagnose(context)
    sig = sig.mark_diagnosed(
        diagnosis=result.analysis,
        confidence=result.confidence,
        suggested_fix=result.suggested_fix,
    )  # ✅ Validated transition
    self.store.update_signature(sig)  # ✅ Persist change
    return result
```

#### In `management_service.py:33-123`
**Before**:
```python
def resolve_signature(self, sig_id: str, ...) -> None:
    sig = self.store.get_signature(sig_id)
    sig.status = SignatureStatus.RESOLVED  # ❌ Direct mutation
```

**After**:
```python
def resolve_signature(self, sig_id: str, ...) -> None:
    sig = self.store.get_signature(sig_id)
    sig = sig.mark_resolved()  # ✅ Validated transition
    self.store.update_signature(sig)  # ✅ Persist change
```

### Test Cases
```python
class TestSignatureStateTransitions:
    """Validate Signature state machine."""

    def test_frozen_prevents_mutation(self):
        """Verify Signature is frozen."""
        sig = Signature(id="1", fingerprint_hash="abc", status=SignatureStatus.NEW)
        with pytest.raises(FrozenInstanceError):
            sig.status = SignatureStatus.INVESTIGATED

    def test_diagnosis_only_on_diagnosed(self):
        """Diagnosis field only valid on DIAGNOSED."""
        with pytest.raises(ValueError, match="diagnosis can only exist on DIAGNOSED"):
            Signature(
                id="1",
                fingerprint_hash="abc",
                status=SignatureStatus.NEW,
                diagnosis="Some cause"  # ❌ Invalid
            )

    def test_mark_diagnosed_validates_transition(self):
        """Can only diagnose from INVESTIGATING."""
        sig = Signature(
            id="1",
            fingerprint_hash="abc",
            status=SignatureStatus.NEW
        )
        with pytest.raises(ValueError, match="Can only diagnose from INVESTIGATING"):
            sig.mark_diagnosed("Root cause", 0.95)

    def test_valid_transition_flow(self):
        """Test valid state machine flow."""
        sig = Signature(id="1", fingerprint_hash="abc", status=SignatureStatus.NEW)

        # NEW → NEEDS_INVESTIGATION
        sig = sig.mark_needs_investigation()
        assert sig.status == SignatureStatus.NEEDS_INVESTIGATION

        # NEEDS_INVESTIGATION → INVESTIGATING
        sig = sig.mark_investigating()
        assert sig.status == SignatureStatus.INVESTIGATING

        # INVESTIGATING → DIAGNOSED
        sig = sig.mark_diagnosed("Root cause", 0.95, "Fix X")
        assert sig.status == SignatureStatus.DIAGNOSED
        assert sig.diagnosis == "Root cause"
        assert sig.confidence == 0.95

        # DIAGNOSED → RESOLVED
        sig = sig.mark_resolved()
        assert sig.status == SignatureStatus.RESOLVED
```

### Verification
- [ ] Add `frozen=True` to Signature dataclass
- [ ] Add `__post_init__` validation
- [ ] Add all transition methods
- [ ] Update poll_service.py (3 mutation sites)
- [ ] Update investigator.py (3 mutation sites)
- [ ] Update management_service.py (4 mutation sites)
- [ ] Run tests - verify all existing tests pass
- [ ] Run new state machine tests - verify all pass
- [ ] Review for any remaining direct mutations

---

## CRITICAL DEVIATION #3: No Constructor Validation

### Location
**File**: `/workspace/rounds/core/models.py`
**Lines**: Multiple types need validation

### Affected Types (8 total)
1. `ErrorEvent` (line 129)
2. `Diagnosis` (line 142)
3. `StackFrame` (line 158)
4. `SpanNode` (line 170)
5. `TraceTree` (line 185)
6. `LogEntry` (line 200)
7. `InvestigationContext` (line 215)
8. `PollResult` (line 235)

### Problem
These types accept invalid values at construction time:
- Empty strings (when values should be non-empty)
- Negative numbers (when should be non-negative)
- None (when should be required)
- Invalid timestamps

### Example: ErrorEvent
**Current Code**:
```python
@dataclass
class ErrorEvent:
    """An error from telemetry."""
    trace_id: str               # ❌ Can be ""
    span_id: str                # ❌ Can be ""
    timestamp: datetime         # ❌ No validation
    service_name: str           # ❌ Can be ""
    message: str                # ❌ Can be ""
    stack_trace: str = ""
    error_type: str = ""
    # No validation - garbage in, garbage out
```

**Corrected Code**:
```python
@dataclass
class ErrorEvent:
    """An error from telemetry.

    All string fields must be non-empty. Timestamps must be in the past.
    """
    trace_id: str
    span_id: str
    timestamp: datetime
    service_name: str
    message: str
    stack_trace: str = ""
    error_type: str = ""

    def __post_init__(self):
        """Validate all fields at construction time."""
        if not self.trace_id:
            raise ValueError("trace_id cannot be empty")
        if not self.span_id:
            raise ValueError("span_id cannot be empty")
        if not self.service_name:
            raise ValueError("service_name cannot be empty")
        if not self.message:
            raise ValueError("message cannot be empty")

        # Timestamp must be in the past (or very recent)
        if self.timestamp > datetime.now() + timedelta(seconds=1):
            raise ValueError("timestamp cannot be in the future")

        # Stack trace and error type are optional but if provided must be non-empty
        if self.stack_trace is not None and isinstance(self.stack_trace, str):
            if self.stack_trace and len(self.stack_trace.strip()) == 0:
                raise ValueError("stack_trace if provided cannot be empty")
        if self.error_type is not None and isinstance(self.error_type, str):
            if self.error_type and len(self.error_type.strip()) == 0:
                raise ValueError("error_type if provided cannot be empty")
```

### Complete Validation for All 8 Types

#### 1. Diagnosis
```python
@dataclass
class Diagnosis:
    trace_id: str
    root_cause: str
    confidence: float
    suggested_fix: str

    def __post_init__(self):
        if not self.trace_id:
            raise ValueError("trace_id cannot be empty")
        if not self.root_cause:
            raise ValueError("root_cause cannot be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0-1.0, got {self.confidence}")
        if not self.suggested_fix:
            raise ValueError("suggested_fix cannot be empty")
```

#### 2. StackFrame
```python
@dataclass
class StackFrame:
    filename: str
    function_name: str
    line_number: int
    source_line: str | None

    def __post_init__(self):
        if not self.filename:
            raise ValueError("filename cannot be empty")
        if not self.function_name:
            raise ValueError("function_name cannot be empty")
        if self.line_number < 0:
            raise ValueError(f"line_number must be >= 0, got {self.line_number}")
```

#### 3. SpanNode
```python
@dataclass
class SpanNode:
    span_id: str
    operation_name: str
    duration_ms: float
    error_occurred: bool

    def __post_init__(self):
        if not self.span_id:
            raise ValueError("span_id cannot be empty")
        if not self.operation_name:
            raise ValueError("operation_name cannot be empty")
        if self.duration_ms < 0:
            raise ValueError(f"duration_ms must be >= 0, got {self.duration_ms}")
```

#### 4. TraceTree
```python
@dataclass
class TraceTree:
    root_span_id: str
    all_spans: list[SpanNode]

    def __post_init__(self):
        if not self.root_span_id:
            raise ValueError("root_span_id cannot be empty")
        if not self.all_spans:
            raise ValueError("all_spans cannot be empty")
```

#### 5. LogEntry
```python
@dataclass
class LogEntry:
    timestamp: datetime
    level: str
    message: str
    service: str

    def __post_init__(self):
        if not self.level:
            raise ValueError("level cannot be empty")
        if self.level not in {"DEBUG", "INFO", "WARN", "ERROR", "FATAL"}:
            raise ValueError(f"Invalid log level: {self.level}")
        if not self.message:
            raise ValueError("message cannot be empty")
        if not self.service:
            raise ValueError("service cannot be empty")
        if self.timestamp > datetime.now() + timedelta(seconds=1):
            raise ValueError("timestamp cannot be in the future")
```

#### 6. InvestigationContext
```python
@dataclass
class InvestigationContext:
    error_event: ErrorEvent
    trace_tree: TraceTree | None
    correlated_logs: list[LogEntry]

    def __post_init__(self):
        if self.error_event is None:
            raise ValueError("error_event cannot be None")
        # Logs can be empty but not None
        if self.correlated_logs is None:
            raise ValueError("correlated_logs cannot be None")
```

#### 7. PollResult
```python
@dataclass
class PollResult:
    errors_found: int
    new_signatures: int
    diagnosed_signatures: int
    duration_seconds: float

    def __post_init__(self):
        if self.errors_found < 0:
            raise ValueError(f"errors_found must be >= 0, got {self.errors_found}")
        if self.new_signatures < 0:
            raise ValueError(f"new_signatures must be >= 0, got {self.new_signatures}")
        if self.diagnosed_signatures < 0:
            raise ValueError(
                f"diagnosed_signatures must be >= 0, got {self.diagnosed_signatures}"
            )
        if self.duration_seconds < 0:
            raise ValueError(
                f"duration_seconds must be >= 0, got {self.duration_seconds}"
            )
```

### Test Cases
```python
class TestConstructorValidation:
    """Verify all types validate at construction time."""

    def test_error_event_rejects_empty_trace_id(self):
        with pytest.raises(ValueError, match="trace_id cannot be empty"):
            ErrorEvent(trace_id="", span_id="1", ...)

    def test_error_event_rejects_future_timestamp(self):
        with pytest.raises(ValueError, match="timestamp cannot be in the future"):
            ErrorEvent(
                trace_id="1",
                span_id="2",
                timestamp=datetime.now() + timedelta(hours=1),
                ...
            )

    def test_diagnosis_rejects_invalid_confidence(self):
        with pytest.raises(ValueError, match="confidence must be 0.0-1.0"):
            Diagnosis(..., confidence=1.5)

    def test_poll_result_rejects_negative_counts(self):
        with pytest.raises(ValueError, match="errors_found must be >= 0"):
            PollResult(errors_found=-1, ...)
```

### Verification
- [ ] Add `__post_init__` to ErrorEvent
- [ ] Add `__post_init__` to Diagnosis
- [ ] Add `__post_init__` to StackFrame
- [ ] Add `__post_init__` to SpanNode
- [ ] Add `__post_init__` to TraceTree
- [ ] Add `__post_init__` to LogEntry
- [ ] Add `__post_init__` to InvestigationContext
- [ ] Add `__post_init__` to PollResult
- [ ] Run `pytest tests/core/test_models.py` - verify all pass
- [ ] Run full test suite - verify no regressions
- [ ] Check for any code constructing these types with invalid values

---

## Summary Table

| Deviation | File | Lines | Fix Time | Severity |
|-----------|------|-------|----------|----------|
| #1: Silent Failure | grafana_stack.py | 457-460 | 5 min | CRITICAL |
| #2: Mutable Signature | models.py | 92-127 | 3 hours | CRITICAL |
| #3: No Validation | models.py | Multiple | 2 hours | CRITICAL |
| Test Coverage | tests/ | Multiple | 2.5 hours | CRITICAL |
| **TOTAL** | — | — | **7.5 hours** | **CRITICAL** |

---

## Implementation Order

1. **Start with Deviation #3** (constructor validation)
   - Easiest to implement
   - Independent of other changes
   - Enables catching issues early

2. **Then Deviation #2** (make Signature frozen)
   - Builds on #3 validation
   - Requires updating multiple callers
   - More complex but well-defined

3. **Then Deviation #1** (fix Grafana Stack)
   - Simplest change
   - Can be done last
   - Verify no dependencies

4. **Finally, update tests**
   - Test all validation
   - Test state transitions
   - Test error handling

---

## Validation Checklist

- [ ] All 3 deviations fixed
- [ ] All 150+ existing tests pass
- [ ] New tests added for each fix
- [ ] No new type safety violations introduced
- [ ] Error messages are clear and actionable
- [ ] Documentation updated
- [ ] Code review completed
- [ ] Ready to merge
