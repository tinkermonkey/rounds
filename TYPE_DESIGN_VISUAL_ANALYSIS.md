# Type Design: Visual Analysis & Comparisons

## 1. Immutability Approaches Across Domain Types

### Exemplary: ErrorEvent (Fully Protected)

```
┌─────────────────────────────────────┐
│        ErrorEvent (frozen=True)      │
├─────────────────────────────────────┤
│ - trace_id: str                     │
│ - span_id: str                      │
│ - service: str                      │
│ - error_type: str                   │
│ - error_message: str                │
│ - stack_frames: tuple[...] ✓        │  Immutable collection
│ - timestamp: datetime               │
│ - attributes: MappingProxyType ✓    │  Read-only dict wrapper
│ - severity: Severity (enum)         │
├─────────────────────────────────────┤
│ Result: COMPLETELY IMMUTABLE        │
│ No way to modify after creation     │
│ Thread-safe by design               │
│ ✓ Risk: ZERO                        │
└─────────────────────────────────────┘
```

### Good: Diagnosis (Mostly Protected)

```
┌─────────────────────────────────────┐
│        Diagnosis (frozen=True)       │
├─────────────────────────────────────┤
│ - root_cause: str                   │
│ - evidence: tuple[str, ...] ✓       │  Immutable collection
│ - suggested_fix: str                │
│ - confidence: Confidence ✓          │  Literal["high"|"medium"|"low"]
│ - diagnosed_at: datetime            │
│ - model: str                        │
│ - cost_usd: float (validated ✓)    │
├─────────────────────────────────────┤
│ __post_init__: Validates cost >= 0  │
│ ✓ Issue: evidence can be empty      │
│         (should validate non-empty) │
├─────────────────────────────────────┤
│ Result: EFFECTIVELY IMMUTABLE       │
│ Validation incomplete               │
│ ✓ Risk: LOW                         │
└─────────────────────────────────────┘
```

### Problematic: Signature (Mutable But Not Well Protected)

```
┌──────────────────────────────────────────────────┐
│        Signature (NOT frozen - mutable)          │
├──────────────────────────────────────────────────┤
│ - id: str                                        │
│ - fingerprint: str                               │
│ - error_type: str                                │
│ - service: str                                   │
│ - message_template: str                          │
│ - stack_hash: str                                │
│ - first_seen: datetime                           │
│ - last_seen: datetime (⚠ DIRECT MUTATION OK)   │
│ - occurrence_count: int (⚠ DIRECT MUTATION OK)  │
│ - status: SignatureStatus (⚠ DIRECT MUTATION OK)│
│ - diagnosis: Diagnosis | None                    │
│ - tags: frozenset[str] ✓                         │
├──────────────────────────────────────────────────┤
│ __post_init__:                                   │
│   ✓ occurrence_count >= 1                        │
│   ✓ last_seen >= first_seen                      │
│   ⚠ diagnosis != None → status == DIAGNOSED?     │
│                          NOT CHECKED!            │
├──────────────────────────────────────────────────┤
│ Methods (supposed to enforce mutations):         │
│   ✓ record_occurrence()                          │
│   ✓ mark_investigating()                         │
│   ✓ mark_diagnosed()                             │
│   ✓ mark_resolved()                              │
│   ✓ mark_muted()                                 │
├──────────────────────────────────────────────────┤
│ REALITY: Callers bypass methods with direct      │
│ assignment:                                      │
│   ⚠ signature.last_seen = timestamp              │
│   ⚠ signature.occurrence_count += 1              │
│   ⚠ signature.status = Status.NEW                │
│   ⚠ signature.diagnosis = diag                    │
├──────────────────────────────────────────────────┤
│ Result: WEAKLY PROTECTED MUTABLE                 │
│ Methods exist but not enforced                   │
│ Direct mutations bypass validation               │
│ ✓ Risk: HIGH - Invariant violations possible    │
└──────────────────────────────────────────────────┘
```

---

## 2. Invariant Enforcement Comparison

### Type: Confidence (TypeAlias)

```
┌──────────────────────────────────────────┐
│ Confidence: TypeAlias =                  │
│   Literal["high", "medium", "low"]       │
├──────────────────────────────────────────┤
│ COMPILE-TIME CHECK:                      │
│   diagnosis = Diagnosis(                 │
│     confidence="invalid"  ✓ TYPE ERROR   │
│   )                                      │
│                                          │
│ RUNTIME CHECK: None (not needed)         │
│                                          │
│ Invariant: confidence in valid set       │
│ Enforcement: 100% (type checker)         │
│ Cost: Zero (compile-time only)           │
│                                          │
│ ✓ EXEMPLARY                              │
└──────────────────────────────────────────┘
```

### Type: Diagnosis.cost_usd

```
┌──────────────────────────────────────────┐
│ @dataclass(frozen=True)                  │
│ class Diagnosis:                         │
│   cost_usd: float                        │
│                                          │
│   def __post_init__(self):               │
│     if self.cost_usd < 0:                │
│       raise ValueError(...)    ✓ CHECK  │
├──────────────────────────────────────────┤
│ COMPILE-TIME CHECK: None                 │
│   (type allows any float)                │
│                                          │
│ RUNTIME CHECK:                           │
│   diagnosis = Diagnosis(                 │
│     cost_usd=-5.0  ✓ ValueError raised   │
│   )                                      │
│                                          │
│ Invariant: cost_usd >= 0                 │
│ Enforcement: 100% (runtime __post_init__)│
│ Cost: Cheap (runs once at creation)      │
│                                          │
│ ✓ GOOD                                   │
└──────────────────────────────────────────┘
```

### Type: Signature.occurrence_count

```
┌──────────────────────────────────────────┐
│ @dataclass                               │
│ class Signature:                         │
│   occurrence_count: int                  │
│                                          │
│   def __post_init__(self):               │
│     if self.occurrence_count < 1:        │
│       raise ValueError(...)    ✓ CHECK  │
│                                          │
│   def record_occurrence(self, ...):      │
│     self.occurrence_count += 1           │
├──────────────────────────────────────────┤
│ COMPILE-TIME CHECK: None                 │
│                                          │
│ RUNTIME CHECK:                           │
│   AT CREATION:                           │
│   sig = Signature(                       │
│     occurrence_count=0  ✓ ValueError     │
│   )                                      │
│                                          │
│   AFTER CREATION:                        │
│   sig.occurrence_count = -5  ✓ NO CHECK │
│   sig.occurrence_count += 1  ✓ NO CHECK │
│                                          │
│ Invariant: occurrence_count >= 1         │
│ Enforcement: 50% (at creation only)      │
│             (direct mutation bypasses!)  │
│                                          │
│ ⚠ PROBLEMATIC                            │
└──────────────────────────────────────────┘
```

### Type: Signature.diagnosis + status

```
┌──────────────────────────────────────────┐
│ @dataclass                               │
│ class Signature:                         │
│   diagnosis: Diagnosis | None            │
│   status: SignatureStatus                │
│                                          │
│   def mark_diagnosed(self, d: Diagnosis):│
│     self.diagnosis = d                   │
│     self.status = SignatureStatus.DIAG.. │
├──────────────────────────────────────────┤
│ Invariant to enforce:                    │
│   (diagnosis != None) ↔ (status == DIAG) │
│                                          │
│ COMPILE-TIME CHECK: None                 │
│                                          │
│ RUNTIME CHECK:                           │
│   AT CREATION:                           │
│   sig = Signature(                       │
│     diagnosis=None,                      │
│     status=SignatureStatus.DIAGNOSED     │
│   )  ✓ INCONSISTENT! But no error!       │
│                                          │
│   AFTER CREATION:                        │
│   sig.diagnosis = Diagnosis(...)         │
│   sig.status = SignatureStatus.NEW       │
│   ✓ INCONSISTENT! No check!              │
│                                          │
│ Invariant: diagnosis != None ↔           │
│             status == DIAGNOSED          │
│ Enforcement: 0% (not checked!)           │
│                                          │
│ ✗ CRITICAL GAP                           │
└──────────────────────────────────────────┘
```

---

## 3. Mutation Pattern Comparison

### Pattern 1: Direct Field Mutation (Current)

```python
# In poll_service.py
signature = await store.get_by_fingerprint(fp)
if signature:
    # ⚠ Direct mutation - bypasses validation
    signature.last_seen = error.timestamp
    signature.occurrence_count += 1
    await store.update(signature)
```

**Problems**:
- Bypasses `record_occurrence()` method which enforces timestamp ordering
- Multiple operations (update timestamp AND occurrence) aren't atomic
- If `error.timestamp < first_seen`, no error is raised
- Violates encapsulation - callers know internal structure

**Risk Level**: HIGH

---

### Pattern 2: Method-Based Mutation (Intended)

```python
# What the code SHOULD do:
signature = await store.get_by_fingerprint(fp)
if signature:
    signature.record_occurrence(error.timestamp)  # ✓ Enforces invariants
    await store.update(signature)
```

**Benefits**:
- Enforces timestamp ordering check
- Atomic operation
- Encapsulated - timestamp/count management is hidden
- If timestamp is invalid, raises ValueError

**Risk Level**: LOW

---

### Pattern 3: Immutable Builder Pattern (Alternative)

```python
# What could be done if mutable approach is problematic:
signature = await store.get_by_fingerprint(fp)
if signature:
    new_signature = signature.with_occurrence(error.timestamp)  # Returns new copy
    await store.update(new_signature)
```

**Benefits**:
- Completely immutable - no mutation side effects
- Clear "before/after" semantics
- Easy to reason about
- No accidental state corruption

**Cost**: Extra copying on each update

---

## 4. Port Interface Quality Assessment

### TelemetryPort: Exception Handling

```
CURRENT (Unsafe):
┌─────────────────────────────────┐
│ async def get_recent_errors()   │
│   -> list[ErrorEvent]:          │
│                                 │
│ Raises:                         │
│   Exception: If backend down    │
├─────────────────────────────────┤
│ Problem:                        │
│ - Catches all exceptions        │
│ - No type safety                │
│ - Hard to handle specific cases │
│                                 │
│ if fetch fails:                 │
│   Can't distinguish:            │
│   - Timeout vs.                 │
│   - Connection refused vs.      │
│   - Invalid query syntax        │
└─────────────────────────────────┘

IMPROVED (Type-Safe):
┌──────────────────────────────────┐
│ class TelemetryError(Exception):  │
│   """Base telemetry error."""     │
│                                  │
│ class TelemetryTimeout(...)       │
│ class TelemetryConnection(...)    │
│ class TelemetryInvalid(...)       │
│                                  │
│ async def get_recent_errors()    │
│   -> list[ErrorEvent]:           │
│                                  │
│ Raises:                          │
│   TelemetryTimeout: If > 30s     │
│   TelemetryConnection: Unreachble│
│   TelemetryError: Other errors   │
├──────────────────────────────────┤
│ Benefits:                        │
│ - Type-safe exception handling   │
│ - Callers can handle specific    │
│   error types                    │
│ - Clear error semantics          │
└──────────────────────────────────┘
```

---

## 5. Configuration Validation Coverage

### Current State

```
Settings Validation Coverage:

✓ poll_interval_seconds > 0
✓ poll_batch_size > 0
✓ claude_code_budget_usd >= 0
✓ daily_budget_limit >= 0
✓ error_lookback_minutes > 0
✓ webhook_port in [1, 65535]
✓ telemetry_backend in {signoz, jaeger, grafana_stack}
✓ store_backend in {sqlite, postgresql}
✓ diagnosis_backend in {claude_code, openai}
✓ notification_backend in {stdout, markdown, github_issue}
✓ log_level in {DEBUG, INFO, WARNING, ERROR, CRITICAL}
✓ log_format in {json, text}
✓ run_mode in {daemon, cli, webhook}

✗ Cross-field validations:
   claude_code_budget_usd <= daily_budget_limit?
   openai_budget_usd <= daily_budget_limit?
   If github_issue backend: github_repo required?
   If webhook mode: webhook_port configured?
```

**Gap**: No validation that per-diagnosis budget fits in daily budget

**Risk**: Could set per-diagnosis=$50, daily=$10 and hit budget errors at runtime

---

## 6. Rating Scorecard

### Encapsulation Comparison

```
Perfect (10/10):
  - Confidence (TypeAlias) - read-only by definition

Excellent (9/10):
  - ErrorEvent - frozen + MappingProxyType
  - Diagnosis - frozen + validation
  - Settings - pydantic immutability (unless mutated)
  - Port Interfaces - abstract, no leaky implementation

Good (8/10):
  - TraceTree/SpanNode/LogEntry - frozen but could validate more

Acceptable (7/10):
  - SignatureStatus - enum prevents invalid values

Poor (6/10):
  - Signature - mutable with methods that aren't enforced
```

### Invariant Expression Comparison

```
Perfect (10/10):
  - Confidence: Literal["high", "medium", "low"]

Excellent (9/10):
  - ErrorEvent - frozen makes immutability clear
  - Settings - validators document constraints
  - Diagnosis - cost constraint clear

Good (8/10):
  - Diagnosis - confidence TypeAlias clear
  - TraceTree/SpanNode - frozen makes intent clear
  - Port interfaces - docstrings thorough

Acceptable (7/10):
  - Signature - methods document transitions, but direct mutation allowed
  - SignatureStatus - enum clear, but state machine not expressed

Poor (6/10):
  - None
```

---

## 7. Invariant Violation Scenarios

### Scenario 1: Negative Occurrence Count

```python
# This is PREVENTED:
sig = Signature(occurrence_count=-1, ...)
# → ValueError at __post_init__ ✓

# But this bypasses validation:
sig.occurrence_count = -1  # No error ✗
```

### Scenario 2: Reversed Timestamps

```python
# This is PREVENTED:
sig = Signature(
    first_seen=datetime(2024, 1, 2),
    last_seen=datetime(2024, 1, 1),
    ...
)
# → ValueError at __post_init__ ✓

# But this bypasses validation:
sig.last_seen = datetime(2023, 1, 1)  # No error ✗
```

### Scenario 3: Diagnosis Without Status

```python
# This is PREVENTED:
sig = Signature(diagnosis=my_diagnosis, status=NEW, ...)
# → Should raise ValueError but currently DOESN'T ✗

# And this bypasses validation:
sig.diagnosis = my_diagnosis
sig.status = NEW  # Inconsistent! No error ✗
```

### Scenario 4: Invalid Status Transition

```python
# This is PREVENTED:
sig.mark_resolved()
sig.mark_resolved()  # ValueError: already resolved ✓

# But this bypasses validation:
sig.status = RESOLVED
sig.status = NEW  # Invalid transition! No error ✗
```

---

## Summary Table: Type Design Quality

| Dimension | Best-in-Class | Good | Needs Work |
|-----------|---|---|---|
| **Immutability** | ErrorEvent, Diagnosis | TraceTree | **Signature** |
| **Validation** | Diagnosis, Settings | ErrorEvent | **Signature** |
| **Encapsulation** | Confidence, Diagnosis | ErrorEvent | **Signature** |
| **Type Safety** | Confidence, Settings | Ports | ErrorEvent |
| **Enforcement** | Diagnosis.__post_init__ | Settings validators | **Signature methods** |
| **Documentation** | Ports (docstrings) | Settings | Models |

**Overall**: Solid architecture with one persistent weakness in Signature mutation.
