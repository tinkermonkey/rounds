# Type Design Review Summary with File:Line References

**Analysis Date**: February 12, 2026
**Branch**: `feature/issue-1-sketch-out-the-project-archite` vs `main`

---

## Quick Reference: Type Ratings

| Type | File:Line | Encapsulation | Expression | Usefulness | Enforcement | Overall | Risk Level |
|------|-----------|---------------|-----------|-----------|-----------|---------|----|
| ErrorEvent | models.py:36-58 | 9/10 | 9/10 | 8/10 | 8/10 | **8.5/10** | Medium |
| Signature | models.py:92-127 | 6/10 | 7/10 | 9/10 | 5/10 | **6.75/10** | **CRITICAL** |
| Diagnosis | models.py:79-90 | 9/10 | 8/10 | 9/10 | 7/10 | **8.25/10** | Low |
| StackFrame | models.py:14-21 | 9/10 | 8/10 | 7/10 | 6/10 | **7.5/10** | Low |
| InvestigationContext | models.py:179-192 | 9/10 | 8/10 | 8/10 | 8/10 | **8.25/10** | Low |
| SpanNode | models.py:129-149 | 9/10 | 8/10 | 8/10 | 7/10 | **8/10** | Low |
| TraceTree | models.py:151-158 | 9/10 | 8/10 | 7/10 | 6/10 | **7.5/10** | Low |
| LogEntry | models.py:160-177 | 9/10 | 8/10 | 8/10 | 7/10 | **8/10** | Low |
| PollResult | models.py:194-203 | 8/10 | 7/10 | 8/10 | 6/10 | **7.25/10** | Medium |

---

## Critical Issues by Severity

### SEVERITY: CRITICAL

#### Issue 1: Signature Type is Mutable (Encapsulation Violation)
**Files**:
- Definition: `/workspace/rounds/core/models.py:92-127`
- Usage: `/workspace/rounds/core/management_service.py:33-123`
- Usage: `/workspace/rounds/core/poll_service.py:73-128`

**Problem**:
```python
# Problem: Signature is mutable (no frozen=True)
@dataclass
class Signature:
    id: str
    status: SignatureStatus  # Can be modified after creation
    diagnosis: Diagnosis | None  # Can be set at any time
    last_seen: datetime  # Can be set backwards
    occurrence_count: int  # Can be decremented
```

**Where it's violated**:
- `/workspace/rounds/core/management_service.py:51` - Direct status mutation
- `/workspace/rounds/core/management_service.py:83` - Direct status mutation
- `/workspace/rounds/core/management_service.py:115` - Direct diagnosis mutation
- `/workspace/rounds/core/poll_service.py:106-107` - Mutable field updates

**Impact**:
- Invariants (last_seen >= first_seen, occurrence_count >= 1) can be violated post-construction
- No audit trail for state changes
- Concurrent mutations could corrupt state
- Type system doesn't prevent invalid state transitions

**Recommended Fix**:
Add validation methods instead of direct field mutation:
```python
@dataclass
class Signature:
    # ... fields ...

    def set_status(self, new_status: SignatureStatus) -> None:
        """Set status with transition validation."""
        if self.status == SignatureStatus.RESOLVED:
            raise ValueError("Cannot change RESOLVED signature status")
        self.status = new_status
```

Or convert to frozen with builder pattern:
```python
@dataclass(frozen=True)
class Signature:
    def with_status(self, status: SignatureStatus) -> "Signature":
        return replace(self, status=status)
```

---

### SEVERITY: HIGH

#### Issue 2: Incomplete Constructor Validation
**Files**: Multiple
- `/workspace/rounds/core/models.py:36-58` (ErrorEvent)
- `/workspace/rounds/core/models.py:79-90` (Diagnosis)
- `/workspace/rounds/core/models.py:129-149` (SpanNode)
- `/workspace/rounds/core/models.py:160-177` (LogEntry)
- `/workspace/rounds/core/models.py:194-203` (PollResult)

**Problem**: Many types lack `__post_init__` validation for:
- Non-empty strings (trace_id, span_id, service, root_cause, suggested_fix, etc.)
- Non-negative numerics (cost_usd, duration_ms)
- Non-empty collections (evidence, stack_frames)

**Examples of Invalid Objects**:
```python
# ErrorEvent with empty trace_id (should fail but doesn't)
ErrorEvent(
    trace_id="",  # Invalid but accepted
    span_id="span-1",
    # ...
)

# Diagnosis with negative cost (should fail but doesn't)
Diagnosis(
    root_cause="...",
    cost_usd=-1.50,  # Invalid but accepted
    # ...
)

# PollResult with negative counts (should fail but doesn't)
PollResult(
    errors_found=-5,  # Invalid but accepted
    # ...
)
```

**Recommended Fix**: Add validation in `__post_init__` for all types:
```python
@dataclass(frozen=True)
class ErrorEvent:
    # ... fields ...

    def __post_init__(self) -> None:
        if not self.trace_id or not self.trace_id.strip():
            raise ValueError("trace_id cannot be empty")
        if not self.span_id or not self.span_id.strip():
            raise ValueError("span_id cannot be empty")
        # ... more validation ...
```

---

#### Issue 3: Opaque dict[str, Any] Returns in Ports
**Files**:
- `/workspace/rounds/core/ports.py:244-252` (SignatureStorePort.get_stats)
- `/workspace/rounds/core/ports.py:465-483` (ManagementPort.get_signature_details)

**Problem**: Methods return untyped dicts, losing all type safety:
```python
# get_stats returns opaque dict (unclear what keys/values exist)
@abstractmethod
async def get_stats(self) -> dict[str, Any]:
    """Summary statistics for reporting."""

# get_signature_details returns opaque dict (caller must know schema)
@abstractmethod
async def get_signature_details(self, signature_id: str) -> dict[str, Any]:
    """Retrieve detailed information about a signature."""
```

**Impact**:
- Type checker can't verify callers use correct keys
- No IDE autocomplete for accessing fields
- Callers must know undocumented schema
- Easy to introduce bugs (typos in key names)

**Recommended Fix**: Define structured return types:
```python
@dataclass(frozen=True)
class SignatureDetails:
    """Full details about a signature."""
    id: str
    fingerprint: str
    error_type: str
    service: str
    occurrence_count: int
    status: SignatureStatus
    diagnosis: Diagnosis | None
    tags: frozenset[str]
    related_signatures: list["SignatureDetails"]

@dataclass(frozen=True)
class StoreStats:
    """Statistics about signature store."""
    total_signatures: int
    new_count: int
    investigating_count: int
    diagnosed_count: int
    # ... more fields ...

class SignatureStorePort(ABC):
    @abstractmethod
    async def get_stats(self) -> StoreStats:  # Typed!
        """Summary statistics for reporting."""
```

**File Changes Required**:
- `/workspace/rounds/core/models.py` - Add SignatureDetails, StoreStats types
- `/workspace/rounds/core/ports.py:244` - Change return type
- `/workspace/rounds/core/ports.py:465` - Change return type
- `/workspace/rounds/core/management_service.py:125-199` - Return typed object instead of dict
- Any adapters - Update to return typed objects

---

#### Issue 4: State Machine Constraints Not Enforced
**Files**:
- `/workspace/rounds/core/models.py:92-127` (Signature definition)
- `/workspace/rounds/core/management_service.py:33-123` (State transitions)
- `/workspace/rounds/core/triage.py:29-52` (Decision logic)

**Problem**: No type-level enforcement of valid state transitions:
```python
# These transitions should be invalid but type system allows them:
sig = Signature(..., status=SignatureStatus.RESOLVED)
sig.status = SignatureStatus.NEW  # Should be prevented!

sig = Signature(..., status=SignatureStatus.MUTED)
sig.diagnosis = some_diagnosis  # Should be prevented!

sig = Signature(..., last_seen=datetime(...))
sig.last_seen = datetime(2000, 1, 1)  # Should be prevented!
```

**Valid Transitions** (should be enforced):
- NEW → INVESTIGATING → DIAGNOSED
- NEW → MUTED (suppress)
- DIAGNOSED → MUTED (suppress later)
- Any → RESOLVED (final)
- Any → NEW (retriage)

**Recommended Fix**: Add transition validation:
```python
def set_status(self, new_status: SignatureStatus) -> None:
    """Transition to new status with validation."""
    valid_next = {
        SignatureStatus.NEW: {SignatureStatus.INVESTIGATING, SignatureStatus.MUTED, SignatureStatus.RESOLVED},
        SignatureStatus.INVESTIGATING: {SignatureStatus.DIAGNOSED, SignatureStatus.NEW},
        SignatureStatus.DIAGNOSED: {SignatureStatus.MUTED, SignatureStatus.RESOLVED, SignatureStatus.NEW},
        SignatureStatus.MUTED: {SignatureStatus.RESOLVED, SignatureStatus.NEW},
        SignatureStatus.RESOLVED: {SignatureStatus.NEW},  # Only retriage allowed
    }

    if new_status not in valid_next.get(self.status, set()):
        raise ValueError(
            f"Cannot transition from {self.status} to {new_status}"
        )
    self.status = new_status
```

---

#### Issue 5: Diagnosis-Status Relationship Not Enforced
**Files**:
- `/workspace/rounds/core/models.py:92-115` (Signature with diagnosis field)
- `/workspace/rounds/core/investigator.py:39-138` (Sets diagnosis when INVESTIGATING)

**Problem**: No type constraint that diagnosis is only set on DIAGNOSED signatures:
```python
# These should be invalid but type allows them:
sig = Signature(..., status=SignatureStatus.NEW, diagnosis=diagnosis)  # Wrong!
sig = Signature(..., status=SignatureStatus.MUTED, diagnosis=diagnosis)  # Wrong!

# Current code correctly does this (investigator.py:115-116)
signature.diagnosis = diagnosis  # But nothing prevents wrong status
signature.status = SignatureStatus.DIAGNOSED
```

**Recommended Fix**: Add validation in Signature:
```python
def set_diagnosis(self, diagnosis: Diagnosis) -> None:
    """Set diagnosis only when status allows it."""
    if self.status not in {SignatureStatus.INVESTIGATING, SignatureStatus.DIAGNOSED}:
        raise ValueError(
            f"Cannot set diagnosis on {self.status} signature"
        )
    self.diagnosis = diagnosis
```

Or in `__post_init__`:
```python
def __post_init__(self) -> None:
    # ... existing validation ...
    if self.diagnosis is not None and self.status == SignatureStatus.NEW:
        raise ValueError(
            "Cannot have diagnosis on NEW signature (should be DIAGNOSED)"
        )
```

---

### SEVERITY: MEDIUM

#### Issue 6: No Validation of Non-Negative Numerics
**Files**:
- `/workspace/rounds/core/models.py:79-90` (Diagnosis.cost_usd)
- `/workspace/rounds/core/models.py:129-149` (SpanNode.duration_ms)
- `/workspace/rounds/core/models.py:194-203` (PollResult counts)

**Problem**: Numeric fields accept invalid negative values:
```python
# All of these should fail but don't:
Diagnosis(..., cost_usd=-0.50)  # Negative cost
SpanNode(..., duration_ms=-100.5)  # Negative duration
PollResult(errors_found=-10, ...)  # Negative count
```

**Recommended Fix**: Add validation in `__post_init__`:
```python
def __post_init__(self) -> None:
    if self.cost_usd < 0:
        raise ValueError(f"cost_usd must be non-negative, got {self.cost_usd}")
```

---

#### Issue 7: Cost Estimation Accuracy Not Type-Expressed
**Files**: `/workspace/rounds/core/ports.py:255-305` (DiagnosisPort)

**Problem**: No type guarantee that estimated cost <= actual cost:
```python
# Port defines both methods but no relationship constraint:
@abstractmethod
async def estimate_cost(self, context: InvestigationContext) -> float:
    """Estimate the cost (in USD) of diagnosing a signature."""

@abstractmethod
async def diagnose(self, context: InvestigationContext) -> Diagnosis:
    """Invoke LLM analysis on an investigation context."""
    # Returns Diagnosis with cost_usd field
    # But nothing guarantees estimate <= actual cost
```

**Impact**: Budget enforcement could fail if adapter returns higher actual cost than estimate

**Recommended Fix**: Document invariant in docstring, or add validation in caller:
```python
# In investigator or poll_service:
estimated = await diagnosis_engine.estimate_cost(context)
if estimated > budget:
    raise BudgetExceededError(f"Estimated ${estimated} exceeds budget ${budget}")

diagnosis = await diagnosis_engine.diagnose(context)
if diagnosis.cost_usd > estimated:
    logger.warning(
        f"Actual cost ${diagnosis.cost_usd} exceeded estimate ${estimated}"
    )
```

---

## File-by-File Summary

### `/workspace/rounds/core/models.py`

| Lines | Type | Issues | Priority |
|-------|------|--------|----------|
| 14-21 | StackFrame | No validation of non-empty strings | P2 |
| 24-32 | Severity | ✓ Good - Enum constrains values | - |
| 36-58 | ErrorEvent | No validation of trace_id/span_id non-empty | P2 |
| 61-77 | SignatureStatus | ✓ Good - Enum constrains values | - |
| 71-77 | Confidence | ✓ Good - Enum constrains values | - |
| 79-90 | Diagnosis | No validation of cost_usd >= 0 or non-empty strings | P2 |
| 92-127 | **Signature** | **CRITICAL**: Mutable type, violates encapsulation; no state transition validation; no diagnosis-status constraint | P1 |
| 129-149 | SpanNode | No validation of duration_ms >= 0 or non-empty strings | P2 |
| 151-158 | TraceTree | No validation of trace_id non-empty | P2 |
| 160-177 | LogEntry | No validation of body non-empty | P2 |
| 179-192 | InvestigationContext | No validation of codebase_path non-empty | P2 |
| 194-203 | PollResult | No validation of non-negative counts | P2 |

---

### `/workspace/rounds/core/ports.py`

| Lines | Port/Method | Issues | Priority |
|-------|---|--------|----------|
| 41-148 | TelemetryPort | ✓ Good port design | - |
| 150-253 | SignatureStorePort | `get_stats()` returns opaque `dict[str, Any]` | P2 |
| 244-252 | .get_stats() | Returns `dict[str, Any]` instead of typed object | P2 |
| 255-305 | DiagnosisPort | Cost estimation accuracy not guaranteed | P3 |
| 307-347 | NotificationPort | ✓ Good port design | - |
| 354-411 | PollPort | "Fatal vs transient" distinction documented but not enforced | P3 |
| 413-484 | ManagementPort | `get_signature_details()` returns opaque `dict[str, Any]` | P2 |
| 465-483 | .get_signature_details() | Returns `dict[str, Any]` instead of SignatureDetails type | P2 |

---

### `/workspace/rounds/core/management_service.py`

| Lines | Code | Issues | Priority |
|-------|------|--------|----------|
| 33-64 | mute_signature() | Direct mutation of signature.status (line 51) violates encapsulation | P1 |
| 65-96 | resolve_signature() | Direct mutation of signature.status (line 83) violates encapsulation | P1 |
| 97-124 | retriage_signature() | Direct mutation of signature.status, diagnosis (lines 114-115) | P1 |
| 125-199 | get_signature_details() | Returns untyped dict instead of SignatureDetails | P2 |

---

### `/workspace/rounds/core/poll_service.py`

| Lines | Code | Issues | Priority |
|-------|------|--------|----------|
| 86-101 | execute_poll_cycle() | Creates Signature directly with all fields | ✓ Good |
| 106-108 | execute_poll_cycle() | Direct mutation of signature.last_seen, occurrence_count | P1 |

---

### `/workspace/rounds/core/investigator.py`

| Lines | Code | Issues | Priority |
|-------|------|--------|----------|
| 89 | investigate() | Sets signature.status = INVESTIGATING (mutation) | P1 |
| 115 | investigate() | Sets signature.diagnosis directly (should validate) | P1 |
| 116 | investigate() | Sets signature.status = DIAGNOSED (mutation) | P1 |

---

## Validation Checklist

The following types need `__post_init__` validation additions:

- [ ] ErrorEvent: non-empty trace_id, span_id
- [ ] Diagnosis: non-negative cost_usd, non-empty root_cause, suggested_fix, non-empty evidence
- [ ] StackFrame: non-empty module, function, filename
- [ ] SpanNode: non-negative duration_ms, non-empty span_id, service, operation
- [ ] TraceTree: non-empty trace_id
- [ ] LogEntry: non-empty body
- [ ] InvestigationContext: non-empty codebase_path
- [ ] PollResult: non-negative counts
- [ ] Signature: occurrence_count >= 1, last_seen >= first_seen (existing), add: state transition validation, diagnosis-status constraint

---

## Test Coverage Impact

Current tests: `/workspace/rounds/tests/core/test_services.py`

**Tests that would break with stricter validation** (good - catch invalid objects):
- Tests creating fixtures with empty strings would fail
- Tests creating PollResult with negative counts would fail
- Tests setting invalid status transitions would fail

**Recommendation**: Update test fixtures to use valid values, which is the right thing to do anyway.

---

## Migration Path

1. **Phase 1 (P1)**: Fix Signature mutability (CRITICAL)
   - Add validation methods OR
   - Convert to frozen with builders
   - Update all mutation sites

2. **Phase 2 (P2)**: Add comprehensive validation (HIGH)
   - Add __post_init__ validation to all types
   - Define SignatureDetails and StoreStats types
   - Update port method signatures

3. **Phase 3 (P3)**: Document and validate constraints (NICE TO HAVE)
   - Add custom exception types
   - Enforce cost estimation accuracy
   - Document and validate graceful degradation patterns

