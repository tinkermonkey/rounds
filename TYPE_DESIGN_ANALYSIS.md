# Type Design Analysis: Rounds Core Models & Ports

## Executive Summary

The Rounds project demonstrates **strong foundational type design** with excellent use of frozen dataclasses, enums, and clear port abstractions. However, there are **specific invariant enforcement gaps** that could allow runtime errors, particularly around deserialization and confidence level validation.

**Overall Quality Score: 7.5/10**

The design excels at compile-time guarantees but has runtime vulnerability points that should be addressed.

---

## Type: Confidence

### Invariants Identified
- Only three valid states: HIGH, MEDIUM, LOW
- Values are fixed strings used across diagnosis, notification, and triage decisions
- Must be parsed from external data (JSON responses from LLMs) and database values
- Triage engine compares confidence levels by identity (using `==` against enum instances)

### Ratings
- **Encapsulation**: 9/10
  Enum provides perfect encapsulation. Cannot instantiate invalid confidence values. All creation points use enum constructors.

- **Invariant Expression**: 9/10
  The enum clearly expresses all valid states. Code using Confidence must explicitly reference enum members, making valid states obvious.

- **Invariant Usefulness**: 9/10
  Prevents the critical bug of accepting arbitrary confidence strings like "uncertain", "maybe", or misspelled values. These would silently fail in triage logic comparisons.

- **Invariant Enforcement**: 8/10
  Properly enforced at construction, but **deserialization in SQLite adapter and Claude Code adapter must handle ValueError**. If `Confidence(invalid_value)` is called with corrupted data, it raises ValueError. The adapters do handle this, but the error path isn't obvious from the type alone.

### Strengths
- Enum provides bulletproof type safety
- Triage engine correctly uses enum for comparisons
- Deserialization error handling exists in adapters
- Clear semantic meaning (HIGH > MEDIUM > LOW is obvious to readers)

### Concerns
- **Deserialization vulnerability**: SQLite adapter line 415 and Claude Code adapter line 266 must catch ValueError when parsing confidence from external data. While they do handle it, the pattern isn't obvious.
- **No parsing helper**: Repeated try/except pattern in adapters suggests opportunity for centralized, type-safe parsing.
- **Case sensitivity in LLM output**: Claude Code adapter accepts "HIGH|MEDIUM|LOW" but must handle case normalization (see line 266: `Confidence(confidence_str.lower())`). This works but the responsibility is on the adapter, not the type.

### Recommended Improvements

1. **Add a factory method for safe parsing** (reduces burden on adapters):
```python
class Confidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def parse(cls, value: str) -> "Confidence":
        """Parse confidence from external data (case-insensitive).

        Args:
            value: String value to parse

        Returns:
            Confidence enum instance

        Raises:
            ValueError: If value is not a valid confidence level
        """
        try:
            return cls(value.lower())
        except ValueError:
            valid_values = [c.value for c in cls]
            raise ValueError(
                f"Invalid confidence '{value}'. "
                f"Must be one of {valid_values}"
            ) from None
```

Then adapters use: `confidence = Confidence.parse(result.get("confidence", ""))`

This centralizes the parsing logic and makes the expectation explicit.

---

## Type: SignatureStatus

### Invariants Identified
- Five valid states: NEW, INVESTIGATING, DIAGNOSED, RESOLVED, MUTED
- State transitions are directional (NEW → INVESTIGATING → DIAGNOSED/RESOLVED/MUTED, not back)
- Muted and Resolved are terminal states (triage.py line 38-39: don't re-investigate if RESOLVED or MUTED)
- Store queries use status for filtering (e.g., get_pending_investigation returns only NEW)

### Ratings
- **Encapsulation**: 9/10
  Enum provides perfect encapsulation. Status cannot be arbitrary strings.

- **Invariant Expression**: 6/10
  The enum expresses valid values, but **state transition rules are implicit in service code**, not expressed in the type. For example:
  - Can you transition DIAGNOSED → RESOLVED? Yes (triage allows it)
  - Can you transition RESOLVED → NEW? Yes, via retriage (management_service.py would allow this)
  - Can you transition MUTED → NEW? Yes, via retriage

  These rules are encoded in service logic, not in the type. Someone reading just the enum wouldn't know the transition graph.

- **Invariant Usefulness**: 8/10
  Prevents typos (STATUS_DIAGONSED vs DIAGNOSED). Enables store queries with type safety. Helps triage engine avoid re-investigating already-resolved errors.

- **Invariant Enforcement**: 8/10
  Enforcement is good for the enum values themselves, but state transition rules are checked only in services, not at construction time. The type doesn't prevent creating a Signature with RESOLVED status and then later transitioning it to INVESTIGATING.

### Strengths
- Enum prevents arbitrary status strings
- Store queries use `.value` to convert to/from database strings
- Triage logic correctly checks status before investigation
- Clear names (no ambiguous "COMPLETED" vs "DONE")

### Concerns
- **State transition rules live in service code**: Triage engine, Investigator, and ManagementService enforce different rules, but there's no single source of truth for valid transitions.
- **No validation at transition time**: When code does `signature.status = SignatureStatus.NEW`, nothing checks if that's a valid transition from the current state.
- **Implicit terminal states**: MUTED and RESOLVED are terminal, but the type doesn't express this. Code must check `if signature.status in {RESOLVED, MUTED}` in multiple places.

### Recommended Improvements

1. **Create a StateTransitionPolicy class** to make transitions explicit:
```python
class SignatureStatusTransition:
    """Encodes valid state transitions for signatures."""

    # Define valid transitions as a mapping
    VALID_TRANSITIONS = {
        SignatureStatus.NEW: {
            SignatureStatus.INVESTIGATING,
            SignatureStatus.MUTED,
        },
        SignatureStatus.INVESTIGATING: {
            SignatureStatus.DIAGNOSED,
            SignatureStatus.NEW,  # Re-triage
        },
        SignatureStatus.DIAGNOSED: {
            SignatureStatus.RESOLVED,
            SignatureStatus.MUTED,
            SignatureStatus.NEW,  # Re-investigate
        },
        SignatureStatus.RESOLVED: {
            SignatureStatus.NEW,  # Retriage if regression
        },
        SignatureStatus.MUTED: {
            SignatureStatus.NEW,  # Unmute via retriage
        },
    }

    @staticmethod
    def is_valid(from_status: SignatureStatus, to_status: SignatureStatus) -> bool:
        """Check if transition is allowed."""
        return to_status in SignatureTransition.VALID_TRANSITIONS.get(from_status, set())

    @staticmethod
    def validate_or_raise(from_status: SignatureStatus, to_status: SignatureStatus) -> None:
        """Validate transition or raise."""
        if not SignatureStatusTransition.is_valid(from_status, to_status):
            raise ValueError(
                f"Invalid transition: {from_status.value} -> {to_status.value}"
            )
```

Then in Signature or services, call `SignatureStatusTransition.validate_or_raise(old, new)` before mutating.

2. **Add helper methods to Signature** (if making it immutable in future):
```python
def can_investigate(self) -> bool:
    """Check if this signature can be sent to diagnosis engine."""
    return self.status in {SignatureStatus.NEW, SignatureStatus.INVESTIGATING}

def is_terminal(self) -> bool:
    """Check if signature has reached a terminal state."""
    return self.status in {SignatureStatus.RESOLVED, SignatureStatus.MUTED}
```

---

## Type: Diagnosis

### Invariants Identified
- All fields are required and immutable
- `confidence` must be a valid Confidence enum value (enforced by type annotation)
- `cost_usd` must be non-negative (not validated)
- `evidence` is a tuple (immutable) with at least one element (not validated)
- `diagnosed_at` should reflect when diagnosis was created, but field is set explicitly

### Ratings
- **Encapsulation**: 9/10
  Frozen dataclass prevents all mutation. All fields are public read-only.

- **Invariant Expression**: 7/10
  The type clearly expresses that confidence is a Confidence enum and cost is a float. However:
  - No minimum evidence requirement (single evidence point seems weak)
  - No validation that cost is non-negative
  - No docstring documenting that evidence should have 3+ items (documented in prompt, not type)

- **Invariant Usefulness**: 8/10
  Prevents accidental mutation of diagnosis. Prevents confidence typos. Helps with cost tracking and budget enforcement.

- **Invariant Enforcement**: 6/10
  **Weak**: There is no constructor validation:
  - `cost_usd` could be -5.0 or 1000.0 without bounds checking
  - `evidence` could be an empty tuple
  - `root_cause`, `suggested_fix` could be empty strings

  The type annotation `Diagnosis(confidence: Confidence)` only guarantees the enum type, not that it's a valid value at construction time.

### Strengths
- Frozen dataclass prevents post-creation mutations
- `confidence` field ensures only valid enum values (at type-check time)
- Tuple for evidence prevents accidental modification
- Clear field names and types

### Concerns
- **No validation in __post_init__**: Unlike Signature, Diagnosis has no validation. Cost, evidence, and text fields could be invalid.
- **Empty evidence edge case**: Type allows `evidence=()`, but an empty tuple of evidence is clearly wrong semantically.
- **Cost bounds not enforced**: Type allows negative or absurdly high costs. Budget enforcement relies on adapter logic, not the type.
- **No minimum string length**: `root_cause=""` and `suggested_fix=""` are technically valid but useless.

### Recommended Improvements

1. **Add __post_init__ validation**:
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
        """Validate diagnosis invariants on creation."""
        if not self.root_cause or not self.root_cause.strip():
            raise ValueError("root_cause cannot be empty")
        if not self.suggested_fix or not self.suggested_fix.strip():
            raise ValueError("suggested_fix cannot be empty")
        if not self.evidence:
            raise ValueError("evidence tuple cannot be empty")
        if len(self.evidence) < 3:
            logger.warning(
                f"Diagnosis has fewer than 3 evidence points ({len(self.evidence)}). "
                f"Consider including more supporting evidence."
            )
        if self.cost_usd < 0:
            raise ValueError(f"cost_usd must be non-negative, got {self.cost_usd}")
```

This prevents invalid Diagnosis instances from existing in the system.

---

## Type: Signature

### Invariants Identified
- `occurrence_count` must be >= 1 (documented in __post_init__)
- `last_seen` must be >= `first_seen` (documented in __post_init__)
- `id` and `fingerprint` must be non-empty strings
- `status` must be a valid SignatureStatus enum value
- `diagnosis` is optional (None or valid Diagnosis)
- `tags` is a frozenset (immutable)
- Intentionally mutable to allow status/occurrence updates (documented in class docstring)

### Ratings
- **Encapsulation**: 7/10
  **Mixed**: Signature is intentionally mutable (not frozen) to allow updates, which is a design choice. However:
  - Direct field mutations are allowed: `signature.status = SignatureStatus.NEW` (used in investigator.py line 89)
  - No setters to validate transitions
  - Mutable fields (`status`, `occurrence_count`, `last_seen`) have no guards

  The design accepts this trade-off for simplicity, but it means invariants are enforced only at construction time.

- **Invariant Expression**: 7/10
  The type expresses:
  - occurrence_count is int (but not that it must be >= 1 until __post_init__)
  - last_seen is datetime (but not that it must be >= first_seen until __post_init__)
  - status is SignatureStatus enum
  - diagnosis is optional Diagnosis

  The invariants are **partially expressed** in the type signature and **completed** in __post_init__.

- **Invariant Usefulness**: 8/10
  Checking occurrence_count and temporal ordering prevents bugs where:
  - A signature with 0 occurrences exists in the store
  - A signature's last_seen is before first_seen (indicating corrupted data)
  - Status is an invalid string

  These checks catch real bugs that could come from deserialization.

- **Invariant Enforcement**: 7/10
  **Construction time is solid**:
  - __post_init__ validates occurrence_count and temporal ordering
  - Store adapter catches invalid Confidence when deserializing diagnosis

  **Runtime mutation is weak**:
  - Direct field mutation bypasses validation
  - No setter methods to guard transitions
  - investigator.py line 89: `signature.status = SignatureStatus.NEW` is valid for type-checker but has no transition validation
  - No validation when incrementing occurrence_count

### Strengths
- __post_init__ validates critical invariants at construction
- Enum status prevents arbitrary status strings
- Immutable tags (frozenset) prevent modification
- Store adapter validates on deserialization (lines 344-346, 380)
- Clear docstring explaining intentional mutability

### Concerns
- **Direct field mutation without validation**: Code directly mutates `signature.status` and `signature.last_seen` without checks. investigator.py and management_service.py do this.
- **No occurrence_count validation on mutation**: Code can do `signature.occurrence_count += 1` without checking new value stays positive (unlikely in practice, but possible in edge case).
- **SQLite adapter deserialization vulnerability** (line 380): `SignatureStatus(status)` is called directly. If database contains an invalid status string (e.g., from corruption), this raises ValueError. The adapter catches the exception (line 385-390), but the error path is in the exception handler.
- **Missing validation on some mutable fields**: When deserializing, `last_seen` is set to a datetime that was earlier validated in __post_init__. But if code mutates `last_seen` to an invalid value, no check prevents it.

### Recommended Improvements

1. **Provide safe setter methods** instead of direct field mutation:
```python
@dataclass
class Signature:
    # ... existing fields ...

    def set_status(self, new_status: SignatureStatus) -> None:
        """Update signature status with validation.

        Raises:
            ValueError: If transition is invalid.
        """
        SignatureStatusTransition.validate_or_raise(self.status, new_status)
        self.status = new_status

    def record_occurrence(self, timestamp: datetime) -> None:
        """Record a new error occurrence, updating timestamp and count.

        Args:
            timestamp: When the error was observed.

        Raises:
            ValueError: If timestamp is before first_seen.
        """
        if timestamp < self.first_seen:
            raise ValueError(
                f"Occurrence timestamp {timestamp} is before "
                f"first_seen {self.first_seen}"
            )
        self.last_seen = timestamp
        self.occurrence_count += 1
```

Then code changes from:
```python
signature.status = SignatureStatus.INVESTIGATING
await self.store.update(signature)
```

To:
```python
signature.set_status(SignatureStatus.INVESTIGATING)
await self.store.update(signature)
```

2. **Add a factory method for safe deserialization**:
```python
@classmethod
def from_database(cls, **kwargs) -> "Signature":
    """Construct a Signature from database fields with full validation.

    Raises:
        ValueError: If any field is invalid or corrupted.
    """
    try:
        # Validate status is valid enum value before construction
        if "status" in kwargs:
            status_str = kwargs["status"]
            try:
                kwargs["status"] = SignatureStatus(status_str)
            except ValueError as e:
                raise ValueError(f"Invalid status '{status_str}'") from e

        # Construct instance - __post_init__ will validate invariants
        return cls(**kwargs)
    except (TypeError, ValueError) as e:
        raise ValueError(f"Failed to construct Signature from database: {e}") from e
```

This centralizes deserialization logic and makes the validation path explicit.

---

## Type: ErrorEvent

### Invariants Identified
- All fields are required and represent a single error occurrence
- `stack_frames` is immutable (tuple)
- `attributes` is converted to MappingProxyType (read-only dict) in __post_init__
- `severity` must be a valid Severity enum value
- `timestamp` should be a valid datetime from telemetry source

### Ratings
- **Encapsulation**: 9/10
  Frozen dataclass prevents all mutation. MappingProxyType ensures attributes dict is read-only. Excellent encapsulation.

- **Invariant Expression**: 8/10
  Tuple for stack_frames and MappingProxyType for attributes express immutability clearly. Severity enum expresses valid severity levels. The type signature makes the shape obvious.

- **Invariant Usefulness**: 8/10
  Prevents accidental mutation of error data. Severity enum prevents arbitrary severity strings. Stack frames and attributes are normalized on creation.

- **Invariant Enforcement**: 8/10
  **Strengths**: __post_init__ converts dict to MappingProxyType, preventing mutations. Frozen dataclass prevents field reassignment.

  **Weakness**: No validation that fields are non-empty. Telemetry adapters could create ErrorEvent with empty error_message or error_type.

### Strengths
- MappingProxyType ensures attributes are read-only
- Tuple for stack_frames prevents modification
- Frozen dataclass prevents any mutation
- Severity enum prevents arbitrary severity strings
- Clear representation of a single error occurrence

### Concerns
- **No validation of required fields**: Type allows empty `error_message` or `error_type`. These would cause issues in fingerprinting but aren't validated at construction.
- **No validation of string lengths**: error_message could be gigabytes of text.

### Recommended Improvements

1. **Add validation for non-empty required fields**:
```python
@dataclass(frozen=True)
class ErrorEvent:
    """A single error occurrence from telemetry."""

    # ... existing fields ...

    def __post_init__(self) -> None:
        """Convert attributes dict to read-only proxy and validate."""
        # Validate required fields are non-empty
        if not self.error_type or not self.error_type.strip():
            raise ValueError("error_type cannot be empty")
        if not self.service or not self.service.strip():
            raise ValueError("service cannot be empty")
        if not self.error_message or not self.error_message.strip():
            raise ValueError("error_message cannot be empty")

        # Convert attributes to read-only proxy
        if isinstance(self.attributes, dict):
            object.__setattr__(
                self, "attributes", MappingProxyType(self.attributes)
            )
```

---

## Type: Port Interfaces

### TelemetryPort

**Invariants Identified**
- `get_recent_errors()` should return ErrorEvent objects in descending timestamp order (documented but not enforced)
- `get_traces()` may return fewer results than trace_ids requested (documented: "omitted from results")
- All methods are async and raise generic Exception on failure

**Ratings**
- **Encapsulation**: 8/10
  Abstract base class clearly defines the boundary. Implementations must implement all methods. Type signature is clear.

- **Invariant Expression**: 7/10
  Return types are clear (list[ErrorEvent], TraceTree, etc.). However:
  - Ordering invariant (descending timestamp) is documented but not expressed in type
  - Partial result behavior (missing traces) is documented but not expressed in type
  - Exception types are generic (Exception, not specific exceptions)

- **Invariant Usefulness**: 7/10
  Returning list[ErrorEvent] allows callers to iterate predictably. Services depend on this contract. Prevents adapters from returning other types.

- **Invariant Enforcement**: 6/10
  Type system enforces return types, but:
  - Ordering is not checked. An adapter could return errors in random order (bug in caller)
  - Partial results are not tracked. Caller must check `len(results) < len(requests)`
  - Exception handling is loose (generic Exception)

**Strengths**
- Abstract base class clearly defines what adapters must implement
- Type annotations are complete
- Docstrings document expected behavior
- Async/await is consistent across all methods

**Concerns**
- **Ordering not enforced**: Docstring says "descending timestamp order" but return type is just `list[ErrorEvent]`. An adapter could return any order and code wouldn't catch it at type-check time.
- **Partial results not tracked**: get_traces() silently omits missing traces. Caller must check result length vs request length (done in investigator.py line 64-68, but not expressed in type).
- **Generic Exception too broad**: Methods raise `Exception`. Should use specific exceptions (e.g., `TelemetryUnavailableError`, `TraceNotFoundError`) for better error handling.

**Recommended Improvements**

1. **Use specific exception types**:
```python
class TelemetryException(Exception):
    """Base exception for telemetry operations."""
    pass

class TelemetryUnavailableError(TelemetryException):
    """Telemetry backend is unreachable or returning errors."""
    pass

class TraceNotFoundError(TelemetryException):
    """Requested trace was not found in the backend."""
    pass

class TelemetryPort(ABC):
    @abstractmethod
    async def get_recent_errors(
        self, since: datetime, services: list[str] | None = None
    ) -> list[ErrorEvent]:
        """Return error events since the given timestamp.

        Raises:
            TelemetryUnavailableError: If backend is unreachable.
        """
```

2. **Use TypeAlias for ordered collections** (optional but clarifying):
```python
# At module level
ErrorEventList: TypeAlias = list[ErrorEvent]
# Docstring note: Returned in descending timestamp order
```

Then in method signature, add a note in docstring:
```python
async def get_recent_errors(...) -> ErrorEventList:
    """Return error events since the given timestamp.

    Returns:
        List of ErrorEvent objects in descending timestamp order (most recent first).
    """
```

### DiagnosisPort

**Invariants Identified**
- `diagnose()` returns a Diagnosis object with valid Confidence level
- `estimate_cost()` returns non-negative float representing USD cost
- Both methods may raise exceptions on failure

**Ratings**
- **Encapsulation**: 9/10
  Clean abstraction. Implementations can vary widely (Claude Code, OpenAI, etc.).

- **Invariant Expression**: 7/10
  Return types are clear. However:
  - estimate_cost() should return non-negative value (not expressed in type)
  - diagnose() should return valid Diagnosis (type-checked, but Diagnosis may have invalid values if validation is weak)

- **Invariant Usefulness**: 8/10
  Type signature prevents returning wrong types. Confidence enum ensures diagnosis validity.

- **Invariant Enforcement**: 7/10
  Type system ensures return types are correct. But:
  - estimate_cost() could return -5.0 without type error (no bounds in type)
  - diagnose() depends on Diagnosis validation (which we identified as weak)
  - Claude Code adapter line 266 must handle ValueError when parsing confidence

**Strengths**
- Clean, minimal interface
- Async/await is consistent
- Confidence enum prevents invalid values in Diagnosis
- Docstrings document expected behavior

**Concerns**
- **Cost bounds not expressed in type**: estimate_cost() return type is just `float`. Could be -5.0 or 1000000.0. Budget enforcement relies on caller.
- **Exception handling is loose**: Generic Exception. Should distinguish between timeout, budget exceeded, model error, etc.

**Recommended Improvements**

```python
class DiagnosisException(Exception):
    """Base exception for diagnosis operations."""
    pass

class DiagnosisBudgetExceededError(DiagnosisException):
    """Diagnosis cost exceeds configured budget."""
    pass

class DiagnosisTimeoutError(DiagnosisException):
    """LLM request timed out."""
    pass

class DiagnosisModelError(DiagnosisException):
    """LLM returned invalid or unparseable response."""
    pass

class DiagnosisPort(ABC):
    @abstractmethod
    async def estimate_cost(self, context: InvestigationContext) -> float:
        """Estimate the cost (in USD) of diagnosing a signature.

        Returns:
            Estimated cost in USD (non-negative float).

        Raises:
            DiagnosisException: If cost estimation fails.
        """
```

### SignatureStorePort

**Invariants Identified**
- `get_by_id()` and `get_by_fingerprint()` return Signature or None
- `save()` and `update()` accept Signature (may validate before storing)
- All methods are async
- Results are ordered by last_seen (documented but not enforced)

**Ratings**
- **Encapsulation**: 9/10
  Clean abstraction. Adapter implementations vary (SQLite, PostgreSQL, etc.).

- **Invariant Expression**: 8/10
  Method signatures are clear. Return types and parameters are explicit.

- **Invariant Usefulness**: 9/10
  Signature type prevents storing wrong data types. Prevents typos in method names.

- **Invariant Enforcement**: 7/10
  Type system ensures signature objects are valid at storage time. However:
  - Deserialization can produce invalid Signature objects (caught by SQLite adapter)
  - Ordering (by last_seen) is documented but not enforced

**Strengths**
- Clear, comprehensive interface
- Async/await consistent
- Return types are precise
- Adapters validate on deserialization

**Concerns**
- **Ordering not enforced in type**: get_pending_investigation() and get_all() should return sorted by priority/recency, but return type is just `list[Signature]`. Caller must not assume ordering.
- **Exception handling is loose**: Generic Exception. Could be more specific.

**Recommended Improvements**

```python
# Use TypeAlias for collections with semantic meaning
SignatureList: TypeAlias = list[Signature]

class SignatureStorePort(ABC):
    @abstractmethod
    async def get_pending_investigation(self) -> SignatureList:
        """Return signatures with status NEW, ordered by priority.

        Ordering: By last_seen DESC, then occurrence_count DESC.

        Returns:
            List of Signature objects with NEW status.
        """
```

---

## Cross-Type Invariant Patterns

### Enum Parsing Pattern (Vulnerability)

The project has a repeated pattern of parsing enums from external data:

**SQLite adapter, line 380:**
```python
status=SignatureStatus(status),
```

**Claude Code adapter, line 266:**
```python
confidence = Confidence(confidence_str.lower())
```

**Both protect with try/except**, but the pattern is scattered. This is a sign that a centralized factory or parser is needed.

**Recommended Pattern:**
```python
# In models.py or a new parsing module
class ModelParsers:
    @staticmethod
    def parse_confidence(value: str) -> Confidence:
        """Parse confidence from external data."""
        try:
            return Confidence(value.lower())
        except ValueError as e:
            raise ValueError(
                f"Invalid confidence '{value}'. "
                f"Valid values: {[c.value for c in Confidence]}"
            ) from e

    @staticmethod
    def parse_status(value: str) -> SignatureStatus:
        """Parse status from external data."""
        try:
            return SignatureStatus(value)
        except ValueError as e:
            raise ValueError(
                f"Invalid status '{value}'. "
                f"Valid values: {[s.value for s in SignatureStatus]}"
            ) from e
```

Then adapters use:
```python
# SQLite
status=ModelParsers.parse_status(status),

# Claude Code
confidence=ModelParsers.parse_confidence(confidence_str),
```

This centralizes the logic and makes the expectation explicit.

---

## Deserialization Vulnerability Summary

The project correctly handles deserialization errors, but the pattern is reactive (try/except) rather than proactive (validation). Here are the vulnerable points:

| Type | Adapter | Line | Pattern | Risk |
|------|---------|------|---------|------|
| Confidence | Claude Code | 266 | `Confidence(str.lower())` + try/except | ValueError on invalid input |
| Confidence | SQLite | 415 | `Confidence(value)` + try/except | ValueError on corrupted data |
| SignatureStatus | SQLite | 380 | `SignatureStatus(value)` directly | ValueError on corrupted data |
| Diagnosis | SQLite | 411 | `json.loads()` + field access | KeyError on malformed JSON |

**All of these are caught and handled**, but the error handling is in the adapter layer. A better approach would be to validate at the model layer (in __post_init__) or have factory methods that validate before construction.

---

## Summary of Gaps

### Critical (Should Fix)
1. **Diagnosis has no __post_init__ validation** - cost_usd could be negative, evidence could be empty
2. **Enum parsing is scattered** - Confidence and SignatureStatus parsing is repeated in adapters with no centralized validation
3. **State transitions are not validated** - Signature status can be mutated to invalid states

### High Priority (Should Address)
4. **Signature allows direct field mutation** - No setter methods to validate transitions before mutation
5. **Port interfaces use generic Exception** - Should use specific exception types for better error handling
6. **Ordering invariants are implicit** - Documented but not expressed in types

### Medium Priority (Nice to Have)
7. **ErrorEvent has no field validation** - Allows empty error_type, error_message
8. **SignatureStatus transition rules are distributed** - Duplicated in Triage, Investigator, ManagementService

### Low Priority (Refinement)
9. **Type aliases could clarify collections** - Ordered lists could use TypeAlias with documentation
10. **InvestigationContext has no validation** - Accepts empty tuples for events, logs, traces

---

## Recommendations Priority Order

### Phase 1 (Fix Critical Issues)
1. Add `Diagnosis.__post_init__()` validation for cost and evidence
2. Create `ModelParsers` class with parse_confidence() and parse_status()
3. Add `Signature.set_status()` and `Signature.record_occurrence()` helper methods

### Phase 2 (High Priority)
4. Create `SignatureStatusTransition` class to centralize state transition rules
5. Update DiagnosisPort and TelemetryPort to use specific exception types
6. Add validation to ErrorEvent.__post_init__()

### Phase 3 (Medium Priority)
7. Update SignatureStorePort to use TypeAlias for ordered collections
8. Consolidate status transition validation in one place
9. Add Signature.from_database() factory method

### Phase 4 (Nice to Have)
10. Consider making Signature frozen and using update_status() factory methods
11. Add type guards for sensitive transitions (e.g., can_diagnose() helper)

---

## Conclusion

The Rounds project has **excellent foundational type design**:
- Frozen dataclasses for immutable domain entities ✓
- Enum for constrained values (Confidence, SignatureStatus, Severity) ✓
- Port abstraction with clear interfaces ✓
- Comprehensive type annotations ✓

However, there are **specific runtime gaps**:
- Weak invariant enforcement in Diagnosis and Signature
- Scattered enum parsing logic in adapters
- Implicit state transition rules in services
- Generic exception types that don't clarify error handling

**Addressing these gaps through the recommended improvements would raise the design from 7.5/10 to 8.5/10** and provide strong defense against subtle runtime errors that could occur during deserialization or state mutation.

The good news: **All of these improvements are localized and don't require architectural changes**. They're primarily about adding validation methods and centralizing parsing logic.
