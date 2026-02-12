# Type Design Fixes: Implementation Guide

This document provides ready-to-use code fixes for all critical and high-priority type design issues identified in the PR review.

---

## CRITICAL: Fix Signature Mutability

### Current Problem
File: `/workspace/rounds/core/models.py:92-127`
```python
@dataclass
class Signature:
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
```

### Solution 1: Add Validation Methods (RECOMMENDED - Backward Compatible)

Replace the Signature class with:

```python
@dataclass
class Signature:
    """A fingerprinted failure pattern.

    Represents a class of errors, not a single occurrence.
    Tracks lifecycle, occurrence count, and optional diagnosis.

    Note: This dataclass is mutable to allow state transitions.
    State changes should be made through validated methods to prevent
    invariant violations.
    """

    id: str  # UUID
    fingerprint: str  # hex digest of normalized error
    error_type: str
    service: str
    message_template: str  # parameterized message
    stack_hash: str  # hash of normalized stack structure
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    status: SignatureStatus
    diagnosis: Diagnosis | None = None
    tags: frozenset[str] = field(default_factory=frozenset)  # immutable set

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

    def set_status(self, new_status: SignatureStatus) -> None:
        """Transition to a new status with validation.

        Args:
            new_status: The target status to transition to.

        Raises:
            ValueError: If the transition is invalid.
        """
        # Define valid transitions
        valid_next = {
            SignatureStatus.NEW: {
                SignatureStatus.INVESTIGATING,
                SignatureStatus.MUTED,
                SignatureStatus.RESOLVED,
            },
            SignatureStatus.INVESTIGATING: {
                SignatureStatus.DIAGNOSED,
                SignatureStatus.NEW,  # Retry if diagnosis fails
            },
            SignatureStatus.DIAGNOSED: {
                SignatureStatus.MUTED,
                SignatureStatus.RESOLVED,
                SignatureStatus.NEW,  # Retriage
            },
            SignatureStatus.MUTED: {
                SignatureStatus.RESOLVED,
                SignatureStatus.NEW,  # Retriage
            },
            SignatureStatus.RESOLVED: {
                SignatureStatus.NEW,  # Only retriage allowed from resolved
            },
        }

        allowed = valid_next.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Cannot transition from {self.status} to {new_status}. "
                f"Valid transitions: {allowed}"
            )

        self.status = new_status

    def set_diagnosis(self, diagnosis: Diagnosis) -> None:
        """Set diagnosis with validation.

        Can only set diagnosis when status is INVESTIGATING or DIAGNOSED.

        Args:
            diagnosis: The diagnosis to record.

        Raises:
            ValueError: If diagnosis cannot be set on current status.
        """
        if self.status not in {SignatureStatus.INVESTIGATING, SignatureStatus.DIAGNOSED}:
            raise ValueError(
                f"Cannot set diagnosis on {self.status} signature. "
                f"Status must be INVESTIGATING or DIAGNOSED."
            )
        self.diagnosis = diagnosis

    def update_occurrence(self, timestamp: datetime) -> None:
        """Update occurrence count and last_seen timestamp.

        Increments occurrence_count and updates last_seen with validation
        that new timestamp is not before the current last_seen.

        Args:
            timestamp: The timestamp of the new occurrence.

        Raises:
            ValueError: If timestamp is before last_seen.
        """
        if timestamp < self.last_seen:
            raise ValueError(
                f"timestamp ({timestamp}) cannot be before last_seen ({self.last_seen})"
            )
        self.occurrence_count += 1
        self.last_seen = timestamp
```

### Update Callers

File: `/workspace/rounds/core/management_service.py`

**Old code (lines 51-52)**:
```python
signature.status = SignatureStatus.MUTED
signature.last_seen = datetime.now(timezone.utc)
```

**New code**:
```python
signature.set_status(SignatureStatus.MUTED)
signature.last_seen = datetime.now(timezone.utc)
```

**Old code (lines 83-84)**:
```python
signature.status = SignatureStatus.RESOLVED
signature.last_seen = datetime.now(timezone.utc)
```

**New code**:
```python
signature.set_status(SignatureStatus.RESOLVED)
signature.last_seen = datetime.now(timezone.utc)
```

**Old code (lines 114-115)**:
```python
signature.status = SignatureStatus.NEW
signature.diagnosis = None
```

**New code**:
```python
signature.set_status(SignatureStatus.NEW)
signature.diagnosis = None
```

File: `/workspace/rounds/core/poll_service.py`

**Old code (lines 106-107)**:
```python
signature.last_seen = error.timestamp
signature.occurrence_count += 1
```

**New code**:
```python
signature.update_occurrence(error.timestamp)
```

File: `/workspace/rounds/core/investigator.py`

**Old code (lines 89, 115-116)**:
```python
signature.status = SignatureStatus.INVESTIGATING
await self.store.update(signature)
# ... later ...
signature.diagnosis = diagnosis
signature.status = SignatureStatus.DIAGNOSED
```

**New code**:
```python
signature.set_status(SignatureStatus.INVESTIGATING)
await self.store.update(signature)
# ... later ...
signature.set_diagnosis(diagnosis)
signature.set_status(SignatureStatus.DIAGNOSED)
```

---

## HIGH PRIORITY: Add Comprehensive Validation

### ErrorEvent - Add String Validation
File: `/workspace/rounds/core/models.py:36-58`

**Replace `__post_init__` with**:
```python
def __post_init__(self) -> None:
    """Convert attributes dict to read-only proxy and validate."""
    # Validate required non-empty strings
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

---

### Diagnosis - Add Cost and String Validation
File: `/workspace/rounds/core/models.py:79-90`

**Add `__post_init__` method**:
```python
def __post_init__(self) -> None:
    """Validate diagnosis invariants."""
    if not self.root_cause or not self.root_cause.strip():
        raise ValueError("root_cause cannot be empty")
    if not self.suggested_fix or not self.suggested_fix.strip():
        raise ValueError("suggested_fix cannot be empty")
    if not self.evidence:
        raise ValueError("evidence tuple cannot be empty")
    if any(not e or not e.strip() for e in self.evidence):
        raise ValueError("evidence items cannot be empty strings")
    if self.cost_usd < 0:
        raise ValueError(f"cost_usd must be non-negative, got {self.cost_usd}")
    if not self.model or not self.model.strip():
        raise ValueError("model cannot be empty")
```

---

### StackFrame - Add String Validation
File: `/workspace/rounds/core/models.py:14-21`

**Add `__post_init__` method**:
```python
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

### SpanNode - Add Duration and String Validation
File: `/workspace/rounds/core/models.py:129-149`

**Replace `__post_init__` with**:
```python
def __post_init__(self) -> None:
    """Convert attributes dict to read-only proxy and validate."""
    if not self.span_id or not self.span_id.strip():
        raise ValueError("span_id cannot be empty")
    if not self.service or not self.service.strip():
        raise ValueError("service cannot be empty")
    if not self.operation or not self.operation.strip():
        raise ValueError("operation cannot be empty")
    if self.duration_ms < 0:
        raise ValueError(f"duration_ms must be non-negative, got {self.duration_ms}")

    # Convert attributes dict to read-only proxy
    if isinstance(self.attributes, dict):
        object.__setattr__(
            self, "attributes", MappingProxyType(self.attributes)
        )
```

---

### TraceTree - Add Trace ID Validation
File: `/workspace/rounds/core/models.py:151-158`

**Add `__post_init__` method**:
```python
def __post_init__(self) -> None:
    """Validate trace tree invariants."""
    if not self.trace_id or not self.trace_id.strip():
        raise ValueError("trace_id cannot be empty")
```

---

### LogEntry - Add Body Validation
File: `/workspace/rounds/core/models.py:160-177`

**Replace `__post_init__` with**:
```python
def __post_init__(self) -> None:
    """Convert attributes dict to read-only proxy and validate."""
    if not self.body or not self.body.strip():
        raise ValueError("body cannot be empty")

    # Convert attributes dict to read-only proxy
    if isinstance(self.attributes, dict):
        object.__setattr__(
            self, "attributes", MappingProxyType(self.attributes)
        )
```

---

### InvestigationContext - Add Path Validation
File: `/workspace/rounds/core/models.py:179-192`

**Add `__post_init__` method**:
```python
def __post_init__(self) -> None:
    """Validate investigation context invariants."""
    if not self.codebase_path or not self.codebase_path.strip():
        raise ValueError("codebase_path cannot be empty")
```

---

### PollResult - Add Count Validation
File: `/workspace/rounds/core/models.py:194-203`

**Add `__post_init__` method**:
```python
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

## HIGH PRIORITY: Add Typed Return Types

### Define SignatureDetails Type
File: `/workspace/rounds/core/models.py` (add after Signature class)

```python
@dataclass(frozen=True)
class SignatureDetails:
    """Full details about a signature with related information.

    Returned by ManagementPort.get_signature_details() instead of
    untyped dict to provide type safety and documentation.
    """

    # Basic fields
    id: str
    fingerprint: str
    error_type: str
    service: str
    message_template: str
    stack_hash: str

    # Timestamps and counts
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int

    # State
    status: SignatureStatus
    tags: frozenset[str]

    # Diagnosis (if available)
    diagnosis: Diagnosis | None

    # Related information
    related_signatures: tuple["SignatureDetails", ...] = ()
```

### Define StoreStats Type
File: `/workspace/rounds/core/models.py` (add after PollResult class)

```python
@dataclass(frozen=True)
class StoreStats:
    """Summary statistics from the signature store.

    Returned by SignatureStorePort.get_stats() instead of untyped dict
    to provide type safety and documentation.
    """

    total_signatures: int
    new_count: int
    investigating_count: int
    diagnosed_count: int
    resolved_count: int
    muted_count: int
    average_occurrence_count: float
    oldest_signature_age_hours: float
    newest_signature_age_hours: float
```

---

### Update Port Signatures
File: `/workspace/rounds/core/ports.py`

**Old code (lines 244-252)**:
```python
@abstractmethod
async def get_stats(self) -> dict[str, Any]:
    """Summary statistics for reporting.

    Returns:
        Dictionary with statistics (keys are implementation-defined).

    Raises:
        Exception: If database is unavailable.
    """
```

**New code**:
```python
@abstractmethod
async def get_stats(self) -> "StoreStats":
    """Summary statistics for reporting.

    Returns:
        StoreStats object with detailed statistics.

    Raises:
        Exception: If database is unavailable.
    """
```

**Old code (lines 465-483)**:
```python
@abstractmethod
async def get_signature_details(self, signature_id: str) -> dict[str, Any]:
    """Retrieve detailed information about a signature.

    Returns all signature fields plus derived information:
    - signature fields (id, fingerprint, error_type, service, etc.)
    - occurrence_count and time window (first_seen to last_seen)
    - diagnosis (if available) with confidence
    - recent error events (for context)
    - related signatures (similar errors)

    Args:
        signature_id: UUID of the signature.

    Returns:
        Dictionary with all signature details.

    Raises:
        Exception: If signature doesn't exist or database error.
    """
```

**New code**:
```python
@abstractmethod
async def get_signature_details(
    self, signature_id: str
) -> "SignatureDetails":
    """Retrieve detailed information about a signature.

    Returns all signature fields plus related information:
    - signature fields (id, fingerprint, error_type, service, etc.)
    - occurrence_count and time window (first_seen to last_seen)
    - diagnosis (if available) with confidence
    - related signatures (similar errors)

    Args:
        signature_id: UUID of the signature.

    Returns:
        SignatureDetails object with all signature information.

    Raises:
        Exception: If signature doesn't exist or database error.
    """
```

---

### Update ManagementService Implementation
File: `/workspace/rounds/core/management_service.py:125-199`

**Old code**:
```python
async def get_signature_details(self, signature_id: str) -> dict[str, Any]:
    """Retrieve detailed information about a signature."""
    signature = await self.store.get_by_id(signature_id)
    if signature is None:
        raise ValueError(f"Signature {signature_id} not found")

    # Get related/similar signatures
    related = await self.store.get_similar(signature, limit=5)

    # Build details dictionary
    details: dict[str, Any] = {
        # Basic fields
        "id": signature.id,
        "fingerprint": signature.fingerprint,
        # ... many more fields ...
    }

    # ... build and return dict ...
    return details
```

**New code**:
```python
async def get_signature_details(
    self, signature_id: str
) -> SignatureDetails:
    """Retrieve detailed information about a signature."""
    signature = await self.store.get_by_id(signature_id)
    if signature is None:
        raise ValueError(f"Signature {signature_id} not found")

    # Get related/similar signatures
    related = await self.store.get_similar(signature, limit=5)

    # Build related details
    related_details = tuple(
        SignatureDetails(
            id=s.id,
            fingerprint=s.fingerprint,
            error_type=s.error_type,
            service=s.service,
            message_template=s.message_template,
            stack_hash=s.stack_hash,
            first_seen=s.first_seen,
            last_seen=s.last_seen,
            occurrence_count=s.occurrence_count,
            status=s.status,
            tags=s.tags,
            diagnosis=s.diagnosis,
        )
        for s in related
    )

    logger.debug(
        f"Retrieved signature details for {signature_id}",
        extra={"signature_id": signature_id},
    )

    return SignatureDetails(
        id=signature.id,
        fingerprint=signature.fingerprint,
        error_type=signature.error_type,
        service=signature.service,
        message_template=signature.message_template,
        stack_hash=signature.stack_hash,
        first_seen=signature.first_seen,
        last_seen=signature.last_seen,
        occurrence_count=signature.occurrence_count,
        status=signature.status,
        tags=signature.tags,
        diagnosis=signature.diagnosis,
        related_signatures=related_details,
    )
```

---

### Update Adapter Implementations
File: `/workspace/rounds/adapters/store/sqlite.py` (for get_stats)

**Update implementation to return StoreStats object instead of dict**

File: Any adapters that implement SignatureStorePort

**Update any adapters to return typed objects**

---

## Testing Updates

### Create test factories for new types
File: `/workspace/rounds/tests/core/test_services.py`

```python
@pytest.fixture
def signature_details() -> SignatureDetails:
    """Create sample signature details for testing."""
    return SignatureDetails(
        id="sig-001",
        fingerprint="abc123def456",
        error_type="ConnectionTimeoutError",
        service="payment-service",
        message_template="Failed to connect to database: timeout",
        stack_hash="hash-stack-001",
        first_seen=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        last_seen=datetime(2024, 1, 1, 12, 5, 0, tzinfo=timezone.utc),
        occurrence_count=5,
        status=SignatureStatus.NEW,
        tags=frozenset(),
        diagnosis=None,
        related_signatures=(),
    )

@pytest.fixture
def store_stats() -> StoreStats:
    """Create sample store statistics for testing."""
    return StoreStats(
        total_signatures=100,
        new_count=25,
        investigating_count=15,
        diagnosed_count=50,
        resolved_count=8,
        muted_count=2,
        average_occurrence_count=5.5,
        oldest_signature_age_hours=168.5,
        newest_signature_age_hours=0.25,
    )
```

### Update existing tests to use validated methods

```python
def test_signature_status_transition(signature: Signature) -> None:
    """Test that status transitions are validated."""
    # Valid transition
    signature.set_status(SignatureStatus.INVESTIGATING)
    assert signature.status == SignatureStatus.INVESTIGATING

    # Invalid transition
    signature.set_status(SignatureStatus.RESOLVED)
    with pytest.raises(ValueError, match="Cannot transition"):
        signature.set_status(SignatureStatus.INVESTIGATING)


def test_signature_update_occurrence(signature: Signature) -> None:
    """Test that occurrence updates are validated."""
    initial_count = signature.occurrence_count
    new_time = signature.last_seen + timedelta(hours=1)

    signature.update_occurrence(new_time)

    assert signature.occurrence_count == initial_count + 1
    assert signature.last_seen == new_time


def test_signature_update_occurrence_backwards_fails(signature: Signature) -> None:
    """Test that setting last_seen backwards fails."""
    old_time = signature.last_seen - timedelta(hours=1)

    with pytest.raises(ValueError, match="cannot be before"):
        signature.update_occurrence(old_time)


def test_error_event_validation() -> None:
    """Test that ErrorEvent validates required fields."""
    with pytest.raises(ValueError, match="trace_id cannot be empty"):
        ErrorEvent(
            trace_id="",  # Empty!
            span_id="span-1",
            service="test",
            error_type="Error",
            error_message="message",
            stack_frames=(),
            timestamp=datetime.now(timezone.utc),
            attributes={},
            severity=Severity.ERROR,
        )


def test_diagnosis_validation() -> None:
    """Test that Diagnosis validates required fields."""
    with pytest.raises(ValueError, match="cost_usd must be non-negative"):
        Diagnosis(
            root_cause="test",
            evidence=("evidence",),
            suggested_fix="fix",
            confidence=Confidence.HIGH,
            diagnosed_at=datetime.now(timezone.utc),
            model="test",
            cost_usd=-0.50,  # Negative!
        )


def test_poll_result_validation() -> None:
    """Test that PollResult validates count fields."""
    with pytest.raises(ValueError, match="errors_found must be non-negative"):
        PollResult(
            errors_found=-5,  # Negative!
            new_signatures=0,
            updated_signatures=0,
            investigations_queued=0,
            timestamp=datetime.now(timezone.utc),
        )
```

---

## Summary of Changes by File

| File | Changes | Impact |
|------|---------|--------|
| `/workspace/rounds/core/models.py` | Add __post_init__ validation to 8 types; add validation methods to Signature; add SignatureDetails and StoreStats types | Critical fixes, backward compatible |
| `/workspace/rounds/core/ports.py` | Update return type annotations for get_stats() and get_signature_details() | Minor signature updates |
| `/workspace/rounds/core/management_service.py` | Use signature.set_status() instead of direct assignment; return SignatureDetails instead of dict | Refactoring for safety |
| `/workspace/rounds/core/poll_service.py` | Use signature.update_occurrence() instead of direct field updates | Refactoring for safety |
| `/workspace/rounds/core/investigator.py` | Use signature.set_status() and set_diagnosis() instead of direct assignments | Refactoring for safety |
| Test files | Add validation tests for new methods; update fixtures for valid values | Improved test coverage |

---

## Implementation Order

1. **Step 1**: Add validation methods to Signature class
2. **Step 2**: Update all callers to use new validation methods
3. **Step 3**: Add __post_init__ validation to other types
4. **Step 4**: Define SignatureDetails and StoreStats types
5. **Step 5**: Update port signatures
6. **Step 6**: Update adapter implementations
7. **Step 7**: Add comprehensive tests
8. **Step 8**: Verify all existing tests still pass with stricter validation

