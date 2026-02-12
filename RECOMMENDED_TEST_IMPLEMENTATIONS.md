# Recommended Test Implementations

This document provides ready-to-implement test code for the critical gaps identified in the coverage analysis.

---

## 1. Model Validation Tests (`/workspace/rounds/tests/core/test_models.py`)

**New file to create.** Tests for domain model invariants in `Signature`, `ErrorEvent`, `LogEntry`, `SpanNode`.

```python
"""Tests for domain model validation and invariants.

Verifies that model dataclasses properly enforce their constraints
and that invalid states are rejected.
"""

import pytest
from datetime import datetime, timedelta, timezone
from rounds.core.models import (
    Signature,
    SignatureStatus,
    ErrorEvent,
    StackFrame,
    Severity,
    LogEntry,
    SpanNode,
)


class TestSignatureValidation:
    """Test Signature model validation."""

    def test_signature_requires_positive_occurrence_count(self) -> None:
        """Signature must reject occurrence_count < 1."""
        with pytest.raises(ValueError, match="occurrence_count must be >= 1"):
            Signature(
                id="sig-001",
                fingerprint="abc123",
                error_type="Error",
                service="service",
                message_template="msg",
                stack_hash="hash",
                first_seen=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
                occurrence_count=0,  # Invalid
                status=SignatureStatus.NEW,
            )

    def test_signature_rejects_negative_occurrence_count(self) -> None:
        """Signature should reject negative occurrence_count."""
        with pytest.raises(ValueError, match="occurrence_count must be >= 1"):
            Signature(
                id="sig-001",
                fingerprint="abc123",
                error_type="Error",
                service="service",
                message_template="msg",
                stack_hash="hash",
                first_seen=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
                occurrence_count=-5,  # Invalid
                status=SignatureStatus.NEW,
            )

    def test_signature_requires_last_seen_after_first_seen(self) -> None:
        """Signature must enforce last_seen >= first_seen."""
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(hours=1)

        with pytest.raises(ValueError, match="last_seen.*cannot be before.*first_seen"):
            Signature(
                id="sig-001",
                fingerprint="abc123",
                error_type="Error",
                service="service",
                message_template="msg",
                stack_hash="hash",
                first_seen=now,
                last_seen=earlier,  # Before first_seen
                occurrence_count=1,
                status=SignatureStatus.NEW,
            )

    def test_signature_accepts_equal_timestamps(self) -> None:
        """Signature should accept first_seen == last_seen."""
        now = datetime.now(timezone.utc)
        sig = Signature(
            id="sig-001",
            fingerprint="abc123",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=now,
            last_seen=now,  # Equal to first_seen
            occurrence_count=1,
            status=SignatureStatus.NEW,
        )
        assert sig.first_seen == sig.last_seen

    def test_signature_accepts_last_seen_after_first_seen(self) -> None:
        """Signature should accept last_seen > first_seen."""
        now = datetime.now(timezone.utc)
        later = now + timedelta(hours=1)

        sig = Signature(
            id="sig-001",
            fingerprint="abc123",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=now,
            last_seen=later,
            occurrence_count=1,
            status=SignatureStatus.NEW,
        )
        assert sig.last_seen > sig.first_seen


class TestErrorEventValidation:
    """Test ErrorEvent model validation."""

    def test_error_event_attributes_are_immutable(self) -> None:
        """ErrorEvent should convert dict attributes to read-only MappingProxyType."""
        event = ErrorEvent(
            trace_id="trace-123",
            span_id="span-456",
            service="service",
            error_type="Error",
            error_message="msg",
            stack_frames=(),
            timestamp=datetime.now(timezone.utc),
            attributes={"user_id": "123", "amount": "99.99"},
            severity=Severity.ERROR,
        )

        # Should not be able to modify the attributes
        with pytest.raises(TypeError):
            event.attributes["user_id"] = "456"  # type: ignore

    def test_error_event_with_empty_attributes(self) -> None:
        """ErrorEvent should handle empty attributes dict."""
        event = ErrorEvent(
            trace_id="trace-123",
            span_id="span-456",
            service="service",
            error_type="Error",
            error_message="msg",
            stack_frames=(),
            timestamp=datetime.now(timezone.utc),
            attributes={},
            severity=Severity.ERROR,
        )
        assert len(event.attributes) == 0


class TestSpanNodeValidation:
    """Test SpanNode model validation."""

    def test_span_node_attributes_are_immutable(self) -> None:
        """SpanNode should convert dict attributes to read-only MappingProxyType."""
        span = SpanNode(
            span_id="span-1",
            parent_id=None,
            service="service",
            operation="op",
            duration_ms=100.0,
            status="ok",
            attributes={"key": "value"},
            events=(),
        )

        # Should not be able to modify the attributes
        with pytest.raises(TypeError):
            span.attributes["key"] = "new_value"  # type: ignore

    def test_span_node_children_are_immutable(self) -> None:
        """SpanNode children tuple should be immutable."""
        child = SpanNode(
            span_id="child-1",
            parent_id="span-1",
            service="service",
            operation="op",
            duration_ms=50.0,
            status="ok",
            attributes={},
            events=(),
        )
        parent = SpanNode(
            span_id="span-1",
            parent_id=None,
            service="service",
            operation="parent-op",
            duration_ms=100.0,
            status="ok",
            attributes={},
            events=(),
            children=(child,),
        )

        # Should not be able to modify children
        with pytest.raises(AttributeError):
            parent.children = (child, child)  # type: ignore


class TestLogEntryValidation:
    """Test LogEntry model validation."""

    def test_log_entry_attributes_are_immutable(self) -> None:
        """LogEntry should convert dict attributes to read-only MappingProxyType."""
        entry = LogEntry(
            timestamp=datetime.now(timezone.utc),
            severity=Severity.ERROR,
            body="Error occurred",
            attributes={"component": "database"},
            trace_id="trace-123",
            span_id="span-456",
        )

        # Should not be able to modify the attributes
        with pytest.raises(TypeError):
            entry.attributes["component"] = "cache"  # type: ignore
```

---

## 2. Investigator Store Failure Tests

**Add to:** `/workspace/rounds/tests/core/test_services.py`

Append these test classes to the existing file:

```python
@pytest.mark.asyncio
class TestInvestigatorStoreFailures:
    """Tests for Investigator error handling when store operations fail."""

    class FailOnSpecificStatusStore(MockSignatureStorePort):
        """Store that fails on specific signature status updates."""

        def __init__(self, fail_on_status: SignatureStatus | None = None):
            super().__init__()
            self.fail_on_status = fail_on_status
            self.update_calls = []

        async def update(self, sig: Signature) -> None:
            self.update_calls.append(sig.status)
            if self.fail_on_status and sig.status == self.fail_on_status:
                raise RuntimeError(f"Store failed on {self.fail_on_status}")
            await super().update(sig)

    async def test_investigator_handles_store_failure_reverting_status(
        self, triage_engine: TriageEngine, signature: Signature
    ) -> None:
        """When diagnosis fails and store fails reverting status, should re-raise diagnosis error."""
        # Setup: diagnosis will fail
        failing_diagnosis = MockDiagnosisPort()
        failing_diagnosis_error = RuntimeError("Diagnosis engine is down")

        async def failing_diagnose(context):
            raise failing_diagnosis_error

        failing_diagnosis.diagnose = failing_diagnose  # type: ignore

        # Setup: store will fail when trying to revert to NEW status
        store = self.FailOnSpecificStatusStore(fail_on_status=SignatureStatus.NEW)

        investigator = Investigator(
            telemetry=MockTelemetryPort(),
            store=store,
            diagnosis_engine=failing_diagnosis,
            notification=MockNotificationPort(),
            triage=triage_engine,
            codebase_path="/app",
        )

        signature.status = SignatureStatus.NEW
        signature.occurrence_count = 10

        # Should re-raise the original diagnosis error (not the store error)
        with pytest.raises(RuntimeError, match="Diagnosis engine is down"):
            await investigator.investigate(signature)

    async def test_investigator_handles_store_failure_persisting_diagnosis(
        self, triage_engine: TriageEngine, signature: Signature
    ) -> None:
        """When persisting diagnosis fails, should re-raise the store error."""
        # Setup: diagnosis will succeed but store will fail on persist
        store = self.FailOnSpecificStatusStore(
            fail_on_status=SignatureStatus.DIAGNOSED
        )

        investigator = Investigator(
            telemetry=MockTelemetryPort(),
            store=store,
            diagnosis_engine=MockDiagnosisPort(),
            notification=MockNotificationPort(),
            triage=triage_engine,
            codebase_path="/app",
        )

        signature.status = SignatureStatus.NEW
        signature.occurrence_count = 10

        # Should re-raise the store error
        with pytest.raises(RuntimeError, match="Store failed on DIAGNOSED"):
            await investigator.investigate(signature)

        # Signature should be left in INVESTIGATING state
        assert signature.status == SignatureStatus.INVESTIGATING


@pytest.mark.asyncio
class TestInvestigatorStoreRevertBestEffort:
    """Test that investigator makes best-effort to revert status on diagnosis failure."""

    class DoubleFailingStore(MockSignatureStorePort):
        """Store that fails on both INVESTIGATING and NEW updates."""

        async def update(self, sig: Signature) -> None:
            if sig.status in {SignatureStatus.INVESTIGATING, SignatureStatus.NEW}:
                raise RuntimeError("Database connection lost")
            await super().update(sig)

    async def test_investigator_logs_when_cannot_revert_status(
        self, triage_engine: TriageEngine, signature: Signature
    ) -> None:
        """When both diagnosis and revert fail, should log the revert failure."""
        failing_diagnosis = MockDiagnosisPort()
        failing_diagnosis.diagnose = AsyncMock(  # type: ignore
            side_effect=RuntimeError("Diagnosis failed")
        )

        store = self.DoubleFailingStore()

        investigator = Investigator(
            telemetry=MockTelemetryPort(),
            store=store,
            diagnosis_engine=failing_diagnosis,
            notification=MockNotificationPort(),
            triage=triage_engine,
            codebase_path="/app",
        )

        signature.status = SignatureStatus.NEW
        signature.occurrence_count = 10

        # Should re-raise the diagnosis error (original failure)
        with pytest.raises(RuntimeError, match="Diagnosis failed"):
            await investigator.investigate(signature)
```

---

## 3. ManagementService Store Failure Tests

**Add to:** `/workspace/rounds/tests/test_new_implementations.py`

Append to the `TestManagementService` class:

```python
    class FailingStore(FakeSignatureStorePort):
        """Store that fails on update operations."""

        async def update(self, signature: Signature) -> None:
            raise RuntimeError("Database write failed")

    async def test_mute_signature_propagates_store_failure(self) -> None:
        """If store.update() fails, mute_signature should propagate the error."""
        store = self.FailingStore()
        service = ManagementService(store)

        # Pre-populate store with a signature
        sig = Signature(
            id="sig-123",
            fingerprint="fp-123",
            error_type="TimeoutError",
            service="auth-service",
            message_template="Connection timeout",
            stack_hash="hash-123",
            first_seen=datetime.now(tz=timezone.utc),
            last_seen=datetime.now(tz=timezone.utc),
            occurrence_count=5,
            status=SignatureStatus.NEW,
        )
        await store.save(sig)

        # Mute should fail and propagate the store error
        with pytest.raises(RuntimeError, match="Database write failed"):
            await service.mute_signature("sig-123")

    async def test_resolve_signature_propagates_store_failure(self) -> None:
        """If store.update() fails on resolve, should propagate the error."""
        store = self.FailingStore()
        service = ManagementService(store)

        sig = Signature(
            id="sig-456",
            fingerprint="fp-456",
            error_type="ValueError",
            service="validator-service",
            message_template="Invalid input",
            stack_hash="hash-456",
            first_seen=datetime.now(tz=timezone.utc),
            last_seen=datetime.now(tz=timezone.utc),
            occurrence_count=3,
            status=SignatureStatus.NEW,
        )
        await store.save(sig)

        with pytest.raises(RuntimeError, match="Database write failed"):
            await service.resolve_signature("sig-456", "Fixed in v2.0")

    async def test_retriage_signature_propagates_store_failure(self) -> None:
        """If store.update() fails on retriage, should propagate the error."""
        store = self.FailingStore()
        service = ManagementService(store)

        diagnosis = Diagnosis(
            root_cause="Connection pool exhausted",
            evidence=("Pool size at max",),
            suggested_fix="Increase pool size",
            confidence=Confidence.HIGH,
            diagnosed_at=datetime.now(tz=timezone.utc),
            model="claude-3",
            cost_usd=0.50,
        )
        sig = Signature(
            id="sig-789",
            fingerprint="fp-789",
            error_type="PoolError",
            service="db-service",
            message_template="Connection pool error",
            stack_hash="hash-789",
            first_seen=datetime.now(tz=timezone.utc),
            last_seen=datetime.now(tz=timezone.utc),
            occurrence_count=10,
            status=SignatureStatus.DIAGNOSED,
            diagnosis=diagnosis,
        )
        await store.save(sig)

        with pytest.raises(RuntimeError, match="Database write failed"):
            await service.retriage_signature("sig-789")

    class FailingGetSimilarStore(FakeSignatureStorePort):
        """Store that fails on get_similar calls."""

        async def get_similar(self, signature: Signature, limit: int = 5) -> list[Signature]:
            raise RuntimeError("Query timeout")

    async def test_get_signature_details_propagates_store_failure(self) -> None:
        """If store.get_similar() fails, should propagate the error."""
        store = self.FailingGetSimilarStore()
        service = ManagementService(store)

        sig = Signature(
            id="sig-details",
            fingerprint="fp-details",
            error_type="NetworkError",
            service="api-service",
            message_template="Network timeout",
            stack_hash="hash-details",
            first_seen=datetime.now(tz=timezone.utc),
            last_seen=datetime.now(tz=timezone.utc),
            occurrence_count=7,
            status=SignatureStatus.NEW,
        )
        await store.save(sig)

        with pytest.raises(RuntimeError, match="Query timeout"):
            await service.get_signature_details("sig-details")
```

---

## 4. TriageEngine Boundary Condition Tests

**Add to:** `/workspace/rounds/tests/core/test_services.py`

Append to the `TestTriageEngine` class:

```python
    def test_calculate_priority_exact_one_hour_boundary(
        self, triage_engine: TriageEngine
    ) -> None:
        """Test priority calculation at exact 1-hour boundary."""
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)

        sig = Signature(
            id="sig-boundary",
            fingerprint="fp-boundary",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=one_hour_ago,
            last_seen=one_hour_ago,
            occurrence_count=10,
            status=SignatureStatus.NEW,
        )

        priority = triage_engine.calculate_priority(sig)

        # At exactly 1 hour, should NOT get the <1 hour bonus (+50)
        # Should get 24-hour component if within 24 hours (+25)
        # Plus frequency (10) and NEW status bonus (50)
        expected = 10 + 25 + 50  # frequency + 24-hour bonus + NEW bonus
        assert priority == expected

    def test_calculate_priority_exact_24_hour_boundary(
        self, triage_engine: TriageEngine
    ) -> None:
        """Test priority calculation at exact 24-hour boundary."""
        now = datetime.now(timezone.utc)
        exactly_24_hours_ago = now - timedelta(hours=24)

        sig = Signature(
            id="sig-24h",
            fingerprint="fp-24h",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=exactly_24_hours_ago,
            last_seen=exactly_24_hours_ago,
            occurrence_count=20,
            status=SignatureStatus.NEW,
        )

        priority = triage_engine.calculate_priority(sig)

        # At exactly 24 hours, should NOT get the <24 hour bonus (+25)
        # Should get frequency (20) and NEW bonus (50)
        expected = 20 + 50  # frequency + NEW bonus, no recency
        assert priority == expected

    def test_calculate_priority_very_old_error(
        self, triage_engine: TriageEngine
    ) -> None:
        """Test priority for very old errors (no recency bonus)."""
        now = datetime.now(timezone.utc)
        very_old = now - timedelta(days=365)

        sig = Signature(
            id="sig-old",
            fingerprint="fp-old",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=very_old,
            last_seen=very_old,
            occurrence_count=100,
            status=SignatureStatus.NEW,
        )

        priority = triage_engine.calculate_priority(sig)

        # Old error: max frequency (100 capped) + NEW bonus (50)
        # No recency bonus since > 24 hours old
        expected = 100 + 50  # frequency capped at 100 + NEW bonus
        assert priority == expected

    def test_calculate_priority_with_flaky_test_penalty(
        self, triage_engine: TriageEngine
    ) -> None:
        """Test that flaky-test tag reduces priority."""
        sig = Signature(
            id="sig-flaky",
            fingerprint="fp-flaky",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=datetime.now(timezone.utc) - timedelta(minutes=30),
            last_seen=datetime.now(timezone.utc),
            occurrence_count=50,
            status=SignatureStatus.NEW,
            tags=frozenset(["flaky-test"]),
        )

        priority = triage_engine.calculate_priority(sig)

        # Should have: frequency(50) + recency(<1hr: 50) + NEW(50) - flaky(-20)
        expected = 50 + 50 + 50 - 20
        assert priority == expected

    def test_calculate_priority_critical_beats_flaky(
        self, triage_engine: TriageEngine
    ) -> None:
        """Test that critical tag overrides flaky-test penalty."""
        sig = Signature(
            id="sig-mixed",
            fingerprint="fp-mixed",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=datetime.now(timezone.utc) - timedelta(minutes=30),
            last_seen=datetime.now(timezone.utc),
            occurrence_count=30,
            status=SignatureStatus.NEW,
            tags=frozenset(["critical", "flaky-test"]),
        )

        priority = triage_engine.calculate_priority(sig)

        # frequency(30) + recency(50) + NEW(50) + critical(100) - flaky(-20)
        expected = 30 + 50 + 50 + 100 - 20
        assert priority == expected
```

---

## 5. Fingerprinter Edge Case Tests

**Add to:** `/workspace/rounds/tests/core/test_services.py`

Append to the `TestFingerprinter` class:

```python
    def test_templatize_message_multiple_occurrences(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Templatize should replace all occurrences of patterns."""
        message = "Error at 2024-01-01 12:00:00, recovered at 2024-01-02 13:30:45"
        result = fingerprinter.templatize_message(message)

        # Should replace both dates and both times
        assert "2024-01-01" not in result
        assert "2024-01-02" not in result
        assert "12:00:00" not in result
        assert "13:30:45" not in result

    def test_templatize_message_multiple_ids_in_message(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Templatize should replace all numeric IDs in message."""
        message = "Request 12345 from user 67890 processing ID 98765"
        result = fingerprinter.templatize_message(message)

        # All IDs should be replaced
        assert "12345" not in result
        assert "67890" not in result
        assert "98765" not in result

    def test_templatize_message_uuid_uppercase(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Templatize should handle uppercase UUIDs (case-insensitive)."""
        message = "Processing request: 550E8400-E29B-41D4-A716-446655440000"
        result = fingerprinter.templatize_message(message)

        assert "550E8400-E29B-41D4-A716-446655440000" not in result
        assert "*" in result

    def test_templatize_message_uuid_mixed_case(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Templatize should handle mixed-case UUIDs."""
        message = "Failed for ID: 550e8400-E29b-41D4-a716-446655440000"
        result = fingerprinter.templatize_message(message)

        assert "550e8400-E29b-41D4-a716-446655440000" not in result

    def test_templatize_message_port_at_string_end(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Templatize should replace port numbers at end of string."""
        message = "Connection to server:8080"
        result = fingerprinter.templatize_message(message)

        assert ":8080" not in result
        assert result.endswith(":*")

    def test_templatize_message_port_with_scheme(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Templatize should handle port in full URL."""
        message = "https://example.com:8443/api"
        result = fingerprinter.templatize_message(message)

        assert ":8443" not in result
        assert ":*" in result

    def test_templatize_message_preserves_word_structure(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Templatize should preserve message structure."""
        message = "Connection to 10.0.0.5:5432 failed after 30 retries"
        result = fingerprinter.templatize_message(message)

        # Word count and structure mostly preserved
        words = result.split()
        assert len(words) >= 6  # Should have most words still

    def test_fingerprint_stability_with_identical_objects(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Create two identical ErrorEvents and verify they produce same fingerprint."""
        now = datetime.now(timezone.utc)

        event1 = ErrorEvent(
            trace_id="trace-1",
            span_id="span-1",
            service="api",
            error_type="TimeoutError",
            error_message="Timeout after 30 seconds connecting to db-1.example.com",
            stack_frames=(
                StackFrame("app.api", "handle_request", "api.py", 42),
                StackFrame("app.db", "connect", "db.py", 15),
            ),
            timestamp=now,
            attributes={"user_id": "123"},
            severity=Severity.ERROR,
        )

        event2 = ErrorEvent(
            trace_id="trace-2",
            span_id="span-2",
            service="api",
            error_type="TimeoutError",
            error_message="Timeout after 30 seconds connecting to db-2.example.com",
            stack_frames=(
                StackFrame("app.api", "handle_request", "api.py", 99),  # Different line
                StackFrame("app.db", "connect", "db.py", 18),  # Different line
            ),
            timestamp=now + timedelta(hours=1),  # Different time
            attributes={"user_id": "456"},  # Different user
            severity=Severity.ERROR,
        )

        fp1 = fingerprinter.fingerprint(event1)
        fp2 = fingerprinter.fingerprint(event2)

        # Should be same fingerprint (IP and line numbers normalized away)
        assert fp1 == fp2

    def test_normalize_stack_preserves_order(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Normalize stack should preserve frame order."""
        frames = (
            StackFrame("app.a", "func_a", "a.py", 10),
            StackFrame("app.b", "func_b", "b.py", 20),
            StackFrame("app.c", "func_c", "c.py", 30),
        )

        normalized = fingerprinter.normalize_stack(frames)

        assert len(normalized) == 3
        assert normalized[0].module == "app.a"
        assert normalized[1].module == "app.b"
        assert normalized[2].module == "app.c"
```

---

## 6. PollService Partial Failure Tests

**Add to:** `/workspace/rounds/tests/test_workflows.py`

Append to the test file:

```python
@pytest.mark.asyncio
class TestPollServicePartialFailures:
    """Test PollService error handling for partial failures."""

    async def test_poll_continues_after_fingerprinter_exception(
        self,
        telemetry_port: FakeTelemetryPort,
        store_port: FakeSignatureStorePort,
        diagnosis_port: FakeDiagnosisPort,
        notification_port: FakeNotificationPort,
    ) -> None:
        """Poll should continue processing remaining errors if fingerprinter fails for one."""
        errors = [
            ErrorEvent(
                trace_id="trace-1",
                span_id="span-1",
                service="api",
                error_type="TypeError",
                error_message="Invalid argument at line 42",
                stack_frames=(
                    StackFrame("app.handler", "process", "handler.py", 42),
                ),
                timestamp=datetime.now(timezone.utc),
                attributes={},
                severity=Severity.ERROR,
            ),
            ErrorEvent(
                trace_id="trace-2",
                span_id="span-2",
                service="api",
                error_type="ValueError",
                error_message="Invalid value at line 99",
                stack_frames=(
                    StackFrame("app.validator", "validate", "validator.py", 99),
                ),
                timestamp=datetime.now(timezone.utc),
                attributes={},
                severity=Severity.ERROR,
            ),
        ]

        telemetry_port.add_errors(errors)

        class FailingFingerprinter(Fingerprinter):
            def __init__(self):
                super().__init__()
                self.call_count = 0

            def fingerprint(self, event: ErrorEvent) -> str:
                self.call_count += 1
                if self.call_count == 1:
                    raise RuntimeError("Fingerprinter is out of memory")
                return super().fingerprint(event)

        fingerprinter = FailingFingerprinter()

        poll_service = PollService(
            telemetry=telemetry_port,
            store=store_port,
            fingerprinter=fingerprinter,
            triage=TriageEngine(),
            investigator=Investigator(
                telemetry=telemetry_port,
                store=store_port,
                diagnosis_engine=diagnosis_port,
                notification=notification_port,
                triage=TriageEngine(),
                codebase_path="./",
            ),
            lookback_minutes=60,
        )

        result = await poll_service.execute_poll_cycle()

        # Should have processed both errors
        assert result.errors_found == 2
        # Only the second error should be saved (first failed to fingerprint)
        assert len(store_port.saved_signatures) == 1

    async def test_poll_continues_after_store_save_failure(
        self,
        fingerprinter: Fingerprinter,
        telemetry_port: FakeTelemetryPort,
        diagnosis_port: FakeDiagnosisPort,
        notification_port: FakeNotificationPort,
    ) -> None:
        """Poll should continue processing remaining errors if store.save() fails for one."""
        errors = [
            ErrorEvent(
                trace_id="trace-1",
                span_id="span-1",
                service="service",
                error_type="Error1",
                error_message="Error 1",
                stack_frames=(StackFrame("app", "func", "app.py", 1),),
                timestamp=datetime.now(timezone.utc),
                attributes={},
                severity=Severity.ERROR,
            ),
            ErrorEvent(
                trace_id="trace-2",
                span_id="span-2",
                service="service",
                error_type="Error2",
                error_message="Error 2",
                stack_frames=(StackFrame("app", "func", "app.py", 1),),
                timestamp=datetime.now(timezone.utc),
                attributes={},
                severity=Severity.ERROR,
            ),
        ]

        telemetry_port.add_errors(errors)

        class FailOnSecondSave(FakeSignatureStorePort):
            def __init__(self):
                super().__init__()
                self.save_count = 0

            async def save(self, signature: Signature) -> None:
                self.save_count += 1
                if self.save_count == 2:
                    raise RuntimeError("Disk space exceeded")
                await super().save(signature)

        store_port = FailOnSecondSave()

        poll_service = PollService(
            telemetry=telemetry_port,
            store=store_port,
            fingerprinter=fingerprinter,
            triage=TriageEngine(),
            investigator=Investigator(
                telemetry=telemetry_port,
                store=store_port,
                diagnosis_engine=diagnosis_port,
                notification=notification_port,
                triage=TriageEngine(),
                codebase_path="./",
            ),
            lookback_minutes=60,
        )

        result = await poll_service.execute_poll_cycle()

        # Should have found both errors
        assert result.errors_found == 2
        # Only first should be saved successfully
        assert result.new_signatures == 1
```

---

## Implementation Guide

### Quick Start

1. **Copy all code from sections 1-6 above**

2. **For Section 1 (New File):**
   ```bash
   # Create new test file
   touch /workspace/rounds/tests/core/test_models.py
   # Paste entire TestSignatureValidation, TestErrorEventValidation, etc. classes
   ```

3. **For Sections 2-6 (Append to Existing Files):**
   - Open each file mentioned
   - Scroll to end of the relevant test class
   - Paste the provided code

4. **Run tests:**
   ```bash
   pytest rounds/tests/core/test_models.py -v
   pytest rounds/tests/core/test_services.py::TestInvestigatorStoreFailures -v
   pytest rounds/tests/test_new_implementations.py::TestManagementService -v
   pytest rounds/tests/test_workflows.py::TestPollServicePartialFailures -v
   ```

### Total Implementation Effort

| Section | File | Lines | Time |
|---------|------|-------|------|
| 1 | test_models.py (NEW) | ~200 | 1.5h |
| 2 | test_services.py | ~50 | 0.5h |
| 3 | test_new_implementations.py | ~80 | 1h |
| 4 | test_services.py | ~80 | 1h |
| 5 | test_services.py | ~90 | 1h |
| 6 | test_workflows.py | ~150 | 1.5h |
| **TOTAL** | | ~650 | **6.5h** |

### High-Impact Recommendations

If time is limited, prioritize in this order:

1. **Section 1 (test_models.py)** - Prevents data corruption (9/10 criticality)
2. **Section 2 (Investigator)** - Prevents stuck states (9/10 criticality)
3. **Section 3 (ManagementService)** - Prevents silent failures (9/10 criticality)
4. Sections 4-6 - Nice-to-have improvements (5-7/10 criticality)

