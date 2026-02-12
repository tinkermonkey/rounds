# Type Design Analysis: Quick Summary

**File Location**: `/workspace/TYPE_DESIGN_ANALYSIS.md` (full detailed report)

## Overall Assessment: 7.5/10

The Rounds project has **strong foundational type design** with excellent use of frozen dataclasses, enums, and clear port abstractions. However, specific invariant enforcement gaps create runtime vulnerability points.

---

## Key Findings

### What's Working Well ✓

1. **Frozen Dataclasses** - ErrorEvent, Diagnosis, TraceTree, and other domain entities use immutable frozen dataclasses. This prevents accidental mutations and makes invariants clearer.

2. **Enum Constraints** - Confidence, SignatureStatus, and Severity are properly implemented as enums, preventing typos and invalid string values at compile time.

3. **Port Abstraction** - Clean abstract base classes (TelemetryPort, DiagnosisPort, SignatureStorePort, NotificationPort) with clear method contracts and comprehensive docstrings.

4. **Type Annotations** - All code uses complete type hints. No `Any` abuse. Return types and parameters are explicit.

5. **Construction Validation** - Signature.__post_init__() validates critical invariants (occurrence_count >= 1, temporal ordering).

---

## Critical Issues Found

### 1. **Diagnosis Has No Validation** (Highest Risk)

**Location**: `/workspace/rounds/core/models.py:80-90`

Diagnosis is frozen but has no __post_init__ validation. This allows:
- `cost_usd = -5.0` (negative costs)
- `evidence = ()` (empty evidence tuple)
- `root_cause = ""` (empty string)

**Impact**: Invalid diagnosis objects can persist in the system and cause issues in cost tracking and notification logic.

**Fix**: Add validation method (see full report, Phase 1, Item 1)

---

### 2. **Enum Parsing is Scattered** (High Risk)

**Locations**:
- `/workspace/rounds/adapters/store/sqlite.py:415` - `Confidence(data["confidence"])`
- `/workspace/rounds/adapters/diagnosis/claude_code.py:266` - `Confidence(confidence_str.lower())`
- `/workspace/rounds/adapters/store/sqlite.py:380` - `SignatureStatus(status)`

Each adapter implements its own try/except error handling. This pattern is error-prone and duplicated.

**Impact**: If parsing logic changes or new adapters are added, parsing errors could be missed.

**Fix**: Centralize in a `ModelParsers` class (see full report, Phase 1, Item 2)

---

### 3. **Signature State Mutations Lack Validation**

**Location**: `/workspace/rounds/core/investigator.py:89`

```python
signature.status = SignatureStatus.NEW  # Direct mutation, no validation
```

The type allows mutating status directly, but state transitions have implicit rules:
- NEW → INVESTIGATING → DIAGNOSED/RESOLVED/MUTED (forward only)
- But retriage allows: DIAGNOSED → NEW

These rules are checked in service code (TriageEngine, Investigator, ManagementService) but the type doesn't enforce them.

**Impact**: Code can set invalid transitions (e.g., MUTED → INVESTIGATING) and type-checker won't catch it.

**Fix**: Create `SignatureStatusTransition` class and add `Signature.set_status()` helper (see full report, Phase 1/2)

---

## Medium-Risk Issues

### 4. **Port Interfaces Use Generic Exception**

TelemetryPort, DiagnosisPort, and SignatureStorePort all raise generic `Exception`. This makes error handling generic:

```python
try:
    diagnosis = await self.diagnosis_engine.diagnose(context)
except Exception as e:  # Too broad!
    # Can't distinguish between timeout, budget exceeded, model error
```

**Fix**: Define specific exception types (DiagnosisTimeoutError, TelemetryUnavailableError, etc.)

### 5. **ErrorEvent Has No Field Validation**

ErrorEvent allows empty strings: `error_type=""`, `service=""`, `error_message=""`

These are immutable but invalid, causing failures in fingerprinting.

**Fix**: Add __post_init__ validation for non-empty required fields

### 6. **State Transition Rules Are Distributed**

Rules like "don't re-investigate RESOLVED signatures" appear in:
- `/workspace/rounds/core/triage.py:38-39` (don't investigate if RESOLVED/MUTED)
- `/workspace/rounds/core/management_service.py` (allow retriage to NEW)

No single source of truth for the state machine.

**Fix**: Create `SignatureStatusTransition` class with transition rules

---

## Code Snippets: Where Issues Manifest

### Issue 1: Diagnosis Validation Gap
```python
# /workspace/rounds/adapters/diagnosis/claude_code.py:273-281
return Diagnosis(
    root_cause=root_cause,           # Could be ""
    evidence=evidence,               # Could be ()
    suggested_fix=suggested_fix,     # Could be ""
    confidence=confidence,           # Validated ✓
    diagnosed_at=datetime.now(timezone.utc),
    model=self.model,
    cost_usd=0.0,                    # Will be overwritten, but could be negative
)
```

### Issue 2: Scattered Parsing
```python
# /workspace/rounds/adapters/store/sqlite.py:415
confidence=Confidence(data["confidence"]),  # Direct conversion, requires try/except outside

# /workspace/rounds/adapters/diagnosis/claude_code.py:266
confidence = Confidence(confidence_str.lower())  # Different pattern, repeated logic
```

### Issue 3: Unvalidated Mutation
```python
# /workspace/rounds/core/investigator.py:89
signature.status = SignatureStatus.INVESTIGATING  # Type checker OK, but no transition validation
await self.store.update(signature)
```

---

## Recommended Fixes (Priority Order)

### Phase 1: Critical (Do First)
1. **Add Diagnosis.__post_init__() validation**
   - Validate cost_usd >= 0
   - Validate evidence is non-empty and has >= 3 items
   - Validate root_cause and suggested_fix are non-empty

2. **Create ModelParsers class**
   - Centralize Confidence.parse(value)
   - Centralize SignatureStatus.parse(value)
   - Update adapters to use these methods

3. **Add Signature.set_status() helper**
   - Replace direct mutations with `signature.set_status(new_status)`
   - Validates state transitions using SignatureStatusTransition

### Phase 2: High Priority
4. **Define specific exception types**
   - DiagnosisException, TelemetryException base classes
   - Specific subclasses: TimeoutError, BudgetExceededError, ModelError, etc.
   - Update port interfaces to document which exceptions they raise

5. **Add ErrorEvent.__post_init__() validation**
   - Validate error_type, service, error_message are non-empty
   - Validate error_type matches expected pattern

### Phase 3: Medium Priority
6. **Create SignatureStatusTransition class**
   - Document all valid transitions
   - Add validate_or_raise() method
   - Use in all status mutation points

---

## Files Involved

**Core Models** (validation needed):
- `/workspace/rounds/core/models.py` - Diagnosis, Signature, ErrorEvent
- `/workspace/rounds/core/ports.py` - Exception types for ports

**Adapters** (update to use centralized parsing):
- `/workspace/rounds/adapters/store/sqlite.py` - Lines 415, 380
- `/workspace/rounds/adapters/diagnosis/claude_code.py` - Line 266

**Services** (update to use helpers):
- `/workspace/rounds/core/investigator.py` - Line 89
- `/workspace/rounds/core/management_service.py` - Status mutations

---

## Testing Impact

**Tests that would catch these issues**:
1. Unit tests for Diagnosis validation edge cases
2. Deserialization tests with corrupted enum values
3. State transition tests (valid and invalid paths)
4. ErrorEvent creation with invalid fields

**Existing test coverage**: Tests use valid fixtures, so these issues aren't caught. See `/workspace/rounds/tests/` for test files.

---

## Bottom Line

**Current State**: Type design is solid but has runtime vulnerability points
- Strengths: Frozen dataclasses, enums, port abstraction
- Weaknesses: Weak invariant enforcement in Diagnosis/ErrorEvent, scattered parsing logic, implicit state transitions

**After Phase 1 improvements**: Would reach 8.5/10
- Validation gaps closed
- Parsing centralized
- State transitions explicit

**Effort**: ~200-300 lines of code changes across models.py, adapters, and services. No architectural changes needed.
