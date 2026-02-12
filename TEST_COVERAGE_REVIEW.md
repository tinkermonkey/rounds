# Test Coverage Analysis: Rounds Diagnostic System PR

## Summary

**Overall Assessment:** Good test coverage with strong behavioral testing across core services. Test suite achieves **150 passing tests** with comprehensive integration workflows. However, several critical edge cases and error scenarios lack explicit test coverage, particularly around concurrent behavior, boundary conditions in data models, and error recovery scenarios.

**Coverage Quality:** 7.5/10 - Well-structured tests following DAMP principles, good integration coverage, but missing targeted unit tests for critical error paths and model invariants.

---

## Critical Gaps (8-10 Priority)

### 1. Signature Model Invariant Violations Not Tested
**Criticality: 9/10** - Data corruption risk

The `Signature` model has critical invariants enforced in `__post_init__`:
- `occurrence_count >= 1`
- `last_seen >= first_seen`

While the code validates these on construction, there are no tests verifying:

1. **Deserialization/Reconstruction scenarios** - What happens when a signature is loaded from the database with invalid state?
2. **Boundary condition edges** - `occurrence_count == 0` or timestamps exactly equal

**Location:** `/workspace/rounds/core/models.py:116-126`
**Tests Needed:** `/workspace/rounds/tests/core/test_models.py` (NEW FILE)

```python
def test_signature_rejects_zero_occurrence_count():
    """Signature.__post_init__ should reject occurrence_count < 1."""
    with pytest.raises(ValueError, match="occurrence_count must be >= 1"):
        Signature(..., occurrence_count=0, ...)

def test_signature_rejects_inverted_timestamps():
    """Signature.__post_init__ should reject last_seen < first_seen."""
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="last_seen.*cannot be before"):
        Signature(
            ...,
            first_seen=now,
            last_seen=now - timedelta(hours=1),
            ...
        )

def test_signature_accepts_equal_timestamps():
    """Signature should accept equal first_seen and last_seen (single occurrence)."""
    now = datetime.now(timezone.utc)
    sig = Signature(..., first_seen=now, last_seen=now, occurrence_count=1)
    assert sig.first_seen == sig.last_seen
```

**Regression Risk:** If a database layer or adapter corrupts signature state, invalid signatures could propagate through the system without detection.

---

### 2. Investigator Store Error Handling Not Tested
**Criticality: 9/10** - Diagnosis persistence failures

The `Investigator.investigate()` method has complex error handling logic for store failures:
- Lines 98-104: Store error while reverting status after diagnosis failure
- Lines 118-124: Store error while persisting successful diagnosis

Currently there are NO tests for these failure modes:

**Location:** `/workspace/rounds/core/investigator.py:98-104, 118-124`
**Tests Needed:** `/workspace/rounds/tests/core/test_services.py`

```python
@pytest.mark.asyncio
async def test_investigator_handles_store_failure_reverting_status():
    """When diagnosis fails and store fails reverting status, should re-raise and log."""
    telemetry = MockTelemetryPort()

    class FailingStore(MockSignatureStorePort):
        async def update(self, sig):
            if sig.status == SignatureStatus.INVESTIGATING:
                raise RuntimeError("Store is down")

    store = FailingStore()
    diagnosis_engine = MockDiagnosisPort()
    diagnosis_engine.diagnose.side_effect = RuntimeError("Diagnosis failed")

    investigator = Investigator(telemetry, store, diagnosis_engine, MockNotificationPort(), ...)
    signature = Signature(..., status=SignatureStatus.NEW)

    # Should re-raise the original diagnosis error
    with pytest.raises(RuntimeError, match="Diagnosis failed"):
        await investigator.investigate(signature)

    # Status should remain NEW (failed to revert, but that's best-effort)
    assert signature.status == SignatureStatus.NEW

@pytest.mark.asyncio
async def test_investigator_handles_store_failure_persisting_diagnosis():
    """When persisting diagnosis fails, should re-raise and leave in INVESTIGATING state."""
    telemetry = MockTelemetryPort()
    diagnosis_engine = MockDiagnosisPort()  # Will succeed

    class FailingStore(MockSignatureStorePort):
        async def update(self, sig):
            # Fail when trying to persist DIAGNOSED status
            if sig.status == SignatureStatus.DIAGNOSED:
                raise RuntimeError("Storage quota exceeded")

    store = FailingStore()
    investigator = Investigator(telemetry, store, diagnosis_engine, MockNotificationPort(), ...)
    signature = Signature(..., status=SignatureStatus.NEW)

    # Should re-raise the store error
    with pytest.raises(RuntimeError, match="Storage quota"):
        await investigator.investigate(signature)

    # Signature should be left in INVESTIGATING state (incomplete transaction)
    assert signature.status == SignatureStatus.INVESTIGATING
```

**Regression Risk:** Store failures during diagnosis persistence could leave signatures in inconsistent states (INVESTIGATING without diagnosis).

---

### 3. ManagementService Database Failure Not Tested
**Criticality: 9/10** - Silent data inconsistency

The `ManagementService` methods call `store.update()` without handling exceptions:

**Location:** `/workspace/rounds/core/management_service.py:46-54, 78-86, 109-118, 144-149`
**Tests Needed:** `/workspace/rounds/tests/test_new_implementations.py`

```python
@pytest.mark.asyncio
async def test_mute_signature_handles_store_failure():
    """If store.update() fails, mute_signature should propagate the error."""
    class FailingStore(FakeSignatureStorePort):
        async def update(self, sig):
            raise RuntimeError("Database connection lost")

    service = ManagementService(FailingStore())
    store = FailingStore()
    await store.save(Signature(..., id="sig-123", ...))

    with pytest.raises(RuntimeError, match="Database connection lost"):
        await service.mute_signature("sig-123")

@pytest.mark.asyncio
async def test_get_signature_details_handles_store_failure():
    """If store.get_similar() fails, should propagate error."""
    class FailingStore(FakeSignatureStorePort):
        async def get_similar(self, sig, limit=5):
            raise RuntimeError("Database timeout")

    store = FailingStore()
    service = ManagementService(store)
    sig = Signature(..., id="sig-123")
    await store.save(sig)

    with pytest.raises(RuntimeError, match="Database timeout"):
        await service.get_signature_details("sig-123")
```

**Regression Risk:** Store failures would silently fail to update signatures while returning success to the API layer.

---

## Important Improvements (5-7 Priority)

### 4. PollService Error Handling Gaps
**Criticality: 7/10** - Partial failure recovery

The `PollService.execute_poll_cycle()` catches and logs errors per error event (line 115-120) but doesn't test:

1. **Store save failure for new signatures** - What if the first signature saves OK but the second fails?
2. **Fingerprinting exceptions** - If fingerprinter raises an exception, poll continues but signature is lost
3. **Partial success tracking** - Result object shows final counts but doesn't indicate which errors failed

**Location:** `/workspace/rounds/core/poll_service.py:73-120`
**Tests Needed:** `/workspace/rounds/tests/test_workflows.py`

```python
@pytest.mark.asyncio
async def test_poll_continues_after_store_failure():
    """Poll should continue processing remaining errors even if store fails for one."""
    errors = [
        ErrorEvent(trace_id="trace-1", ..., error_type="Type1", ...),
        ErrorEvent(trace_id="trace-2", ..., error_type="Type2", ...),
        ErrorEvent(trace_id="trace-3", ..., error_type="Type3", ...),
    ]

    class FailOnSecond(FakeSignatureStorePort):
        def __init__(self):
            super().__init__()
            self.save_count = 0

        async def save(self, sig):
            self.save_count += 1
            if self.save_count == 2:
                raise RuntimeError("Disk full")
            await super().save(sig)

    store = FailOnSecond()
    telemetry = FakeTelemetryPort()
    telemetry.add_errors(errors)

    poll = PollService(telemetry, store, Fingerprinter(), ...)
    result = await poll.execute_poll_cycle()

    # Should process all 3 errors despite store failure on second
    assert result.errors_found == 3
    # Only 1 and 3 should be saved successfully
    assert len(store.saved_signatures) == 2
    # This is currently not testable - no way to know which failed
```

**Regression Risk:** Silent loss of error events during high-volume polling when storage issues occur.

---

### 5. TriageEngine Priority Calculation Edge Cases
**Criticality: 6/10** - Incorrect prioritization

The `calculate_priority()` method has several time-based branches:
- Hours since last < 1: +50 points
- Hours since last < 24: +25 points
- Other: 0 points

No tests cover:
1. **Exact boundary conditions** - What happens when exactly 1 hour or exactly 24 hours has passed?
2. **Large time deltas** - Very old errors (years ago) should get 0 priority
3. **Future timestamps** - What if `last_seen` is in the future (clock skew)?

**Location:** `/workspace/rounds/core/triage.py:92-137`
**Tests Needed:** `/workspace/rounds/tests/core/test_services.py`

```python
def test_calculate_priority_exact_one_hour_boundary():
    """Test priority at exact 1-hour boundary."""
    now = datetime.now(timezone.utc)

    # Exactly 1 hour ago
    sig = Signature(..., last_seen=now - timedelta(hours=1), occurrence_count=10)
    priority = triage_engine.calculate_priority(sig)
    # Should NOT get the <1 hour bonus
    assert priority == 10 + 25  # frequency(10) + 24-hour component(25)

def test_calculate_priority_very_old_error():
    """Test priority for very old errors."""
    now = datetime.now(timezone.utc)

    # 1 year ago
    sig = Signature(..., last_seen=now - timedelta(days=365), occurrence_count=100)
    priority = triage_engine.calculate_priority(sig)
    # Should only get frequency + critical tag bonus, no recency bonus
    assert priority == 100  # max frequency, no recency bonus

def test_calculate_priority_future_timestamp():
    """Test priority when last_seen is in future (clock skew)."""
    now = datetime.now(timezone.utc)

    # 1 hour in future (clock skew scenario)
    sig = Signature(..., last_seen=now + timedelta(hours=1), occurrence_count=50)
    priority = triage_engine.calculate_priority(sig)
    # Hours will be negative - this could cause unexpected behavior
    # Should handle gracefully without throwing
    assert isinstance(priority, int)
```

**Regression Risk:** Incorrect priority ordering could cause old critical errors to be investigated before recent ones.

---

### 6. Fingerprinter Message Templatization Edge Cases
**Criticality: 5/10** - Pattern matching failures

The `templatize_message()` uses regex patterns that could have edge cases:

**Location:** `/workspace/rounds/core/fingerprint.py:61-89`
**Tests Needed:** `/workspace/rounds/tests/core/test_services.py`

```python
def test_templatize_message_ipv6_addresses():
    """Templatize should handle IPv6 addresses."""
    message = "Failed to connect to 2001:0db8:85a3:0000:0000:8a2e:0370:7334"
    result = fingerprinter.templatize_message(message)
    # Current implementation only handles IPv4, not IPv6
    # Should either handle IPv6 or document the limitation

def test_templatize_message_multiple_timestamps_same_message():
    """Should replace all timestamp occurrences."""
    message = "Error at 2024-01-01 12:00:00 recovered at 2024-01-01 12:05:00"
    result = fingerprinter.templatize_message(message)
    # Both dates should be replaced
    assert result.count("*") >= 4  # 2 dates + 2 times

def test_templatize_message_port_at_end_of_string():
    """Port replacement should work at end of string."""
    message = "Connection to server:8080"
    result = fingerprinter.templatize_message(message)
    assert ":8080" not in result
    assert ":*" in result

def test_templatize_message_uuid_case_insensitive():
    """UUID replacement should be case-insensitive."""
    message = "ID: 550E8400-E29B-41D4-A716-446655440000"
    result = fingerprinter.templatize_message(message)
    assert "550E8400" not in result
```

**Regression Risk:** Errors with IPv6, uppercase UUIDs, or unusual formatting might not be properly grouped together.

---

### 7. InvestigationContext Incomplete Data Handling
**Criticality: 6/10** - LLM receives partial context

The `Investigator.investigate()` gracefully handles incomplete traces (line 64-69) but:
1. No test verifies the diagnosis quality with partial data
2. No test checks if the diagnosis engine can handle empty tuples
3. No test ensures the warning is actually logged

**Location:** `/workspace/rounds/core/investigator.py:56-73`
**Tests Needed:** `/workspace/rounds/tests/core/test_services.py`

```python
@pytest.mark.asyncio
async def test_investigator_diagnoses_with_empty_events():
    """Investigator should handle signatures with no recent events."""
    class EmptyTelemetry(MockTelemetryPort):
        async def get_events_for_signature(self, fingerprint, limit=5):
            return []  # No events found

    telemetry = EmptyTelemetry()
    store = MockSignatureStorePort()
    diagnosis = MockDiagnosisPort()

    investigator = Investigator(telemetry, store, diagnosis, ...)
    sig = Signature(..., occurrence_count=100, status=SignatureStatus.NEW)

    # Should still produce a diagnosis with empty events
    result = await investigator.investigate(sig)
    assert result is not None

    # Verify diagnosis engine was called with empty tuples
    context = diagnosis.diagnose.call_args[0][0]
    assert context.recent_events == ()
    assert context.trace_data == ()
    assert context.related_logs == ()

@pytest.mark.asyncio
async def test_investigator_logs_incomplete_traces(caplog):
    """Investigator should log when trace retrieval is incomplete."""
    class PartialTelemetry(MockTelemetryPort):
        async def get_events_for_signature(self, fingerprint, limit=5):
            return [
                ErrorEvent(trace_id=f"trace-{i}", ...) for i in range(5)
            ]

        async def get_traces(self, trace_ids):
            # Only return 2 of 5
            return [self.create_trace(trace_ids[0]), self.create_trace(trace_ids[1])]

    investigator = Investigator(PartialTelemetry(), MockSignatureStorePort(), ...)
    sig = Signature(..., occurrence_count=100, status=SignatureStatus.NEW)

    with caplog.at_level(logging.WARNING):
        await investigator.investigate(sig)

    # Should have logged the incomplete trace warning
    assert "Incomplete trace data" in caplog.text
    assert "retrieved 2 of 5" in caplog.text
```

**Regression Risk:** Silent quality degradation - diagnoses from incomplete context could be less accurate without explicit warning in logs.

---

## Test Quality Issues (Implementation Overfit)

### 8. Brittle Test Fixtures - Tight Coupling to Mock Implementation
**Severity: 6/10** - Tests may not catch real adapter failures

Many tests use `FakeTelemetryPort` and similar fakes that are too simplistic:

**Location:** `/workspace/rounds/tests/fakes/` directory

```python
# Current FakeTelemetryPort doesn't test:
# - Timeout behavior
# - Rate limiting
# - Partial failures (some traces succeed, some fail)
# - Empty result sets with service filtering

# Better approach:
class ConfigurableFakeTelemetryPort(TelemetryPort):
    def __init__(self):
        self.fail_traces: set[str] = set()  # Which trace IDs should fail
        self.delay_ms: float = 0  # Simulate network delay
        self.errors: list[ErrorEvent] = []

    async def get_trace(self, trace_id: str) -> TraceTree:
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)
        if trace_id in self.fail_traces:
            raise RuntimeError(f"Trace {trace_id} not found")
        # ... return trace
```

---

### 9. Tests Don't Verify Log Output
**Severity: 5/10** - Silent error propagation

Several components use `logger.error()` or `logger.warning()` but tests don't verify these are called:

**Locations:**
- `/workspace/rounds/core/investigator.py:100-110` - Error reverting status
- `/workspace/rounds/core/investigator.py:65-69` - Incomplete traces
- `/workspace/rounds/core/poll_service.py:115-119` - Per-error handling
- `/workspace/rounds/core/management_service.py:56-63` - Audit logging

```python
@pytest.mark.asyncio
async def test_investigator_logs_store_revert_failure(caplog):
    """Investigator should log when it fails to revert status after diagnosis failure."""
    class FailingStore(MockSignatureStorePort):
        async def update(self, sig):
            if sig.status == SignatureStatus.INVESTIGATING:
                raise RuntimeError("Revert failed")

    with caplog.at_level(logging.ERROR):
        investigator = Investigator(FailingStore(), ...)
        with pytest.raises(RuntimeError):
            await investigator.investigate(sig)

    # Should have logged the revert failure
    assert "Failed to revert signature status" in caplog.text
```

---

## Positive Observations - Well-Tested Areas

### Strong Areas (9-10/10 Coverage)

1. **Fingerprinter Behavior** - Excellent test coverage
   - Stability, hex format validation, templatization for IPs/ports/IDs/timestamps/UUIDs
   - Tests verify both positive and negative cases
   - Good use of parameterized test data
   - **File:** `/workspace/rounds/tests/core/test_services.py:341-438`

2. **TriageEngine Decision Logic** - Comprehensive coverage
   - All major decision branches tested (should_investigate, should_notify, calculate_priority)
   - Edge case testing for status transitions and cooldown periods
   - Tag handling and critical/flaky flags
   - **File:** `/workspace/rounds/tests/core/test_services.py:445-741`

3. **Integration Workflows** - Well-designed end-to-end tests
   - Poll cycle (detect, update, deduplicate)
   - Investigation cycle workflow
   - Error recovery scenarios
   - **File:** `/workspace/rounds/tests/test_workflows.py`

4. **Model Instantiation** - Port contracts verified
   - All ports properly abstract
   - Concrete implementations instantiate correctly
   - Port method signatures enforced
   - **File:** `/workspace/rounds/tests/core/test_ports.py`

5. **ManagementService Basic Operations** - Happy path well-tested
   - Mute, resolve, retriage operations
   - Details retrieval with and without diagnosis
   - **File:** `/workspace/rounds/tests/test_new_implementations.py:42-173`

---

## Test Organization Assessment

**Strengths:**
- Clear separation: core unit tests (`test_services.py`), port contracts (`test_ports.py`), integration workflows (`test_workflows.py`), adapters (`test_new_implementations.py`)
- Good use of pytest fixtures for reusable test data
- Async test support with `@pytest.mark.asyncio`
- Clear test naming (DAMP principle: Descriptive And Meaningful Phrases)
- Comprehensive mock/fake implementations in `/workspace/rounds/tests/fakes/`

**Weaknesses:**
- No dedicated model/domain object tests (`test_models.py` missing)
- Limited negative test cases for data validation
- Lack of logging assertion tests (no caplog verification)
- Store operation failures not systematically tested

---

## Recommended Test Addition Plan

### Phase 1: Critical (Should Fix Before Merge)

1. **New file:** `/workspace/rounds/tests/core/test_models.py` - Model invariant validation
   - Signature occurrence_count validation
   - Signature timestamp ordering validation
   - Boundary condition tests
   - **Estimated effort:** 1-2 hours

2. **Extend:** `/workspace/rounds/tests/core/test_services.py` - Investigator store failures
   - Store failure during status revert
   - Store failure during diagnosis persistence
   - **Estimated effort:** 1 hour

3. **Extend:** `/workspace/rounds/tests/test_new_implementations.py` - ManagementService store failures
   - All four management operations with store failures
   - **Estimated effort:** 1-2 hours

### Phase 2: Important (Should Fix Soon After)

4. **Extend:** `/workspace/rounds/tests/test_workflows.py` - PollService partial failures
   - Store failure on specific signature
   - Fingerprinter exception handling
   - **Estimated effort:** 1-2 hours

5. **Extend:** `/workspace/rounds/tests/core/test_services.py` - TriageEngine edge cases
   - Exact time boundaries (1 hour, 24 hours)
   - Future timestamps (clock skew)
   - Very old errors
   - **Estimated effort:** 1 hour

6. **Extend:** `/workspace/rounds/tests/core/test_services.py` - Fingerprinter edge cases
   - IPv6 addresses
   - Multiple occurrences in same message
   - Case-insensitive UUID handling
   - **Estimated effort:** 1 hour

### Phase 3: Nice-to-Have (Quality Improvements)

7. **Extend:** `/workspace/rounds/tests/core/test_services.py` - Logging verification
   - Use `caplog` fixture to verify warnings and errors are logged
   - All components with logging
   - **Estimated effort:** 1-2 hours

8. **Improve fakes:** `/workspace/rounds/tests/fakes/` - Add configurability
   - Failure injection
   - Delay simulation
   - Partial failure support
   - **Estimated effort:** 2-3 hours

---

## Conclusion

The PR demonstrates **solid fundamental test coverage** with comprehensive integration testing. The test suite correctly covers:
- Core domain logic (fingerprinting, triage decisions)
- Port contracts and adapter instantiation
- Happy-path workflows
- Some error scenarios

However, **critical data consistency and error handling gaps** exist that should be addressed:

| Category | Status | Priority |
|----------|--------|----------|
| Fingerprinting logic | ✓ Well-tested | - |
| Triage decisions | ✓ Well-tested | - |
| Integration workflows | ✓ Mostly tested | 7/10 |
| Model validation | ✗ **Not tested** | **9/10** |
| Store error handling | ✗ **Partially tested** | **8-9/10** |
| Error recovery paths | ◐ Partially tested | 6-7/10 |
| Edge cases | ◐ Partially tested | 5-6/10 |
| Logging/observability | ✗ **Not verified** | 5/10 |

**Recommendation:** Add tests for the critical gaps (model validation and store failures) before merging. These prevent silent data corruption and system instability. The other gaps are valuable but less urgent for this PR.

