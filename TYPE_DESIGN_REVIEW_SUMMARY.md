# Type Design Review Summary - Actionable Items

## Key Findings

**Overall**: Strong type system with excellent immutable data types and port abstraction. One critical issue: `Signature` class allows direct field mutation that bypasses validation methods.

---

## Critical Issue: Signature Mutation Enforcement

### The Problem

The `Signature` class (lines 94-163 in `/workspace/rounds/core/models.py`) has validation methods but callers bypass them with direct field assignment:

**In `/workspace/rounds/core/poll_service.py` (lines 113-114)**:
```python
signature.last_seen = error.timestamp       # ⚠ Bypasses record_occurrence()
signature.occurrence_count += 1             # ⚠ No validation
```

**In `/workspace/rounds/core/investigator.py` (lines 100, 107, 131)**:
```python
signature.status = SignatureStatus.INVESTIGATING  # ⚠ Bypasses mark_investigating()
signature.status = SignatureStatus.NEW             # ⚠ No transition check
signature.status = SignatureStatus.DIAGNOSED      # ⚠ No consistency check
```

This violates two critical invariants:
1. Occurrence count and last_seen ordering (enforce via record_occurrence method)
2. Status transitions follow state machine rules (enforce via mark_* methods)

### Immediate Fix (30 minutes)

**Option A: Require Method Usage** (recommended)

Replace all direct mutations with method calls:

```python
# In poll_service.py, change:
signature.last_seen = error.timestamp
signature.occurrence_count += 1

# To:
signature.record_occurrence(error.timestamp)

# ----

# In investigator.py, change:
signature.status = SignatureStatus.INVESTIGATING
await self.store.update(signature)

# To:
signature.mark_investigating()
await self.store.update(signature)

# And change:
signature.status = SignatureStatus.DIAGNOSED
signature.diagnosis = diagnosis

# To:
signature.mark_diagnosed(diagnosis)
```

**Option B: Atomic Status + Diagnosis Update** (better)

Add new method to Signature for atomic updates:

```python
# In models.py
def set_diagnosed(self, diagnosis: Diagnosis) -> None:
    """Atomically set diagnosis and status to DIAGNOSED.

    Prevents inconsistency where diagnosis is set but status isn't.
    """
    self.diagnosis = diagnosis
    self.status = SignatureStatus.DIAGNOSED
```

---

## High Priority: Missing Invariant Check

### The Problem

No validation that if `diagnosis != None`, then `status == DIAGNOSED`:

```python
# This is currently possible and invalid:
signature = Signature(...)
signature.diagnosis = Diagnosis(...)  # Set diagnosis
signature.status = SignatureStatus.NEW  # But wrong status
```

### Fix

Add to `Signature.__post_init__()` (line 118):

```python
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

    # NEW: Check diagnosis-status consistency
    if self.diagnosis is not None and self.status != SignatureStatus.DIAGNOSED:
        raise ValueError(
            f"Signature has diagnosis but status is {self.status}, "
            f"expected DIAGNOSED. Diagnosis and status must be synchronized."
        )
```

---

## Medium Priority: Configuration Validation

### The Problem

No check that per-diagnosis budget doesn't exceed daily limit:

```python
# This could be invalid:
claude_code_budget_usd: 50.0    # Per diagnosis
daily_budget_limit: 10.0        # Daily total (impossible!)
```

### Fix

Add to `Settings` class in `/workspace/rounds/config.py`:

```python
from pydantic import model_validator

@model_validator(mode='after')
def validate_budget_constraints(self) -> "Settings":
    """Validate cross-field budget relationships."""
    if self.claude_code_budget_usd > self.daily_budget_limit:
        raise ValueError(
            f"Per-diagnosis budget (${self.claude_code_budget_usd}) "
            f"cannot exceed daily limit (${self.daily_budget_limit})"
        )
    if self.openai_budget_usd > self.daily_budget_limit:
        raise ValueError(
            f"OpenAI per-diagnosis budget (${self.openai_budget_usd}) "
            f"cannot exceed daily limit (${self.daily_budget_limit})"
        )
    return self
```

---

## Medium Priority: Evidence Validation

### The Problem

`Diagnosis.evidence` tuple can be empty:

```python
diagnosis = Diagnosis(
    root_cause="...",
    evidence=(),              # ⚠ Empty - invalid!
    suggested_fix="...",
    confidence="high",
    ...
)
```

### Fix

Add to `Diagnosis.__post_init__()` (line 86):

```python
def __post_init__(self) -> None:
    """Validate diagnosis invariants on creation."""
    if self.cost_usd < 0:
        raise ValueError(
            f"cost_usd must be non-negative, got {self.cost_usd}"
        )

    # NEW: Validate evidence is non-empty
    if not self.evidence:
        raise ValueError(
            "Diagnosis must include at least one piece of evidence. "
            "Empty evidence tuple indicates incomplete analysis."
        )
```

---

## Ratings Summary

| Type | Issue | Status |
|------|-------|--------|
| **Signature** | Direct mutation bypasses validation | **CRITICAL** |
| **Signature** | No diagnosis-status consistency check | **HIGH** |
| **Diagnosis** | Empty evidence tuple not validated | **MEDIUM** |
| **Settings** | Cross-field budget not validated | **MEDIUM** |
| **Settings** | GitHub config redundancy | **LOW** |
| **ErrorEvent** | ✓ Excellent (frozen + read-only attrs) | OK |
| **TraceTree/SpanNode/LogEntry** | ✓ Excellent (frozen) | OK |
| **Confidence TypeAlias** | ✓ Exemplary use of Literal | OK |
| **Ports** | Documentation good, types could be stricter | OK |

---

## Files Requiring Changes

1. **`/workspace/rounds/core/models.py`** (3 additions)
   - Add diagnosis-status consistency check to `Signature.__post_init__()`
   - Add evidence non-emptiness check to `Diagnosis.__post_init__()`
   - Add `set_diagnosed()` atomic method to `Signature` (recommended)

2. **`/workspace/rounds/core/poll_service.py`** (1 change)
   - Replace direct mutation with `signature.record_occurrence(error.timestamp)`

3. **`/workspace/rounds/core/investigator.py`** (3 changes)
   - Replace `signature.status = INVESTIGATING` with `signature.mark_investigating()`
   - Replace `signature.status = NEW` with method (need to add)
   - Replace `signature.status = DIAGNOSED; signature.diagnosis = ...` with atomic method

4. **`/workspace/rounds/config.py`** (1 addition)
   - Add `@model_validator` for cross-field budget validation

---

## Estimated Effort

- **Critical fix** (Signature mutation enforcement): 30 minutes
- **High priority** (Invariant checks): 15 minutes
- **Medium priority** (Config validation): 15 minutes
- **Testing/verification**: 30 minutes
- **Total**: ~90 minutes

All changes maintain backward compatibility at the API level (no external callers).

---

## Testing Checklist

After applying fixes, verify:

- [ ] All `Signature` creations still work
- [ ] Direct mutations are replaced with method calls
- [ ] `Signature.__post_init__()` catches invalid diagnosis-status combinations
- [ ] `Diagnosis.__post_init__()` catches empty evidence
- [ ] `Settings` validation catches budget violations
- [ ] All existing tests pass
- [ ] New test cases for invariant violations

---

## Files Reviewed

- `/workspace/rounds/core/models.py` - Domain entities (258 lines)
- `/workspace/rounds/core/ports.py` - Port interfaces (538 lines)
- `/workspace/rounds/config.py` - Configuration (262 lines)
- `/workspace/rounds/core/poll_service.py` - Usage example (167 lines)
- `/workspace/rounds/core/investigator.py` - Usage example (153 lines)

See `/workspace/TYPE_DESIGN_REVIEW.md` for detailed analysis of all types.
