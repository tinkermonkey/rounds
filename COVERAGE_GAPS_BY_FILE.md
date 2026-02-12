# Test Coverage Gaps - Organized by Source File

This document maps each source file to its test coverage status and identified gaps.

---

## Core Domain Files

### `/workspace/rounds/core/models.py`

**Status:** ⚠️ **CRITICAL GAP - No Model Validation Tests**

**Coverage:**
- ✓ Model classes defined correctly
- ✗ Validation invariants NOT tested
- ✗ Immutability properties NOT verified

**Invariants Lacking Tests:**
- `Signature.__post_init__` lines 116-126
  - ✗ `occurrence_count >= 1` validation
  - ✗ `last_seen >= first_seen` validation
  - ✗ Boundary conditions (equality, negative values)

- `ErrorEvent.__post_init__` lines 53-58
  - ✗ MappingProxyType immutability

- `SpanNode.__post_init__` lines 143-148
  - ✗ MappingProxyType immutability
  - ✗ Tuple immutability of children

- `LogEntry.__post_init__` lines 171-176
  - ✗ MappingProxyType immutability

**Test File Missing:** `/workspace/rounds/tests/core/test_models.py`

**Recommended Fix:** Create new test file with ~200 lines of invariant validation tests
**Effort:** 1.5 hours
**Priority:** 🔴 CRITICAL (9/10)

**Reference:** See `RECOMMENDED_TEST_IMPLEMENTATIONS.md` Section 1

---

### `/workspace/rounds/core/fingerprint.py`

**Status:** ✓ **GOOD - 95% Coverage**

**Coverage:**
- ✓ `fingerprint()` method - stability, output format
- ✓ `normalize_stack()` - line number stripping
- ✓ `templatize_message()` - basic patterns (IPs, ports, IDs, timestamps, UUIDs)
- ✗ Edge cases not covered

**Gaps:**
1. **IPv6 address handling** (line 72)
   - Current: Only IPv4 pattern `\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b`
   - Missing: IPv6 addresses like `2001:0db8:85a3::8a2e:0370:7334`
   - Test file: `/workspace/rounds/tests/core/test_services.py`
   - Test class: `TestFingerprinter`

2. **Multiple timestamp occurrences** (lines 77-80)
   - Current: Regex should replace all occurrences
   - Status: Unclear if regex handles multiple matches in same string
   - Need test with multiple dates/times

3. **Port at string end** (line 74)
   - Pattern: `:` followed by digits
   - Edge case: Port at very end without trailing punctuation
   - Test file: `/workspace/rounds/tests/core/test_services.py`

4. **UUID case sensitivity** (lines 82-87)
   - Pattern: Uses `re.IGNORECASE` flag
   - Status: ✓ Should work, but no explicit test for uppercase/mixed-case
   - Recommendation: Add explicit test case

**Recommended Fix:** Add 4-5 edge case tests to `TestFingerprinter` class
**Effort:** 1 hour
**Priority:** 🟡 MEDIUM (5/10) - Current tests likely adequate, edge cases rare

**Reference:** See `RECOMMENDED_TEST_IMPLEMENTATIONS.md` Section 5

---

### `/workspace/rounds/core/triage.py`

**Status:** ✓ **GOOD - Core Logic Covered, Edge Cases Missing**

**Coverage:**
- ✓ `should_investigate()` - all status/occurrence/cooldown branches
- ✓ `should_notify()` - confidence levels, new signatures, critical tags
- ✓ `calculate_priority()` - basic calculation
- ✗ Boundary conditions not tested

**Gaps:**

1. **Priority calculation boundaries** (lines 117-124)
   - Recency rules:
     - ✓ `hours_since_last < 1` → +50 points
     - ✓ `hours_since_last < 24` → +25 points
     - ✗ Exact boundary at 1.0 hour NOT tested
     - ✗ Exact boundary at 24.0 hours NOT tested
     - ✗ Very old errors (year-old) NOT tested
   - Test file: `/workspace/rounds/tests/core/test_services.py`
   - Test class: `TestTriageEngine`

2. **Clock skew scenario** (line 118)
   - When `last_seen` is in future (system clock skew)
   - `hours_since_last` would be negative
   - Current code doesn't validate, would just get 0 bonus (correct behavior)
   - Recommendation: Document and test this edge case

3. **Flaky-test penalty interaction** (line 135)
   - Pattern: Can tags combine (critical + flaky-test)?
   - Current: `+100` for critical, `-20` for flaky
   - Net: `+80` if both present
   - Status: No test for combined tags

**Test Locations:**
- Positive tests: `/workspace/rounds/tests/core/test_services.py:631-741`
- Edge cases: MISSING

**Recommended Fix:** Add 4-5 boundary/edge case tests to `TestTriageEngine` class
**Effort:** 1 hour
**Priority:** 🟡 MEDIUM (6/10) - Affects prioritization accuracy

**Reference:** See `RECOMMENDED_TEST_IMPLEMENTATIONS.md` Section 4

---

### `/workspace/rounds/core/investigator.py`

**Status:** ⚠️ **CRITICAL GAP - Error Recovery Paths Not Tested**

**Coverage:**
- ✓ Normal investigation flow
- ✓ Incomplete trace handling (logs warning)
- ✗ Store failure during status revert NOT tested
- ✗ Store failure during diagnosis persist NOT tested

**Critical Gaps:**

1. **Store error while reverting status** (lines 98-104)
   ```python
   except Exception as store_error:
       logger.error(
           f"Failed to revert signature status after diagnosis failure: "
           f"{store_error}",
           exc_info=True,
       )
   ```
   - Scenario: Diagnosis fails → tries to revert status → store fails
   - Current test coverage: ✗ NONE
   - Impact: Signature left in INVESTIGATING state
   - Test file: `/workspace/rounds/tests/core/test_services.py`
   - Should test: Store fails with RuntimeError("Revert failed")

2. **Store error while persisting diagnosis** (lines 118-124)
   ```python
   try:
       await self.store.update(signature)
   except Exception as e:
       logger.error(
           f"Failed to persist diagnosis for signature {signature.fingerprint}: {e}",
           exc_info=True,
       )
       raise
   ```
   - Scenario: Diagnosis succeeds → tries to persist → store fails
   - Current test coverage: ✗ NONE
   - Impact: Diagnosis is lost (not persisted)
   - Test file: `/workspace/rounds/tests/core/test_services.py`
   - Should test: Store fails with RuntimeError("Storage full")

3. **Incomplete trace warning not verified** (lines 64-69)
   - Code logs warning but no test verifies logging
   - Current: Only integration test checks behavior works
   - Missing: Unit test with `caplog` to verify warning message

**Test Locations:**
- Current positive tests: `/workspace/rounds/tests/core/test_services.py:1171-1211`
- Current notification failure tests: `/workspace/rounds/tests/core/test_services.py:1246-1290`
- Missing: Store failure tests

**Recommended Fix:** Add 3-4 tests to `TestInvestigatorStoreFailures` class
**Effort:** 1-1.5 hours
**Priority:** 🔴 CRITICAL (9/10) - Can cause data loss and inconsistent state

**Reference:** See `RECOMMENDED_TEST_IMPLEMENTATIONS.md` Section 2

---

### `/workspace/rounds/core/poll_service.py`

**Status:** ◐ **PARTIAL - Happy Path OK, Partial Failures Missing**

**Coverage:**
- ✓ New signature creation
- ✓ Existing signature update
- ✓ Deduplication of identical errors
- ✓ Telemetry failure handling (lines 57-67)
- ✗ Per-error failures (lines 115-120) not thoroughly tested
- ✗ Store failures on specific signatures not tested

**Gaps:**

1. **Fingerprinter exception on specific error** (line 75-76)
   ```python
   fingerprint = self.fingerprinter.fingerprint(error)
   ```
   - Scenario: Fingerprinting works for first error, fails for second
   - Current: Caught by outer try-except, continues processing
   - Missing: Test verifying second error is processed despite first failure
   - Test file: `/workspace/rounds/tests/test_workflows.py`

2. **Store save failure for specific signature** (line 102)
   ```python
   await self.store.save(signature)
   ```
   - Scenario: First signature saves OK, second save fails
   - Current: Caught by outer try-except
   - Missing: Test verifying counts are accurate when some saves fail
   - Test file: `/workspace/rounds/tests/test_workflows.py`

3. **Result object incomplete on failures** (lines 122-128)
   - `PollResult` only shows final counts
   - No way to know which errors failed (lost without logging)
   - Acceptable but worth noting in tests

**Test Locations:**
- Current: `/workspace/rounds/tests/test_workflows.py:109-248`
- Current: `/workspace/rounds/tests/core/test_services.py:742-815`
- Missing: Partial failure scenarios

**Recommended Fix:** Add 2-3 partial failure scenario tests to `TestPollServicePartialFailures` class
**Effort:** 1.5 hours
**Priority:** 🟡 IMPORTANT (7/10) - Can cause silent error loss

**Reference:** See `RECOMMENDED_TEST_IMPLEMENTATIONS.md` Section 6

---

### `/workspace/rounds/core/management_service.py`

**Status:** ⚠️ **CRITICAL GAP - Store Failures Not Tested**

**Coverage:**
- ✓ Normal operation (mute, resolve, retriage, get_details)
- ✗ Store failure handling for all operations

**Critical Gaps:**

All four methods call `store` without proper error handling:

1. **`mute_signature()` (lines 46-54)**
   ```python
   await self.store.update(signature)
   ```
   - Current: If store fails, error propagates
   - Status: ✓ Correct behavior, but NOT tested
   - Missing: Test with store.update() raising RuntimeError

2. **`resolve_signature()` (lines 78-86)**
   ```python
   await self.store.update(signature)
   ```
   - Same issue as mute_signature

3. **`retriage_signature()` (lines 109-118)**
   ```python
   await self.store.update(signature)
   ```
   - Same issue

4. **`get_signature_details()` (lines 144-199)**
   - Line 144: `await self.store.get_by_id(signature_id)`
   - Line 149: `await self.store.get_similar(signature, limit=5)`
   - Missing: Tests for both store call failures

**Test Locations:**
- Current: `/workspace/rounds/tests/test_new_implementations.py:72-172`
- Missing: Store failure scenarios

**Recommended Fix:** Add 5 tests (one per operation + one for get_similar)
**Effort:** 1-1.5 hours
**Priority:** 🔴 CRITICAL (9/10) - Silent failures to user operations

**Reference:** See `RECOMMENDED_TEST_IMPLEMENTATIONS.md` Section 3

---

### `/workspace/rounds/core/ports.py`

**Status:** ✓ **EXCELLENT - Port Contracts Well-Tested**

**Coverage:**
- ✓ All ports properly abstract (cannot instantiate)
- ✓ Concrete implementations instantiate correctly
- ✓ Method signatures enforced
- ✓ Port behaviors documented

**Test Locations:**
- `/workspace/rounds/tests/core/test_ports.py:149-605`

**No gaps identified.** This is a reference implementation for proper interface testing.

---

## Adapter Files

### `/workspace/rounds/adapters/cli/commands.py`
- **Status:** ✓ Tested (`test_new_implementations.py:179-280`)
- **Gaps:** None identified

### `/workspace/rounds/adapters/notification/github_issues.py`
- **Status:** ✓ Tested (`test_new_implementations.py:391-471`)
- **Gaps:** None identified

### `/workspace/rounds/adapters/notification/markdown.py`
- **Status:** ✓ Tested (`test_new_implementations.py:298-389`)
- **Gaps:** None identified

### `/workspace/rounds/adapters/telemetry/jaeger.py`
- **Status:** ◐ Lifecycle tested, functionality tests missing
- **Gaps:** Integration tests with actual Jaeger unavailable (expected for unit tests)

### `/workspace/rounds/adapters/telemetry/grafana_stack.py`
- **Status:** ◐ Lifecycle tested, functionality tests missing
- **Gaps:** Integration tests with actual Grafana unavailable (expected for unit tests)

### `/workspace/rounds/adapters/store/sqlite.py`
- **Status:** ✓ Referenced in composition tests but not fully tested
- **Gaps:** aiosqlite dependency not installed in test environment

---

## Test Infrastructure Files

### `/workspace/rounds/tests/fakes/`

**Status:** ◐ **GOOD but Could Be Enhanced**

**Current Fakes:**
- ✓ `FakeTelemetryPort` - Basic implementation
- ✓ `FakeSignatureStorePort` - In-memory store
- ✓ `FakeDiagnosisPort` - Configurable results
- ✓ `FakeNotificationPort` - Tracking captured reports

**Gaps:**

1. **No failure injection support**
   - Fakes don't support configurable failures
   - Makes testing error paths harder
   - Would need to create custom failing fakes for each test

2. **No latency simulation**
   - Fakes execute instantly
   - Can't test timeout behavior
   - Can't test concurrent requests

3. **No partial failure support**
   - Can't simulate "return success for first 2 items, fail on 3rd"
   - Important for testing poll cycle error recovery

**Recommendation:** Enhance fake implementations with:
```python
class ConfigurableFakeTelemetryPort(TelemetryPort):
    def __init__(self):
        self.fail_traces: set[str] = set()
        self.delay_ms: float = 0
        self.errors: list[ErrorEvent] = []

    async def get_trace(self, trace_id: str):
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)
        if trace_id in self.fail_traces:
            raise RuntimeError(f"Trace not found: {trace_id}")
        # ... return trace
```

**Effort:** 2-3 hours
**Priority:** 🟠 NICE-TO-HAVE (Code quality improvement)

---

## Test Organization

### `/workspace/rounds/tests/core/test_ports.py`
- **Status:** ✓ Excellent organization
- **Lines:** 608
- **Purpose:** Port contract verification
- **Quality:** Strong examples of interface testing

### `/workspace/rounds/tests/core/test_services.py`
- **Status:** ◐ Good but needs model tests
- **Lines:** 1290
- **Purpose:** Unit tests for core services
- **Gap:** Missing `/workspace/rounds/tests/core/test_models.py`

### `/workspace/rounds/tests/test_workflows.py`
- **Status:** ✓ Good integration tests
- **Lines:** 496
- **Purpose:** End-to-end workflow verification
- **Gap:** Missing partial failure scenarios

### `/workspace/rounds/tests/test_new_implementations.py`
- **Status:** ✓ Good adapter tests
- **Lines:** 572
- **Purpose:** Adapter and implementation testing
- **Gap:** Missing store failure scenarios

### `/workspace/rounds/tests/test_composition_root.py`
- **Status:** ⚠️ Broken (aiosqlite not installed)
- **Lines:** 366
- **Purpose:** Dependency injection verification
- **Recommendation:** These tests can be skipped in PR review (sqlite setup issue)

### Missing File: `/workspace/rounds/tests/core/test_models.py`
- **Status:** ✗ NOT CREATED
- **Should contain:** Model validation and invariant tests
- **Lines needed:** ~200
- **Priority:** 🔴 CRITICAL

---

## Summary Table

| File | Status | Criticality | Effort | Notes |
|------|--------|-------------|--------|-------|
| models.py | 🔴 Missing | CRITICAL (9/10) | 1.5h | Create test_models.py |
| fingerprint.py | ✓ 95% | LOW (5/10) | 1h | Add edge cases |
| triage.py | ✓ 90% | MEDIUM (6/10) | 1h | Add boundaries |
| investigator.py | 🔴 CRITICAL | CRITICAL (9/10) | 1.5h | Add store failures |
| poll_service.py | ◐ 80% | MEDIUM (7/10) | 1.5h | Add partial failures |
| management_service.py | 🔴 CRITICAL | CRITICAL (9/10) | 1.5h | Add store failures |
| ports.py | ✓ 100% | - | - | Excellent |
| Adapters | ✓ 90% | - | - | Mostly good |
| Fakes | ◐ 70% | LOW (5/10) | 2-3h | Quality improvement |
| **TOTAL** | | | **10-12h** | |

---

## Action Items

### Before Merge (Critical - 4 hours)
- [ ] Create `/workspace/rounds/tests/core/test_models.py`
- [ ] Add store failure tests to `test_services.py` (Investigator)
- [ ] Add store failure tests to `test_new_implementations.py` (ManagementService)

### After Merge - Sprint 1 (Important - 4.5 hours)
- [ ] Add partial failure tests to `test_workflows.py`
- [ ] Add boundary condition tests to `test_services.py` (TriageEngine)
- [ ] Add edge case tests to `test_services.py` (Fingerprinter)
- [ ] Add logging verification to multiple test files

### After Merge - Sprint 2 (Quality - 3-5 hours)
- [ ] Enhance fake implementations with failure injection
- [ ] Add caplog verification for all logging
- [ ] Review and improve test resilience

