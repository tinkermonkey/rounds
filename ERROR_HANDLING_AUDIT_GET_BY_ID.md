# Error Handling Audit: get_by_id() Method Implementations

## Executive Summary

This audit identifies **6 critical error handling issues** in the `get_by_id()` method implementations across the signature store adapters. The findings reveal **silent failures, inadequate logging, missing input validation, and test gaps** that violate the project's error handling standards.

**Critical Finding:** The SQLite adapter's `get_by_id()` method can fail silently when the database becomes unavailable, corrupted, or unreadable, returning `None` and misleading callers into thinking the signature simply doesn't exist.

---

## Issue #1: SILENT DATABASE FAILURES IN SQLITE ADAPTER

**Severity:** CRITICAL
**File:** `/workspace/rounds/adapters/store/sqlite.py`
**Lines:** 111-125
**Category:** Silent Failure

### Problem

The `get_by_id()` method lacks error handling for database-level failures. While it properly manages connection pooling via try-finally, it does NOT handle exceptions that can occur during query execution or row parsing.

```python
async def get_by_id(self, signature_id: str) -> Signature | None:
    """Look up a signature by its ID."""
    await self._init_schema()

    conn = await self._get_connection()
    try:
        cursor = await conn.execute(
            "SELECT * FROM signatures WHERE id = ?", (signature_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_signature(row)  # CAN FAIL SILENTLY
    finally:
        await self._return_connection(conn)
```

### Hidden Errors That Can Be Silently Caught

1. **aiosqlite.DatabaseError** - Database file corrupted, locked, or inaccessible
2. **aiosqlite.OperationalError** - Database is locked by another process
3. **aiosqlite.ProgrammingError** - Invalid SQL (shouldn't happen but could with schema changes)
4. **ValueError** - Raised by `_row_to_signature()` when row is malformed (see lines 280-356)
5. **json.JSONDecodeError** - Diagnosis JSON parsing fails (lines 318-324)
6. **KeyError** - Missing expected diagnosis field (line 378)
7. **TypeError** - Unexpected data type in database column

### User Impact

When a database error occurs:

1. **No distinction between "not found" and "database error"** - Both return `None`
2. **Misleading error messages** - Callers in `management_service.py` raise `ValueError: Signature {id} not found` when the actual problem is database corruption
3. **Silent cascading failures** - Management operations appear to succeed but actually fail
4. **Impossible to debug** - The actual database error is completely hidden from logs
5. **Production incidents masked** - Database availability issues go unnoticed until they cause data loss

### Specific Call Sites Affected

**In `/workspace/rounds/core/management_service.py`:**

- Line 46: `mute_signature()` - If get_by_id() fails, user can't mute signatures
- Line 78: `resolve_signature()` - If get_by_id() fails, user can't mark signatures resolved
- Line 109: `retriage_signature()` - If get_by_id() fails, user can't retriage
- Line 144: `get_signature_details()` - If get_by_id() fails, user gets misleading "not found" error

All four methods will raise `ValueError: Signature {id} not found` when the real problem is database unavailability.

### Recommended Fix

```python
async def get_by_id(self, signature_id: str) -> Signature | None:
    """Look up a signature by its ID.

    Args:
        signature_id: UUID of the signature.

    Returns:
        Signature object if found, None if not found.

    Raises:
        ValueError: If signature_id is None or empty.
        aiosqlite.DatabaseError: If database is unavailable or corrupted.
        ValueError: If database row is malformed (data integrity issue).
    """
    # Validate input
    if not signature_id or not isinstance(signature_id, str):
        logger.warning(
            f"Invalid signature_id in get_by_id: {signature_id!r}",
            extra={"signature_id": signature_id}
        )
        return None

    await self._init_schema()

    conn = await self._get_connection()
    try:
        try:
            cursor = await conn.execute(
                "SELECT * FROM signatures WHERE id = ?", (signature_id,)
            )
            row = await cursor.fetchone()
        except aiosqlite.DatabaseError as e:
            # Database-level error - connection, locking, corruption, etc.
            logger.error(
                f"Database error retrieving signature {signature_id}: {e}",
                exc_info=True,
                extra={
                    "signature_id": signature_id,
                    "error_type": type(e).__name__,
                }
            )
            raise
        except aiosqlite.ProgrammingError as e:
            # SQL or schema error
            logger.error(
                f"Programming error retrieving signature {signature_id}: {e}",
                exc_info=True,
                extra={"signature_id": signature_id}
            )
            raise ValueError(f"Database schema error: {e}") from e

        if row is None:
            logger.debug(f"Signature not found: {signature_id}")
            return None

        # Parse row - this can raise ValueError for malformed data
        try:
            signature = self._row_to_signature(row)
            logger.debug(
                f"Retrieved signature {signature_id}",
                extra={"fingerprint": signature.fingerprint}
            )
            return signature
        except ValueError as e:
            logger.error(
                f"Failed to parse signature {signature_id} from database: {e}",
                exc_info=True,
                extra={"signature_id": signature_id}
            )
            # Data integrity issue - don't hide this
            raise
    finally:
        await self._return_connection(conn)
```

### Verification Steps

Add test to verify error handling:

```python
@pytest.mark.asyncio
async def test_get_by_id_handles_database_error(sqlite_store):
    """get_by_id() must raise DatabaseError when database fails."""
    # Mock the connection to raise DatabaseError
    with patch.object(sqlite_store, '_get_connection') as mock:
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = aiosqlite.DatabaseError("Database locked")
        mock.return_value = mock_conn

        with pytest.raises(aiosqlite.DatabaseError):
            await sqlite_store.get_by_id("sig-123")
```

---

## Issue #2: MISSING INPUT VALIDATION IN FAKE STORE

**Severity:** HIGH
**File:** `/workspace/rounds/tests/fakes/store.py`
**Lines:** 28-34
**Category:** Test Gap / Silent Failure

### Problem

The fake store implementation doesn't validate input parameters, allowing invalid IDs (None, empty string, wrong type) to pass through silently.

```python
async def get_by_id(self, signature_id: str) -> Signature | None:
    """Get a signature by ID.

    Returns the signature if found, None otherwise.
    """
    self.get_by_id_calls.append(signature_id)
    return self.signatures_by_id.get(signature_id)
```

### Hidden Errors

1. **None passed as signature_id** - Silently added to tracking list, dict.get(None) returns None
2. **Empty string "" passed** - Silently added to tracking list, dict.get("") returns None
3. **Integer or UUID object passed** - Silent type mismatch, can cause odd behavior in dict keys
4. **Whitespace-only string** - Allowed even though it's not a valid UUID

### User Impact

1. **Tests pass with invalid inputs that fail in production** - Real SQLite adapter should reject these
2. **Bugs hidden until production** - Code that passes None or "" to get_by_id() goes undetected
3. **Inconsistent test/prod behavior** - Tests validate differently than production code
4. **Hard to debug test failures** - When tests fail due to input handling, the issue isn't obvious

### Example of Hidden Bug

This code would pass tests but fail in production:

```python
# In some service code:
signature_id = extract_id_from_request()  # Could return None or ""
signature = await store.get_by_id(signature_id)  # BUG: doesn't validate

if signature is None:
    raise ValueError("Not found")  # Wrong error message if ID was None
```

Tests with fake store would pass. In production, SQLite adapter would eventually raise ValueError for invalid ID, but the error message would be confusing.

### Recommended Fix

```python
async def get_by_id(self, signature_id: str) -> Signature | None:
    """Get a signature by ID.

    Args:
        signature_id: UUID of the signature. Must be non-empty string.

    Returns:
        Signature if found, None if not found.

    Raises:
        ValueError: If signature_id is None, empty, or not a string.
    """
    # Validate input to catch bugs in tests
    if not isinstance(signature_id, str):
        raise ValueError(
            f"signature_id must be a string, got {type(signature_id).__name__}"
        )
    if not signature_id or not signature_id.strip():
        raise ValueError(
            f"signature_id must be non-empty, got {signature_id!r}"
        )

    self.get_by_id_calls.append(signature_id)
    return self.signatures_by_id.get(signature_id)
```

### Verification Steps

Add tests to fake store:

```python
@pytest.mark.asyncio
async def test_get_by_id_rejects_none():
    """get_by_id() must raise ValueError for None."""
    store = FakeSignatureStorePort()
    with pytest.raises(ValueError, match="must be a string"):
        await store.get_by_id(None)

@pytest.mark.asyncio
async def test_get_by_id_rejects_empty_string():
    """get_by_id() must raise ValueError for empty string."""
    store = FakeSignatureStorePort()
    with pytest.raises(ValueError, match="non-empty"):
        await store.get_by_id("")

@pytest.mark.asyncio
async def test_get_by_id_rejects_integer():
    """get_by_id() must raise ValueError for non-string."""
    store = FakeSignatureStorePort()
    with pytest.raises(ValueError, match="must be a string"):
        await store.get_by_id(12345)
```

---

## Issue #3: INADEQUATE INPUT VALIDATION IN ALL IMPLEMENTATIONS

**Severity:** HIGH
**Files:**
- `/workspace/rounds/adapters/store/sqlite.py` (lines 111-125)
- `/workspace/rounds/tests/fakes/store.py` (lines 28-34)

**Category:** Silent Failure / Edge Case Handling

### Problem

Neither implementation validates the `signature_id` parameter. The port interface specifies the parameter as `signature_id: str` but nowhere is it validated that:

1. `signature_id` is not None
2. `signature_id` is not an empty string
3. `signature_id` is actually a string type
4. `signature_id` is not whitespace-only

### Hidden Errors

1. **Code calls `get_by_id(None)`** - Silently returns None, misleads caller
2. **Code calls `get_by_id("")`** - Query executes for empty ID, finds nothing, misleads caller
3. **Code calls `get_by_id(123)`** - Type mismatch in SQLite query parameter
4. **Code calls `get_by_id("   ")`** - Whitespace-only string passes through

### User Impact

1. **Callers can't distinguish "not found" from "invalid input"** - Both return None
2. **Bugs pass silently** - Code that accidentally passes wrong types isn't caught
3. **Error messages are misleading** - When signature lookup fails due to None input, the error "Signature not found" is confusing
4. **Hard to trace bugs** - No log showing that invalid input was passed

### Example Scenario

In `management_service.py` line 46:

```python
async def mute_signature(self, signature_id: str, reason: str | None = None) -> None:
    signature = await self.store.get_by_id(signature_id)  # NO VALIDATION
    if signature is None:
        raise ValueError(f"Signature {signature_id} not found")
```

If `signature_id` is None (e.g., from a missing API parameter):

1. `get_by_id(None)` executes
2. Returns None (no logging, no error)
3. Code raises `ValueError: Signature None not found` ← CONFUSING ERROR MESSAGE
4. User has no idea they passed invalid input

### Recommended Fix

Add validation at START of method:

```python
async def get_by_id(self, signature_id: str) -> Signature | None:
    """Look up a signature by its ID.

    Args:
        signature_id: UUID of the signature. Must be non-empty string.

    Returns:
        Signature object if found, None otherwise.

    Raises:
        ValueError: If signature_id is None, empty string, or not a string.
        Exception: If database is unavailable.
    """
    # Input validation
    if not isinstance(signature_id, str):
        raise ValueError(
            f"signature_id must be a string, got {type(signature_id).__name__}: {signature_id!r}"
        )

    if not signature_id.strip():
        raise ValueError(
            f"signature_id must be non-empty string, got {signature_id!r}"
        )

    # ... rest of implementation
```

This way:
- Bugs are caught immediately with clear error
- Callers know to fix their input
- Logs show "Invalid input" not "Not found"

---

## Issue #4: INCONSISTENT ERROR HANDLING BETWEEN REAL AND FAKE IMPLEMENTATIONS

**Severity:** HIGH
**Files:**
- `/workspace/rounds/adapters/store/sqlite.py` (real implementation)
- `/workspace/rounds/tests/fakes/store.py` (fake implementation)

**Category:** Test Gap

### Problem

The fake store and real SQLite store handle errors VERY differently:

**Real SQLite Implementation:**
- Has try-finally for connection pooling
- Calls `_row_to_signature()` which validates and can raise ValueError
- Currently DOES NOT catch database errors (Issue #1)
- Currently DOES NOT log failures (Issue #1)
- No input validation

**Fake Implementation:**
- Direct dict lookup, no I/O
- No error handling
- No logging
- No input validation

### Consequences

1. **Error handling is not tested** - Fake never raises database errors, so error paths in management service never execute in tests
2. **Tests give false confidence** - Tests pass with fake but fail with real SQLite
3. **Production errors untested** - Database unavailability, corruption, etc. are never tested
4. **Different behavior in test vs production** - Confuses developers debugging issues

### Example: Untested Error Path

In `/workspace/rounds/core/management_service.py` lines 46-48:

```python
signature = await self.store.get_by_id(signature_id)
if signature is None:
    raise ValueError(f"Signature {signature_id} not found")
```

This error path is NEVER tested because:
- Fake store always returns None or a Signature, never raises exception
- Real SQLite store (per Issue #1) also just returns None for database errors
- No test ever checks "what if database is unavailable during mute_signature()?"

### Recommended Fix

1. **Add input validation to both** - They should match
2. **Update fake to optionally simulate errors** for testing error paths
3. **Add integration tests** with real SQLite store that fail the database

Example enhancement to fake:

```python
class FakeSignatureStorePort(SignatureStorePort):
    """In-memory signature store for testing.

    Can be configured to simulate database errors for testing error handling.
    """

    def __init__(self, fail_on_next_get_by_id: bool = False):
        """Initialize with optional failure mode.

        Args:
            fail_on_next_get_by_id: If True, next get_by_id() raises RuntimeError.
        """
        self.signatures: dict[str, Signature] = {}
        self.signatures_by_id: dict[str, Signature] = {}
        self.fail_on_next_get_by_id = fail_on_next_get_by_id
        self.get_by_id_calls: list[str] = []

    async def get_by_id(self, signature_id: str) -> Signature | None:
        """Get a signature by ID.

        Can optionally raise RuntimeError to simulate database failure.
        """
        # Input validation (matching real implementation)
        if not isinstance(signature_id, str) or not signature_id.strip():
            raise ValueError(
                f"signature_id must be non-empty string, got {signature_id!r}"
            )

        # Optional failure mode for testing error handling
        if self.fail_on_next_get_by_id:
            self.fail_on_next_get_by_id = False
            raise RuntimeError("Simulated database error")

        self.get_by_id_calls.append(signature_id)
        return self.signatures_by_id.get(signature_id)
```

Then add integration tests:

```python
@pytest.mark.asyncio
async def test_mute_signature_handles_database_error():
    """mute_signature() must handle database errors gracefully."""
    store = FakeSignatureStorePort(fail_on_next_get_by_id=True)
    sig = Signature(...)
    await store.save(sig)

    service = ManagementService(store)

    with pytest.raises(RuntimeError, match="database"):
        await service.mute_signature(sig.id)
```

---

## Issue #5: INSUFFICIENT CONTEXT LOGGING IN MANAGEMENT SERVICE

**Severity:** MEDIUM
**File:** `/workspace/rounds/core/management_service.py`
**Lines:** 46, 78, 109, 144
**Category:** Poor Observability

### Problem

All four management operations call `get_by_id()` but don't wrap it with any logging that explains what operation is being attempted. When errors occur, logs lack context.

```python
async def mute_signature(self, signature_id: str, reason: str | None = None) -> None:
    signature = await self.store.get_by_id(signature_id)  # NO CONTEXT
    if signature is None:
        raise ValueError(f"Signature {signature_id} not found")
    # ... rest
```

### Hidden Errors

When `get_by_id()` eventually raises an exception (per Issue #1 recommended fix), the error will lack context about:
- What operation was being attempted (mute vs resolve vs retriage vs get_details)
- Whether this is a user-initiated operation or automated
- The reason (if provided) for the operation

### User Impact

1. **Error logs don't explain what was being done** - Admin looks at logs and sees "Database error" but doesn't know if it was during mute, resolve, or retriage
2. **Can't correlate operations to errors** - No trace of which operation failed
3. **Difficult to investigate issues** - Developers can't quickly understand what went wrong

### Example of Poor Error Context

If database becomes unavailable during mute:
```
ERROR: Failed to look up signature for mute operation: DatabaseError
```

Without wrapper, you'd just see:
```
ERROR: Database error retrieving signature sig-001
```

The second message doesn't explain it was during a mute operation.

### Recommended Fix

Wrap each `get_by_id()` call with operation context:

```python
async def mute_signature(self, signature_id: str, reason: str | None = None) -> None:
    """Mute a signature to suppress further notifications."""
    try:
        signature = await self.store.get_by_id(signature_id)
    except Exception as e:
        # Log with operation context
        logger.error(
            f"Failed to look up signature for mute operation: {e}",
            exc_info=True,
            extra={
                "signature_id": signature_id,
                "operation": "mute",
                "reason": reason,
            }
        )
        raise

    if signature is None:
        raise ValueError(f"Signature {signature_id} not found")

    # ... rest of code
```

Do this for all four methods: `mute_signature()`, `resolve_signature()`, `retriage_signature()`, and `get_signature_details()`.

---

## Issue #6: AMBIGUOUS PORT CONTRACT EXCEPTION SPECIFICATION

**Severity:** MEDIUM
**File:** `/workspace/rounds/core/ports.py`
**Lines:** 166-177
**Category:** Contract Clarity

### Problem

The port interface's exception specification is too vague:

```python
@abstractmethod
async def get_by_id(self, signature_id: str) -> Signature | None:
    """Look up a signature by its ID.

    Args:
        signature_id: UUID of the signature.

    Returns:
        Signature object if found, None otherwise.

    Raises:
        Exception: If database is unavailable.  # <-- TOO VAGUE
    """
```

### Issues with This Contract

1. **"Exception" is too generic** - Could mean anything, doesn't help implementers
2. **Doesn't list specific exception types** - Should be `RuntimeError`, `DatabaseError`, `ValueError`, etc.
3. **Doesn't document input validation** - Should explain what happens for None/empty/invalid input
4. **Ambiguous return behavior** - Should clarify: never return None for input validation errors
5. **Doesn't match actual implementations** - SQLite currently returns None for database errors (Issue #1)

### User Impact

1. **Implementers don't know what to do** - Each adapter might interpret differently
2. **Callers don't know what to expect** - Should they catch ValueError? RuntimeError? Everything?
3. **Tests incomplete** - Don't know what error scenarios to test
4. **Inconsistent behavior** - Different adapters might raise different exceptions

### Recommended Fix

Update the contract to be explicit:

```python
@abstractmethod
async def get_by_id(self, signature_id: str) -> Signature | None:
    """Look up a signature by its ID.

    Args:
        signature_id: UUID of the signature. Must be a non-empty string.

    Returns:
        Signature object if found.
        None if signature with given ID does not exist.
        NEVER returns None for invalid input - always raises ValueError instead.

    Raises:
        ValueError:
            - If signature_id is None, empty string, or not a string
            - If database row is malformed (data integrity error)
        RuntimeError:
            - If database is unavailable (connection error, timeout, etc.)
            - If database is locked or has I/O errors
        Exception:
            - Any other unexpected error from the database backend.

    Implementation notes:
        - Input validation is mandatory: reject None, empty strings, non-strings
        - Database errors MUST be raised, not silently returned as None
        - All failures MUST be logged with appropriate context
    """
```

Then update the implementations to match this contract.

---

## Summary Table

| # | Issue | Severity | File | Lines | Type | Fix Effort |
|---|-------|----------|------|-------|------|-----------|
| 1 | Silent database errors in SQLite | CRITICAL | sqlite.py | 111-125 | Silent Failure | High |
| 2 | No input validation in fake store | HIGH | store.py | 28-34 | Test Gap | Low |
| 3 | Inadequate input validation (all) | HIGH | sqlite.py, store.py | 111-125, 28-34 | Silent Failure | Low |
| 4 | Inconsistent error handling | HIGH | sqlite.py, store.py | Multiple | Test Gap | Medium |
| 5 | Missing context in management service | MEDIUM | management_service.py | 46,78,109,144 | Observability | Low |
| 6 | Ambiguous port contract | MEDIUM | ports.py | 166-177 | Contract | Low |

---

## Violations of Project Error Handling Standards

These findings violate the project's documented error handling requirements:

### Requirement 1: "Never silently fail in production code"
**Status:** VIOLATED
- Issue #1: Database errors silently return None
- Issue #3: Invalid inputs silently return None

### Requirement 2: "Always log errors using appropriate logging functions"
**Status:** VIOLATED
- Issue #1: Database errors are not logged
- Issue #5: Management operations don't log context

### Requirement 3: "Include relevant context in error messages"
**Status:** VIOLATED
- Issue #1: No context about what operation failed
- Issue #5: No context about operation type or reason

### Requirement 4: "Catch blocks must be specific"
**Status:** NOT APPLICABLE - There are no catch blocks to review (Issue #1)

### Requirement 5: "Mock/fake implementations must match real implementations"
**Status:** VIOLATED
- Issue #4: Fake store doesn't validate input like real implementation should

---

## Recommended Implementation Priority

### Phase 1 (Urgent - Address Critical Issues)
1. **Issue #1** - Add error handling and logging to SQLite.get_by_id()
2. **Issue #3** - Add input validation to all implementations
3. **Issue #6** - Update port contract to clarify expectations

### Phase 2 (High Priority - Close Test Gaps)
4. **Issue #2** - Add input validation to fake store
5. **Issue #4** - Enhance fake store with optional failure mode for testing
6. Add integration tests with real SQLite that verify error handling

### Phase 3 (Medium Priority - Improve Observability)
7. **Issue #5** - Add operation context logging in management service

---

## Testing Strategy

### Unit Tests Needed

```python
# Test input validation
- get_by_id(None) raises ValueError
- get_by_id("") raises ValueError
- get_by_id(123) raises ValueError
- get_by_id("  ") raises ValueError

# Test successful cases
- get_by_id("valid-id") returns Signature if found
- get_by_id("valid-id") returns None if not found

# Test database error handling (with enhanced fake)
- get_by_id() raises DatabaseError when database unavailable
- Error message includes signature_id
- Error is logged with appropriate severity
```

### Integration Tests Needed

```python
# Test with real SQLite that simulate database issues
- Database file deleted while querying
- Database file becomes read-only
- Database file corrupted
- Database locked by another process
- Management service operations fail gracefully with database errors
```

---

## Acceptance Criteria

All changes are complete when:

1. ✓ SQLite.get_by_id() catches database errors and re-raises with logging
2. ✓ Both implementations validate input and raise ValueError for invalid IDs
3. ✓ Port contract explicitly specifies exception types and input requirements
4. ✓ Fake store matches real implementation error handling (with optional failure mode)
5. ✓ Management service wraps get_by_id() calls with operation context logging
6. ✓ All unit tests pass with real and fake implementations
7. ✓ Integration tests verify error handling with database failures
8. ✓ No silent failures remain in get_by_id() code paths
9. ✓ All logs include appropriate context (signature_id, operation type, etc.)

