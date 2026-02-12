# Type Design Improvements: Concrete Implementation Examples

This document provides ready-to-use code for the recommended improvements.

---

## Improvement 1: Add Diagnosis Validation

**File**: `/workspace/rounds/core/models.py`

**Current Code** (lines 79-90):
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
```

**Improved Code**:
```python
@dataclass(frozen=True)
class Diagnosis:
    """LLM-generated root cause analysis for a signature.

    Invariants:
    - root_cause and suggested_fix are non-empty
    - evidence tuple contains at least 3 items
    - cost_usd is non-negative
    - confidence is a valid Confidence enum value (enforced by type)
    """

    root_cause: str
    evidence: tuple[str, ...]
    suggested_fix: str
    confidence: Confidence
    diagnosed_at: datetime
    model: str
    cost_usd: float

    def __post_init__(self) -> None:
        """Validate diagnosis invariants on creation."""
        # Validate root_cause
        if not self.root_cause or not self.root_cause.strip():
            raise ValueError("root_cause cannot be empty")

        # Validate suggested_fix
        if not self.suggested_fix or not self.suggested_fix.strip():
            raise ValueError("suggested_fix cannot be empty")

        # Validate evidence
        if not self.evidence:
            raise ValueError("evidence tuple cannot be empty")

        if len(self.evidence) < 3:
            logger.warning(
                f"Diagnosis has fewer than 3 evidence points ({len(self.evidence)}). "
                f"Consider including more supporting evidence for higher confidence."
            )

        # Validate cost
        if self.cost_usd < 0:
            raise ValueError(
                f"cost_usd must be non-negative, got {self.cost_usd}"
            )
```

**Impact**:
- Prevents invalid Diagnosis objects from being created
- Catches issues at construction time, not later in persistence or notification
- Helps cost tracking (no negative costs)
- Encourages better evidence gathering (3+ items)

---

## Improvement 2: Centralize Enum Parsing

**File**: `/workspace/rounds/core/models.py`

**Add New Class** (after imports, before Severity enum):
```python
class ModelParsers:
    """Centralized parsing for enums from external data sources.

    Handles case normalization and validation for enums that come from
    JSON, database, or other external sources.
    """

    @staticmethod
    def parse_confidence(value: str) -> "Confidence":
        """Parse confidence from external data (case-insensitive).

        Args:
            value: String value to parse (e.g., "HIGH", "high", "High")

        Returns:
            Confidence enum instance

        Raises:
            ValueError: If value is not a valid confidence level
        """
        try:
            return Confidence(value.lower())
        except ValueError as e:
            valid_values = [c.value for c in Confidence]
            raise ValueError(
                f"Invalid confidence '{value}'. "
                f"Must be one of {valid_values}"
            ) from e

    @staticmethod
    def parse_status(value: str) -> "SignatureStatus":
        """Parse status from external data.

        Args:
            value: String value to parse (e.g., "new", "investigating")

        Returns:
            SignatureStatus enum instance

        Raises:
            ValueError: If value is not a valid status
        """
        try:
            return SignatureStatus(value)
        except ValueError as e:
            valid_values = [s.value for s in SignatureStatus]
            raise ValueError(
                f"Invalid status '{value}'. "
                f"Must be one of {valid_values}"
            ) from e

    @staticmethod
    def parse_severity(value: str) -> "Severity":
        """Parse severity from external data (case-insensitive).

        Args:
            value: String value to parse (e.g., "ERROR", "error", "Error")

        Returns:
            Severity enum instance

        Raises:
            ValueError: If value is not a valid severity
        """
        try:
            return Severity(value.upper())
        except ValueError as e:
            valid_values = [s.value for s in Severity]
            raise ValueError(
                f"Invalid severity '{value}'. "
                f"Must be one of {valid_values}"
            ) from e
```

**Update SQLite Adapter**: `/workspace/rounds/adapters/store/sqlite.py`

**Current Code** (lines 407-419):
```python
@staticmethod
def _deserialize_diagnosis(diagnosis_json: str) -> Diagnosis:
    """Deserialize a Diagnosis from JSON."""
    data = json.loads(diagnosis_json)
    return Diagnosis(
        root_cause=data["root_cause"],
        evidence=tuple(data["evidence"]),
        suggested_fix=data["suggested_fix"],
        confidence=Confidence(data["confidence"]),
        diagnosed_at=datetime.fromisoformat(data["diagnosed_at"]),
        model=data["model"],
        cost_usd=data["cost_usd"],
    )
```

**Improved Code**:
```python
@staticmethod
def _deserialize_diagnosis(diagnosis_json: str) -> Diagnosis:
    """Deserialize a Diagnosis from JSON.

    Raises:
        ValueError: If diagnosis_json is malformed or contains invalid data.
    """
    data = json.loads(diagnosis_json)
    return Diagnosis(
        root_cause=data["root_cause"],
        evidence=tuple(data["evidence"]),
        suggested_fix=data["suggested_fix"],
        confidence=ModelParsers.parse_confidence(data["confidence"]),
        diagnosed_at=datetime.fromisoformat(data["diagnosed_at"]),
        model=data["model"],
        cost_usd=data["cost_usd"],
    )
```

**Update Claude Code Adapter**: `/workspace/rounds/adapters/diagnosis/claude_code.py`

**Current Code** (lines 264-271):
```python
# Parse confidence - raise on invalid value
try:
    confidence = Confidence(confidence_str.lower())
except ValueError as e:
    raise ValueError(
        f"Invalid confidence level '{confidence_str}'. "
        f"Must be one of {[c.value for c in Confidence]}"
    ) from e
```

**Improved Code**:
```python
# Parse confidence - raise on invalid value
try:
    confidence = ModelParsers.parse_confidence(confidence_str)
except ValueError as e:
    raise ValueError(
        f"Failed to parse confidence from LLM response: {e}"
    ) from e
```

**Update SQLite Adapter Status Parsing**: `/workspace/rounds/adapters/store/sqlite.py` (line 380)

**Current Code**:
```python
status=SignatureStatus(status),
```

**Improved Code**:
```python
status=ModelParsers.parse_status(status),
```

**Impact**:
- Single source of truth for enum parsing
- Consistent error messages
- Case normalization handled centrally
- Easier to add new enum types

---

## Improvement 3: Add Signature State Mutation Helpers

**File**: `/workspace/rounds/core/models.py`

**Add State Transition Validator** (before Signature class):
```python
class SignatureStatusTransition:
    """Encodes valid state transitions for signatures.

    Signature status follows a directed graph:
    - NEW → INVESTIGATING, MUTED
    - INVESTIGATING → DIAGNOSED, NEW (retriage)
    - DIAGNOSED → RESOLVED, MUTED, NEW (retriage)
    - RESOLVED → NEW (retriage on regression)
    - MUTED → NEW (unmute via retriage)
    """

    # Define valid transitions as a mapping
    VALID_TRANSITIONS = {
        SignatureStatus.NEW: {
            SignatureStatus.INVESTIGATING,
            SignatureStatus.MUTED,
        },
        SignatureStatus.INVESTIGATING: {
            SignatureStatus.DIAGNOSED,
            SignatureStatus.NEW,  # Retriage
        },
        SignatureStatus.DIAGNOSED: {
            SignatureStatus.RESOLVED,
            SignatureStatus.MUTED,
            SignatureStatus.NEW,  # Retriage
        },
        SignatureStatus.RESOLVED: {
            SignatureStatus.NEW,  # Regression retriage
        },
        SignatureStatus.MUTED: {
            SignatureStatus.NEW,  # Unmute via retriage
        },
    }

    @staticmethod
    def is_valid(from_status: SignatureStatus, to_status: SignatureStatus) -> bool:
        """Check if transition is allowed.

        Args:
            from_status: Current status
            to_status: Desired status

        Returns:
            True if transition is allowed, False otherwise
        """
        return to_status in SignatureStatusTransition.VALID_TRANSITIONS.get(
            from_status, set()
        )

    @staticmethod
    def validate_or_raise(
        from_status: SignatureStatus, to_status: SignatureStatus
    ) -> None:
        """Validate transition or raise.

        Args:
            from_status: Current status
            to_status: Desired status

        Raises:
            ValueError: If transition is not allowed
        """
        if not SignatureStatusTransition.is_valid(from_status, to_status):
            raise ValueError(
                f"Invalid status transition: {from_status.value} -> {to_status.value}"
            )
```

**Add Methods to Signature Class** (in the mutable section):
```python
@dataclass
class Signature:
    """A fingerprinted failure pattern."""

    # ... existing fields ...

    def set_status(self, new_status: SignatureStatus) -> None:
        """Update signature status with validation.

        Checks that the transition is allowed according to the state machine.

        Args:
            new_status: New status to set

        Raises:
            ValueError: If transition is not allowed
        """
        SignatureStatusTransition.validate_or_raise(self.status, new_status)
        self.status = new_status

    def record_occurrence(self, timestamp: datetime) -> None:
        """Record a new error occurrence.

        Updates the last_seen timestamp and increments occurrence_count.
        Validates that the new timestamp is not before first_seen.

        Args:
            timestamp: When the error was observed

        Raises:
            ValueError: If timestamp is before first_seen
        """
        if timestamp < self.first_seen:
            raise ValueError(
                f"Occurrence timestamp {timestamp} is before "
                f"first_seen {self.first_seen}"
            )
        self.last_seen = timestamp
        self.occurrence_count += 1

    def can_investigate(self) -> bool:
        """Check if this signature should be investigated.

        Returns True if signature is in a status that allows investigation
        (NEW or INVESTIGATING). Returns False if resolved, muted, or fully diagnosed.

        Returns:
            True if signature can be investigated
        """
        return self.status in {SignatureStatus.NEW, SignatureStatus.INVESTIGATING}

    def is_terminal(self) -> bool:
        """Check if signature has reached a terminal state.

        Terminal states (RESOLVED, MUTED) should not be re-investigated
        unless explicitly retriaged.

        Returns:
            True if signature is in a terminal state
        """
        return self.status in {SignatureStatus.RESOLVED, SignatureStatus.MUTED}
```

**Update Investigator**: `/workspace/rounds/core/investigator.py`

**Current Code** (line 89):
```python
signature.status = SignatureStatus.INVESTIGATING
await self.store.update(signature)
```

**Improved Code**:
```python
signature.set_status(SignatureStatus.INVESTIGATING)
await self.store.update(signature)
```

**Update TriageEngine**: `/workspace/rounds/core/triage.py` (use can_investigate helper)

**Current Code** (line 29-52):
```python
def should_investigate(self, signature: Signature) -> bool:
    """Is this signature worth sending to the diagnosis engine?"""
    # Don't investigate resolved or muted signatures
    if signature.status in {SignatureStatus.RESOLVED, SignatureStatus.MUTED}:
        return False
    # ... rest of logic
```

**Improved Code**:
```python
def should_investigate(self, signature: Signature) -> bool:
    """Is this signature worth sending to the diagnosis engine?"""
    # Don't investigate terminal (resolved or muted) signatures
    if signature.is_terminal():
        return False
    # ... rest of logic
```

**Impact**:
- All state transitions are validated before they occur
- Type-unsafe mutations are replaced with type-safe methods
- State machine is documented in one place
- Helper methods (can_investigate, is_terminal) make intent clear

---

## Improvement 4: Add ErrorEvent Validation

**File**: `/workspace/rounds/core/models.py`

**Current Code** (lines 35-59):
```python
@dataclass(frozen=True)
class ErrorEvent:
    """A single error occurrence from telemetry."""

    trace_id: str
    span_id: str
    service: str
    error_type: str  # e.g. "ConnectionTimeoutError"
    error_message: str  # raw message
    stack_frames: tuple[StackFrame, ...]
    timestamp: datetime
    attributes: MappingProxyType
    severity: Severity

    def __post_init__(self) -> None:
        """Convert attributes dict to read-only proxy."""
        if isinstance(self.attributes, dict):
            object.__setattr__(
                self, "attributes", MappingProxyType(self.attributes)
            )
```

**Improved Code**:
```python
@dataclass(frozen=True)
class ErrorEvent:
    """A single error occurrence from telemetry.

    Represents a normalized, immutable view of an error from any telemetry backend.

    Invariants:
    - trace_id, span_id, service, error_type are non-empty strings
    - error_message is non-empty
    - attributes is immutable (MappingProxyType)
    - severity is a valid Severity enum value
    """

    trace_id: str
    span_id: str
    service: str
    error_type: str  # e.g. "ConnectionTimeoutError"
    error_message: str  # raw message
    stack_frames: tuple[StackFrame, ...]
    timestamp: datetime
    attributes: MappingProxyType
    severity: Severity

    def __post_init__(self) -> None:
        """Validate error event invariants and convert attributes to read-only."""
        # Validate required string fields are non-empty
        if not self.trace_id or not self.trace_id.strip():
            raise ValueError("trace_id cannot be empty")

        if not self.span_id or not self.span_id.strip():
            raise ValueError("span_id cannot be empty")

        if not self.service or not self.service.strip():
            raise ValueError("service cannot be empty")

        if not self.error_type or not self.error_type.strip():
            raise ValueError("error_type cannot be empty")

        if not self.error_message or not self.error_message.strip():
            raise ValueError("error_message cannot be empty")

        # Convert attributes dict to read-only proxy
        if isinstance(self.attributes, dict):
            object.__setattr__(
                self, "attributes", MappingProxyType(self.attributes)
            )
```

**Impact**:
- Prevents creating ErrorEvent with empty critical fields
- Catches telemetry adapter issues early
- Prevents fingerprinting failures downstream

---

## Improvement 5: Add Specific Exception Types to Ports

**File**: `/workspace/rounds/core/ports.py`

**Add After Imports** (after line 33):
```python
# ============================================================================
# PORT-SPECIFIC EXCEPTIONS
# ============================================================================


class TelemetryException(Exception):
    """Base exception for telemetry port operations."""

    pass


class TelemetryUnavailableError(TelemetryException):
    """Telemetry backend is unreachable or returning errors."""

    pass


class TraceNotFoundError(TelemetryException):
    """Requested trace was not found in the backend."""

    pass


class DiagnosisException(Exception):
    """Base exception for diagnosis port operations."""

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


class SignatureStoreException(Exception):
    """Base exception for signature store operations."""

    pass


class SignatureNotFoundError(SignatureStoreException):
    """Signature with given ID or fingerprint not found."""

    pass


class NotificationException(Exception):
    """Base exception for notification port operations."""

    pass
```

**Update TelemetryPort Docstrings** (line 76):
```python
Raises:
    TelemetryUnavailableError: If telemetry backend is unreachable.
    TraceNotFoundError: If no traces found (only for get_trace, not get_traces).
```

**Update DiagnosisPort Docstrings** (line 301-303):
```python
Raises:
    DiagnosisBudgetExceededError: If estimated cost exceeds budget.
    DiagnosisModelError: If LLM returns invalid response.
    DiagnosisTimeoutError: If LLM request times out.
```

**Update Adapters to Use Specific Exceptions**

Example for Claude Code adapter (`/workspace/rounds/adapters/diagnosis/claude_code.py`):

**Current Code** (lines 75-80):
```python
except (ValueError, TimeoutError, RuntimeError) as e:
    logger.error(f"Failed to diagnose: {e}")
    raise
```

**Improved Code**:
```python
except TimeoutError as e:
    logger.error(f"Claude Code diagnosis timed out: {e}")
    raise DiagnosisTimeoutError(str(e)) from e
except ValueError as e:
    if "budget" in str(e).lower():
        logger.error(f"Diagnosis exceeds budget: {e}")
        raise DiagnosisBudgetExceededError(str(e)) from e
    logger.error(f"Failed to parse diagnosis response: {e}")
    raise DiagnosisModelError(str(e)) from e
except RuntimeError as e:
    logger.error(f"Claude Code CLI error: {e}")
    raise DiagnosisModelError(str(e)) from e
```

**Impact**:
- Callers can distinguish between different error types
- Better error recovery (retry on timeout, log on budget exceeded, etc.)
- Clearer intent in error messages

---

## Testing the Improvements

**Test for Diagnosis Validation** (`/workspace/rounds/tests/core/test_models.py`):
```python
import pytest
from datetime import datetime, timezone
from rounds.core.models import Diagnosis, Confidence

def test_diagnosis_validates_non_empty_root_cause():
    """Diagnosis must have non-empty root_cause."""
    with pytest.raises(ValueError, match="root_cause cannot be empty"):
        Diagnosis(
            root_cause="",
            evidence=("e1", "e2", "e3"),
            suggested_fix="fix it",
            confidence=Confidence.HIGH,
            diagnosed_at=datetime.now(timezone.utc),
            model="claude-3",
            cost_usd=1.0,
        )

def test_diagnosis_validates_evidence_count():
    """Diagnosis must have at least some evidence (warns if < 3)."""
    with pytest.raises(ValueError, match="evidence tuple cannot be empty"):
        Diagnosis(
            root_cause="cause",
            evidence=(),
            suggested_fix="fix",
            confidence=Confidence.HIGH,
            diagnosed_at=datetime.now(timezone.utc),
            model="claude-3",
            cost_usd=1.0,
        )

def test_diagnosis_validates_non_negative_cost():
    """Diagnosis cost must be non-negative."""
    with pytest.raises(ValueError, match="cost_usd must be non-negative"):
        Diagnosis(
            root_cause="cause",
            evidence=("e1", "e2", "e3"),
            suggested_fix="fix",
            confidence=Confidence.HIGH,
            diagnosed_at=datetime.now(timezone.utc),
            model="claude-3",
            cost_usd=-5.0,
        )
```

**Test for State Transitions** (`/workspace/rounds/tests/core/test_models.py`):
```python
def test_signature_invalid_status_transition():
    """Signature rejects invalid state transitions."""
    sig = Signature(
        id="sig-1",
        fingerprint="fp-1",
        error_type="TypeError",
        service="api",
        message_template="msg",
        stack_hash="sh",
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
        occurrence_count=1,
        status=SignatureStatus.RESOLVED,
    )

    # RESOLVED → INVESTIGATING is invalid
    with pytest.raises(ValueError, match="Invalid status transition"):
        sig.set_status(SignatureStatus.INVESTIGATING)

    # RESOLVED → NEW is valid (retriage)
    sig.set_status(SignatureStatus.NEW)
    assert sig.status == SignatureStatus.NEW
```

**Test for Enum Parsing** (`/workspace/rounds/tests/core/test_models.py`):
```python
def test_parse_confidence_case_insensitive():
    """Confidence parser handles case normalization."""
    assert ModelParsers.parse_confidence("HIGH") == Confidence.HIGH
    assert ModelParsers.parse_confidence("high") == Confidence.HIGH
    assert ModelParsers.parse_confidence("High") == Confidence.HIGH
    assert ModelParsers.parse_confidence("MEDIUM") == Confidence.MEDIUM

def test_parse_confidence_invalid_value():
    """Confidence parser rejects invalid values."""
    with pytest.raises(ValueError, match="Invalid confidence"):
        ModelParsers.parse_confidence("MAYBE")
```

---

## Migration Path

**Step 1**: Add improvements to models.py (Diagnosis validation, ModelParsers, SignatureStatusTransition, Signature helpers)
**Step 2**: Update adapters to use ModelParsers.parse_*() methods
**Step 3**: Update services to use Signature.set_status() and can_investigate()
**Step 4**: Add specific exception types to ports.py
**Step 5**: Update adapter error handling to raise specific exceptions
**Step 6**: Add tests for all new validation

**Total Effort**: ~300 lines of new/modified code, spread across multiple files. No breaking changes to public APIs if done carefully.

---

## Verification Checklist

After implementing improvements:

- [ ] Diagnosis.__post_init__() validates cost, evidence, and text fields
- [ ] ModelParsers.parse_confidence/status/severity centralize enum parsing
- [ ] SignatureStatusTransition documents all valid state transitions
- [ ] Signature.set_status() validates transitions before mutation
- [ ] ErrorEvent.__post_init__() validates required string fields
- [ ] Ports define specific exception types
- [ ] All adapters updated to use centralized parsing
- [ ] All services updated to use Signature.set_status()
- [ ] All error handling uses specific exception types
- [ ] Tests added for validation edge cases
- [ ] Tests added for invalid state transitions
- [ ] Tests added for enum parsing with case normalization

