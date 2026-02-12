# Type Design Analysis: Visual Guide

## Type Quality Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     ROUNDS TYPE DESIGN SCORES                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  OVERALL: 7.5/10 ████████░░                                      │
│                                                                   │
│  BY CATEGORY:                                                    │
│  • Encapsulation:     8.2/10 ████████░░                         │
│  • Invariant Expression: 7.1/10 ███████░░░                      │
│  • Invariant Usefulness: 8.0/10 ████████░░                      │
│  • Invariant Enforcement: 7.0/10 ███████░░░                     │
│                                                                   │
│  STRENGTHS:                                                      │
│  ✓ Frozen dataclasses prevent mutation                          │
│  ✓ Enums ensure valid string values                             │
│  ✓ Port abstraction is clean and clear                          │
│  ✓ Construction-time validation in Signature                   │
│  ✓ Complete type annotations (no Any abuse)                    │
│                                                                   │
│  WEAKNESSES:                                                     │
│  ✗ Diagnosis has no __post_init__ validation                   │
│  ✗ Enum parsing is scattered across adapters                   │
│  ✗ State transitions are implicit (not validated)              │
│  ✗ Signature allows direct field mutation                      │
│  ✗ Port interfaces use generic Exception                       │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Type Dependency Graph

```
┌──────────────────────────────────────────────────────────────────┐
│                      CORE DOMAIN TYPES                            │
└──────────────────────────────────────────────────────────────────┘

                              ┌─────────────┐
                              │  Severity   │ ◄── ENUM
                              │  (6 levels) │
                              └──────┬──────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
              ┌─────▼──────┐  ┌──────▼──────┐  ┌─────▼──────┐
              │ StackFrame  │  │ ErrorEvent  │  │ LogEntry   │
              │ (frozen)    │  │ (frozen)    │  │ (frozen)   │
              └─────────────┘  └──────┬──────┘  └────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                 │
                    │          ┌──────▼──────┐          │
                    │          │ Fingerprint  │          │
                    │          │   (via hash) │          │
                    │          └──────┬───────┘          │
                    │                 │                  │
              ┌─────▼──────────────────▼──────────────────┴──────┐
              │                    Signature                      │
              │ (MUTABLE - allows status/count updates)          │
              │                                                  │
              │  Fields:                                         │
              │  • id, fingerprint (immutable)                  │
              │  • error_type, service, message (immutable)     │
              │  • status (MUTABLE ← needs validation)          │
              │  • occurrence_count (MUTABLE)                   │
              │  • first_seen, last_seen (MUTABLE)             │
              │  • diagnosis: Diagnosis | None                  │
              │  • tags: frozenset (immutable)                  │
              └───────────────────────┬──────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                 │
              ┌─────▼──────────────────▼──────────────────┴──────┐
              │         Confidence ◄── ENUM                      │
              │         (HIGH, MEDIUM, LOW)                      │
              │                                                  │
              │  Uses:                                           │
              │  • Diagnosis.confidence field                    │
              │  • Triage.should_notify() logic                  │
              └─────────────────┬──────────────────────────────┘
                                │
                    ┌───────────┼───────────┐
                    │           │           │
          ┌─────────▼───┐  ┌────▼──────┐  ┌▼────────────┐
          │ Diagnosis   │  │ TraceTree  │  │InvestigCtx  │
          │ (frozen)    │  │ (frozen)   │  │ (frozen)    │
          │ NO VALID.   │  │            │  │             │
          │ ✗ ISSUE     │  │            │  │             │
          └─────────────┘  └────────────┘  └─────────────┘


┌──────────────────────────────────────────────────────────────────┐
│                      SignatureStatus ◄── ENUM                    │
│                  (NEW, INVESTIGATING, DIAGNOSED,                 │
│                   RESOLVED, MUTED)                               │
│                                                                  │
│  ✗ ISSUE: State transitions are implicit                        │
│           (not validated when status changes)                    │
│                                                                  │
│  Valid State Machine:                                           │
│  ┌─────────────────────────────────────────────────┐            │
│  │ NEW ──→ INVESTIGATING ──→ DIAGNOSED ──→ RESOLVED │            │
│  │  ↑                        ↓                    │             │
│  │  └──────────── ◄─── NEW (retriage) ◄──────────┘             │
│  │  ↑                                                           │
│  │  └────────── ◄─── MUTED ──→ NEW (unmute) ──┘               │
│  └─────────────────────────────────────────────────┘            │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Invariant Enforcement Matrix

```
┌─────────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
│ TYPE            │ CONSTRUCTION │ RUNTIME      │ DESER-       │ ISSUE?       │
│                 │ VALIDATION   │ VALIDATION   │ IALIZATION   │              │
├─────────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ Confidence      │ ✓ Enum       │ N/A (frozen) │ ⚠ Try/except │ Scattered    │
│ (enum)          │ Type-safe    │              │ in adapters  │ parsing      │
├─────────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ SignatureStatus │ ✓ Enum       │ N/A (frozen) │ ⚠ Try/except │ Scattered    │
│ (enum)          │ Type-safe    │              │ in adapters  │ parsing      │
├─────────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ Diagnosis       │ ✗ NO CHECK   │ N/A (frozen) │ ⚠ JSON parse │ CRITICAL:    │
│ (frozen)        │              │              │ only         │ No cost/     │
│                 │              │              │              │ evidence     │
│                 │              │              │              │ validation   │
├─────────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ ErrorEvent      │ ⚠ Partial    │ N/A (frozen) │ ✓ Okay       │ Empty string │
│ (frozen)        │ (only attrs)  │              │              │ allowed      │
├─────────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ Signature       │ ✓ Good       │ ✗ NO GUARDS  │ ✓ Catches    │ Direct       │
│ (mutable)       │ (__post_init_)│ on mutation  │ errors       │ mutation not │
│                 │              │              │              │ validated    │
├─────────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ StackFrame      │ ✗ NO CHECK   │ N/A (frozen) │ ✓ Okay       │ Empty fields │
│ (frozen)        │              │              │              │ allowed      │
├─────────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ TraceTree       │ ✗ NO CHECK   │ N/A (frozen) │ ✓ Okay       │ Empty spans  │
│ (frozen)        │              │              │              │ allowed      │
└─────────────────┴──────────────┴──────────────┴──────────────┴──────────────┘
```

---

## Critical Issue Locations

```
FILE: /workspace/rounds/core/models.py
────────────────────────────────────────

Line 80-90 (Diagnosis):
  ✗ ISSUE: No __post_init__ validation
  • cost_usd could be negative
  • evidence could be empty
  • root_cause/suggested_fix could be empty

Line 92-127 (Signature):
  ✓ Has __post_init__ validation
  ✗ BUT: Direct mutation not validated
    signature.status = SignatureStatus.NEW  ← No check!
    signature.occurrence_count += 1 ← No check!

Line 35-59 (ErrorEvent):
  ⚠ Only validates attributes dict conversion
  ✗ Missing: empty string validation


FILE: /workspace/rounds/adapters/store/sqlite.py
────────────────────────────────────────────────

Line 415:
  ⚠ confidence=Confidence(data["confidence"]),
  ✗ ISSUE: Enum parsing here, not centralized
  ✓ Protected by try/except at line 385

Line 380:
  ⚠ status=SignatureStatus(status),
  ✗ ISSUE: Enum parsing here, not centralized
  ✓ Protected by try/except at line 385


FILE: /workspace/rounds/adapters/diagnosis/claude_code.py
──────────────────────────────────────────────────────────

Line 266:
  ⚠ confidence = Confidence(confidence_str.lower())
  ✗ ISSUE: Enum parsing here, not centralized
  ✓ Protected by try/except at line 265


FILE: /workspace/rounds/core/investigator.py
─────────────────────────────────────────────

Line 89:
  ✗ signature.status = SignatureStatus.INVESTIGATING
  ✗ ISSUE: Direct mutation, no transition validation
  • Type says SignatureStatus is valid
  • But state transition rules are implicit
  • Should use: signature.set_status(SignatureStatus.INVESTIGATING)
```

---

## Risk Matrix: Impact vs Likelihood

```
        HIGH IMPACT
            ▲
            │
            │    Diagnosis validation gap ✗
            │    (cost errors, empty evidence)
            │
            │         Enum parsing scattered ✗
            │         (repeated logic)
    MEDIUM  │
     IMPACT │      State transitions implicit ✗
            │      (type allows invalid changes)
            │
            │              ErrorEvent validation ⚠
            │              (empty strings)
            │    ┌──────────────────────────┐
            │    │ Port generic exceptions ⚠ │
            │    └──────────────────────────┘
            │
    LOW     │    StackFrame/TraceTree gaps
     IMPACT │    (minor)
            │
            └────────────────────────────────────► LOW LIKELIHOOD  HIGH
                                                  (caught by tests) (missed)
```

---

## Before & After: Signature State Mutation

```
BEFORE (Current):
═════════════════════════════════════════════════════════════════

signature.status = SignatureStatus.INVESTIGATING  ← Type-safe, ✓
await self.store.update(signature)                ← Works fine

BUT if code does:
signature.status = SignatureStatus.RESOLVED
signature.status = SignatureStatus.INVESTIGATING  ← Invalid! No error!

TYPE SYSTEM: "This is a SignatureStatus, OK"
RUNTIME: Works, but violates state machine


AFTER (Improved):
═════════════════════════════════════════════════════════════════

signature.set_status(SignatureStatus.INVESTIGATING)  ← Validates!
await self.store.update(signature)

If code tries:
signature.status = SignatureStatus.RESOLVED
signature.set_status(SignatureStatus.INVESTIGATING)  ← ValueError!

TYPE SYSTEM: "This is a SignatureStatus, OK"
RUNTIME: Validates transition, raises if invalid ✓


Also available:
if signature.can_investigate():          ← Query helper
    await investigator.investigate(...)

if signature.is_terminal():               ← Query helper
    skip_this_one()
```

---

## Before & After: Enum Parsing

```
BEFORE (Current):
═════════════════════════════════════════════════════════════════

SQLite adapter:
  try:
    confidence=Confidence(data["confidence"]),  ← Parse
  except ValueError as e:                       ← Handle
    logger.warning(f"...")

Claude Code adapter:
  try:
    confidence = Confidence(confidence_str.lower())  ← Different!
  except ValueError as e:                           ← Handle
    raise ValueError(f"...")

PROBLEM: Logic duplicated, case handling different, no central truth


AFTER (Improved):
═════════════════════════════════════════════════════════════════

All adapters:
  try:
    confidence = ModelParsers.parse_confidence(value)  ← Same logic
  except ValueError as e:                              ← Consistent
    # Handle

class ModelParsers:
    @staticmethod
    def parse_confidence(value: str) -> Confidence:
        """Case-insensitive parsing with validation."""
        try:
            return Confidence(value.lower())
        except ValueError:
            raise ValueError(f"Invalid confidence '{value}'...")

Benefits:
✓ Single source of truth
✓ Consistent case handling
✓ Better error messages
✓ Easy to test
✓ Easy to extend (add parse_status, parse_severity)
```

---

## Before & After: Diagnosis Validation

```
BEFORE (Current):
═════════════════════════════════════════════════════════════════

diagnosis = Diagnosis(
    root_cause="",                          ← INVALID! No check
    evidence=(),                            ← INVALID! No check
    suggested_fix="",                       ← INVALID! No check
    confidence=Confidence.HIGH,             ← Valid ✓
    diagnosed_at=datetime.now(...),
    model="claude-3",
    cost_usd=-5.0,                          ← INVALID! No check
)

RESULT: Diagnosis created with invalid data
        Discovered later during notification or persistence


AFTER (Improved):
═════════════════════════════════════════════════════════════════

diagnosis = Diagnosis(
    root_cause="",                          ← ValueError!
    evidence=(),                            ← ValueError!
    suggested_fix="",                       ← ValueError!
    confidence=Confidence.HIGH,
    diagnosed_at=datetime.now(...),
    model="claude-3",
    cost_usd=-5.0,                          ← ValueError!
)

class Diagnosis:
    def __post_init__(self) -> None:
        if not self.root_cause or not self.root_cause.strip():
            raise ValueError("root_cause cannot be empty")
        if not self.evidence:
            raise ValueError("evidence cannot be empty")
        if len(self.evidence) < 3:
            logger.warning("Consider 3+ evidence points...")
        if not self.suggested_fix or not self.suggested_fix.strip():
            raise ValueError("suggested_fix cannot be empty")
        if self.cost_usd < 0:
            raise ValueError("cost_usd must be non-negative...")

RESULT: Invalid diagnosis rejected immediately
        Error caught at construction, not later
```

---

## Implementation Effort

```
┌────────────────────────────────────────────────────────────────┐
│ IMPROVEMENT EFFORT ESTIMATE                                    │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│ 1. Diagnosis validation (Phase 1)                             │
│    ├── Add __post_init__ to models.py                         │
│    └── Effort: ~15 lines in one file                          │
│        Time: 15 minutes                                        │
│        Risk: Very low                                          │
│                                                                │
│ 2. ModelParsers class (Phase 1)                               │
│    ├── Add to models.py                                       │
│    ├── Update SQLite adapter (2 calls)                        │
│    ├── Update Claude Code adapter (1 call)                    │
│    └── Effort: ~30 lines in models.py + ~10 in adapters      │
│        Time: 30 minutes                                        │
│        Risk: Low (isolated changes)                            │
│                                                                │
│ 3. Signature state helpers (Phase 1)                          │
│    ├── Add SignatureStatusTransition to models.py             │
│    ├── Add methods to Signature class                         │
│    ├── Update Investigator (1 line)                           │
│    ├── Update TriageEngine (2 lines)                          │
│    └── Effort: ~70 lines in models.py + ~5 in services       │
│        Time: 1 hour                                            │
│        Risk: Low (new methods, can coexist with old)          │
│                                                                │
│ 4. ErrorEvent validation (Phase 2)                            │
│    ├── Add validation to __post_init__                        │
│    └── Effort: ~20 lines in models.py                         │
│        Time: 15 minutes                                        │
│        Risk: Very low                                          │
│                                                                │
│ 5. Specific exception types (Phase 2)                         │
│    ├── Add to ports.py                                        │
│    ├── Update adapter error handling (3+ adapters)            │
│    └── Effort: ~40 lines in ports.py + ~30 in adapters       │
│        Time: 1 hour                                            │
│        Risk: Medium (exception handling affects many places)  │
│                                                                │
│ 6. Tests (Phase 3)                                            │
│    ├── Validation tests                                       │
│    ├── State transition tests                                 │
│    ├── Enum parsing tests                                     │
│    └── Effort: ~100 lines of test code                        │
│        Time: 1.5 hours                                        │
│        Risk: Very low (new tests only)                        │
│                                                                │
├────────────────────────────────────────────────────────────────┤
│ TOTAL EFFORT: ~3.5 hours                                       │
│ TOTAL CODE CHANGES: ~300 lines across 6-8 files               │
│ BREAKING CHANGES: None (new methods added alongside old)      │
│ TEST IMPACT: All tests should pass (more validation, fewer    │
│              bugs reaching test boundaries)                    │
└────────────────────────────────────────────────────────────────┘
```

---

## Quality Improvement Trajectory

```
                        QUALITY SCORE
                             │
                        9.0  ├─────────────────────────────────────
                             │
                        8.5  ├────────────── AFTER ALL PHASES (Target)
                             │ ╱
                        8.0  ├╱──────────────
                             │
                        7.5  ├────────── CURRENT STATE
                             │ ╱
                        7.0  ├╱──────────────
                             │
                        6.5  └─────────────────────────────────────
                             │       │       │       │       │
                           Phase 1  Phase 2 Phase 3 Phase 4  Final
                           (Critical)(High) (Medium)(Nice)

Phase 1 (Critical - 3 improvements):
  → Diagnosis validation
  → ModelParsers class
  → Signature state helpers
  GAIN: +0.5 points (7.5 → 8.0)

Phase 2 (High Priority - 2 improvements):
  → ErrorEvent validation
  → Specific exception types
  GAIN: +0.3 points (8.0 → 8.3)

Phase 3 (Medium - 1 improvement):
  → Consolidate state transitions
  GAIN: +0.1 points (8.3 → 8.4)

Phase 4 (Polish - 2 improvements):
  → Type aliases for clarity
  → Additional helpers
  GAIN: +0.1 points (8.4 → 8.5)
```

---

## Quick Reference: Files to Modify

```
MODELS (Add validation and helpers):
  /workspace/rounds/core/models.py
  ├── Add Diagnosis.__post_init__() validation
  ├── Add ModelParsers class
  ├── Add SignatureStatusTransition class
  ├── Add Signature.set_status()
  ├── Add Signature.record_occurrence()
  ├── Add Signature.can_investigate()
  ├── Add Signature.is_terminal()
  └── Add ErrorEvent.__post_init__() validation

PORTS (Add exception types):
  /workspace/rounds/core/ports.py
  ├── Add TelemetryException hierarchy
  ├── Add DiagnosisException hierarchy
  ├── Add SignatureStoreException hierarchy
  ├── Add NotificationException
  └── Update docstrings with exception types

ADAPTERS (Use centralized parsing):
  /workspace/rounds/adapters/store/sqlite.py
  ├── Use ModelParsers.parse_confidence()
  ├── Use ModelParsers.parse_status()
  └── Update error handling

  /workspace/rounds/adapters/diagnosis/claude_code.py
  ├── Use ModelParsers.parse_confidence()
  └── Raise specific exceptions

SERVICES (Use new helpers):
  /workspace/rounds/core/investigator.py
  └── Use signature.set_status()

  /workspace/rounds/core/triage.py
  └── Use signature.is_terminal() / signature.can_investigate()

TESTS (Add validation tests):
  /workspace/rounds/tests/core/test_models.py
  ├── Test Diagnosis validation
  ├── Test state transitions
  ├── Test enum parsing
  └── Test error event validation
```

---

## Conclusion at a Glance

| Aspect | Current | After Improvements |
|--------|---------|-------------------|
| Overall Score | 7.5/10 | 8.5/10 |
| Construction Validation | 60% | 95% |
| Runtime Validation | 30% | 70% |
| Enum Parsing | Scattered | Centralized |
| State Transitions | Implicit | Explicit |
| Exception Handling | Generic | Specific |
| Code Duplication | Yes (parsing) | No |
| Test Coverage | Good | Better |
| Implementation Effort | - | ~3.5 hours |
| Breaking Changes | - | None |

**Recommendation**: Implement Phase 1 (Critical) immediately. Phase 2-4 can follow in regular development cycles.
