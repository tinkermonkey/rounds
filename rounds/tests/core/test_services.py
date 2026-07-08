"""Unit tests for core domain services.

Tests verify that Fingerprinter, TriageEngine, Investigator, and PollService
implement the core diagnostic logic correctly.
"""

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from rounds.core.fingerprint import Fingerprinter
from rounds.core.investigator import Investigator
from rounds.core.management_service import ManagementService
from rounds.core.models import (
    Diagnosis,
    ErrorEvent,
    InvestigationContext,
    PollResult,
    Severity,
    Signature,
    SignatureStatus,
    StackFrame,
    TraceTree,
)
from rounds.core.poll_service import PollService
from rounds.core.triage import TriageEngine
from rounds.tests.fakes import (
    FakeBudgetTracker,
    FakeDiagnosisPort,
    FakeNotificationPort,
    FakeSignatureStorePort,
    FakeTelemetryPort,
)

# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def error_event() -> ErrorEvent:
    """Create a sample error event for testing."""
    return ErrorEvent(
        trace_id="trace-123",
        span_id="span-456",
        service="payment-service",
        error_type="ConnectionTimeoutError",
        error_message="Failed to connect to database at 10.0.0.5:5432 for user 12345",
        stack_frames=(
            StackFrame(
                module="payment.service",
                function="process_charge",
                filename="service.py",
                lineno=42,
            ),
            StackFrame(
                module="payment.db",
                function="execute",
                filename="db.py",
                lineno=15,
            ),
        ),
        timestamp=datetime.now(UTC),
        attributes={"user_id": "123", "amount": "99.99"},
        severity=Severity.ERROR,
    )


@pytest.fixture
def signature() -> Signature:
    """Create a sample signature for testing."""
    return Signature(
        id="sig-001",
        fingerprint="abc123def456",
        error_type="ConnectionTimeoutError",
        service="payment-service",
        message_template="Failed to connect to database: timeout",
        stack_hash="hash-stack-001",
        first_seen=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        last_seen=datetime(2024, 1, 1, 12, 5, 0, tzinfo=UTC),
        occurrence_count=5,
        status=SignatureStatus.NEW,
    )


@pytest.fixture
def diagnosis() -> Diagnosis:
    """Create a sample diagnosis for testing."""
    return Diagnosis(
        root_cause="Database connection pool exhausted",
        evidence=("Stack trace shows pool limit reached",),
        suggested_fix="Increase connection pool size",
        confidence="high",
        diagnosed_at=datetime(2024, 1, 1, 12, 30, 0, tzinfo=UTC),
        model="claude-opus-4",
        cost_usd=0.45,
    )


@pytest.fixture
def fingerprinter() -> Fingerprinter:
    """Create a Fingerprinter instance."""
    return Fingerprinter()


@pytest.fixture
def triage_engine() -> TriageEngine:
    """Create a TriageEngine instance."""
    return TriageEngine(
        min_occurrence_for_investigation=3,
        investigation_cooldown_hours=24,
        high_confidence_threshold="high",
    )


# ============================================================================
# Test-Specific Port Subclasses
# ============================================================================
#
# These subclasses extend the standard fakes to add test-specific behavior
# (like tracking calls or simulating failures for specific test scenarios).
# The majority of the port behavior is inherited from the base fakes.


class FailingNotificationPort(FakeNotificationPort):
    """Extends FakeNotificationPort to simulate notification failures."""

    async def report(self, signature: Signature, diagnosis: Diagnosis) -> None:
        """Always fails."""
        raise RuntimeError("Notification service is unavailable")


class PartialTraceTelemetryPort(FakeTelemetryPort):
    """Extends FakeTelemetryPort to simulate intermittent trace fetch failures."""

    def __init__(self, fail_trace_count: int = 2) -> None:
        """Initialize with failure count."""
        super().__init__()
        self.fail_trace_count = fail_trace_count
        self.fetch_count = 0

    async def get_trace(self, trace_id: str) -> TraceTree:
        """Fails for the first N trace fetch attempts."""
        self.fetch_count += 1
        if self.fetch_count <= self.fail_trace_count:
            raise RuntimeError(f"Failed to fetch trace {trace_id}")
        return await super().get_trace(trace_id)


# ============================================================================
# Fingerprinter Tests
# ============================================================================


class TestFingerprinter:
    """Tests for the Fingerprinter service."""

    def test_fingerprint_stability(
        self, fingerprinter: Fingerprinter, error_event: ErrorEvent
    ) -> None:
        """Same error should produce the same fingerprint."""
        fp1 = fingerprinter.fingerprint(error_event)
        fp2 = fingerprinter.fingerprint(error_event)
        assert fp1 == fp2

    def test_fingerprint_is_hex_string(
        self, fingerprinter: Fingerprinter, error_event: ErrorEvent
    ) -> None:
        """Fingerprint should be a valid hex string."""
        fp = fingerprinter.fingerprint(error_event)
        assert len(fp) == 64  # SHA256 hex digest
        assert all(c in "0123456789abcdef" for c in fp)

    def test_different_errors_different_fingerprints(
        self, fingerprinter: Fingerprinter, error_event: ErrorEvent
    ) -> None:
        """Different errors should produce different fingerprints."""
        fp1 = fingerprinter.fingerprint(error_event)

        # Create a different error
        different_error = ErrorEvent(
            trace_id="trace-456",
            span_id="span-789",
            service="different-service",
            error_type="ValueError",
            error_message="Different error message",
            stack_frames=error_event.stack_frames,
            timestamp=error_event.timestamp,
            attributes=error_event.attributes,
            severity=Severity.ERROR,
        )
        fp2 = fingerprinter.fingerprint(different_error)
        assert fp1 != fp2

    def test_normalize_stack_strips_line_numbers(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Normalize stack should strip line numbers."""
        frames = (
            StackFrame(
                module="app.service",
                function="process",
                filename="service.py",
                lineno=42,
            ),
            StackFrame(module="app.db", function="query", filename="db.py", lineno=15),
        )

        normalized = fingerprinter.normalize_stack(frames)

        assert len(normalized) == 2
        assert normalized[0].lineno is None
        assert normalized[1].lineno is None
        assert normalized[0].module == "app.service"
        assert normalized[1].function == "query"

    def test_templatize_message_replaces_ips(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Templatize should replace IP addresses."""
        message = "Failed to connect to 10.0.0.5"
        result = fingerprinter.templatize_message(message)
        assert "10.0.0.5" not in result
        assert "*" in result

    def test_templatize_message_replaces_ports(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Templatize should replace port numbers."""
        message = "Connection to localhost:5432 failed"
        result = fingerprinter.templatize_message(message)
        assert ":5432" not in result
        assert ":*" in result

    def test_templatize_message_replaces_ids(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Templatize should replace numeric IDs."""
        message = "User ID 12345 not found"
        result = fingerprinter.templatize_message(message)
        assert "12345" not in result

    def test_templatize_message_replaces_timestamps(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Templatize should replace timestamps."""
        message = "Error at 2024-01-01 12:30:45"
        result = fingerprinter.templatize_message(message)
        assert "2024-01-01" not in result
        assert "12:30:45" not in result

    def test_templatize_message_replaces_uuids(
        self, fingerprinter: Fingerprinter
    ) -> None:
        """Templatize should replace UUIDs."""
        message = "Request 550e8400-e29b-41d4-a716-446655440000 failed"
        result = fingerprinter.templatize_message(message)
        assert "550e8400-e29b-41d4-a716-446655440000" not in result


# ============================================================================
# TriageEngine Tests
# ============================================================================


class TestTriageEngine:
    """Tests for the TriageEngine service."""

    def test_constructor_validates_min_occurrence(self) -> None:
        """Test that TriageEngine rejects min_occurrence_for_investigation <= 0."""
        with pytest.raises(
            ValueError, match="min_occurrence_for_investigation must be positive"
        ):
            TriageEngine(
                min_occurrence_for_investigation=0,
                investigation_cooldown_hours=1,
            )

        with pytest.raises(
            ValueError, match="min_occurrence_for_investigation must be positive"
        ):
            TriageEngine(
                min_occurrence_for_investigation=-1,
                investigation_cooldown_hours=1,
            )

    def test_constructor_validates_cooldown(self) -> None:
        """Test that TriageEngine rejects investigation_cooldown_hours <= 0."""
        with pytest.raises(
            ValueError, match="investigation_cooldown_hours must be positive"
        ):
            TriageEngine(
                min_occurrence_for_investigation=1,
                investigation_cooldown_hours=0,
            )

        with pytest.raises(
            ValueError, match="investigation_cooldown_hours must be positive"
        ):
            TriageEngine(
                min_occurrence_for_investigation=1,
                investigation_cooldown_hours=-1,
            )

    def test_constructor_accepts_valid_parameters(self) -> None:
        """Test that TriageEngine accepts valid parameters."""
        engine = TriageEngine(
            min_occurrence_for_investigation=2,
            investigation_cooldown_hours=4,
        )
        assert engine.min_occurrence_for_investigation == 2
        assert engine.investigation_cooldown_hours == 4

    def test_should_investigate_new_signature_below_threshold(
        self, triage_engine: TriageEngine
    ) -> None:
        """Should not investigate if occurrence count below threshold."""
        sig = Signature(
            id="sig-001",
            fingerprint="fp-001",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            occurrence_count=1,  # Below default threshold of 3
            status=SignatureStatus.NEW,
        )
        assert not triage_engine.should_investigate(sig)

    def test_should_investigate_new_signature_above_threshold(
        self, triage_engine: TriageEngine
    ) -> None:
        """Should investigate if occurrence count meets threshold."""
        sig = Signature(
            id="sig-001",
            fingerprint="fp-001",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            occurrence_count=5,  # Above threshold of 3
            status=SignatureStatus.NEW,
        )
        assert triage_engine.should_investigate(sig)

    def test_should_not_investigate_resolved_signature(
        self, triage_engine: TriageEngine
    ) -> None:
        """Should not investigate resolved signatures."""
        sig = Signature(
            id="sig-001",
            fingerprint="fp-001",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            occurrence_count=100,
            status=SignatureStatus.RESOLVED,
        )
        assert not triage_engine.should_investigate(sig)

    def test_should_not_investigate_muted_signature(
        self, triage_engine: TriageEngine
    ) -> None:
        """Should not investigate muted signatures."""
        sig = Signature(
            id="sig-001",
            fingerprint="fp-001",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            occurrence_count=100,
            status=SignatureStatus.MUTED,
        )
        assert not triage_engine.should_investigate(sig)

    def test_should_not_investigate_during_cooldown(
        self, triage_engine: TriageEngine
    ) -> None:
        """Should not investigate if within cooldown period."""
        now = datetime.now(UTC)
        diagnosis = Diagnosis(
            root_cause="root",
            evidence=(),
            suggested_fix="fix",
            confidence="high",
            diagnosed_at=now - timedelta(hours=12),  # Within 24-hour cooldown
            model="model",
            cost_usd=0.0,
        )
        sig = Signature(
            id="sig-001",
            fingerprint="fp-001",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=now,
            last_seen=now,
            occurrence_count=100,
            status=SignatureStatus.DIAGNOSED,
            diagnosis=diagnosis,
        )
        assert not triage_engine.should_investigate(sig)

    def test_should_investigate_after_cooldown_expires(
        self, triage_engine: TriageEngine
    ) -> None:
        """Should investigate if cooldown period has expired."""
        now = datetime.now(UTC)
        diagnosis = Diagnosis(
            root_cause="root",
            evidence=(),
            suggested_fix="fix",
            confidence="high",
            diagnosed_at=now - timedelta(hours=25),  # Beyond 24-hour cooldown
            model="model",
            cost_usd=0.0,
        )
        sig = Signature(
            id="sig-001",
            fingerprint="fp-001",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=now,
            last_seen=now,
            occurrence_count=100,
            status=SignatureStatus.DIAGNOSED,
            diagnosis=diagnosis,
        )
        assert triage_engine.should_investigate(sig)

    def test_should_notify_high_confidence(
        self, triage_engine: TriageEngine, signature: Signature, diagnosis: Diagnosis
    ) -> None:
        """Should always notify high confidence diagnoses."""
        assert triage_engine.should_notify(signature, diagnosis)

    def test_should_notify_medium_confidence_new_signature(
        self, triage_engine: TriageEngine, signature: Signature
    ) -> None:
        """Should notify medium confidence for new signatures."""
        # Create a new signature with NEW status to avoid mutating the fixture
        new_signature = Signature(
            id=signature.id,
            fingerprint=signature.fingerprint,
            error_type=signature.error_type,
            service=signature.service,
            message_template=signature.message_template,
            stack_hash=signature.stack_hash,
            first_seen=signature.first_seen,
            last_seen=signature.last_seen,
            occurrence_count=signature.occurrence_count,
            status=SignatureStatus.NEW,
        )
        diagnosis = Diagnosis(
            root_cause="root",
            evidence=(),
            suggested_fix="fix",
            confidence="medium",
            diagnosed_at=datetime.now(),
            model="model",
            cost_usd=0.0,
        )
        assert triage_engine.should_notify(new_signature, diagnosis)

    def test_should_not_notify_low_confidence(
        self, triage_engine: TriageEngine, signature: Signature
    ) -> None:
        """Should not notify low confidence diagnoses."""
        diagnosis = Diagnosis(
            root_cause="root",
            evidence=(),
            suggested_fix="fix",
            confidence="low",
            diagnosed_at=datetime.now(),
            model="model",
            cost_usd=0.0,
        )
        assert not triage_engine.should_notify(signature, diagnosis)

    def test_should_notify_critical_tag(
        self, triage_engine: TriageEngine, signature: Signature
    ) -> None:
        """Should notify signatures tagged as critical."""
        # Create a new signature with critical tag to avoid mutating the fixture
        critical_signature = Signature(
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
            tags=frozenset(["critical"]),
        )
        diagnosis = Diagnosis(
            root_cause="root",
            evidence=(),
            suggested_fix="fix",
            confidence="low",
            diagnosed_at=datetime.now(),
            model="model",
            cost_usd=0.0,
        )
        assert triage_engine.should_notify(critical_signature, diagnosis)

    def test_calculate_priority_frequency_component(
        self, triage_engine: TriageEngine
    ) -> None:
        """Priority should increase with occurrence count."""
        now = datetime.now(UTC)
        sig1 = Signature(
            id="sig-1",
            fingerprint="fp-1",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=now,
            last_seen=now,
            occurrence_count=10,
            status=SignatureStatus.NEW,
        )
        sig2 = Signature(
            id="sig-2",
            fingerprint="fp-2",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=now,
            last_seen=now,
            occurrence_count=50,
            status=SignatureStatus.NEW,
        )
        assert triage_engine.calculate_priority(
            sig2
        ) > triage_engine.calculate_priority(sig1)

    def test_calculate_priority_recency_component(
        self, triage_engine: TriageEngine
    ) -> None:
        """Priority should increase for recent signatures."""
        now = datetime.now(UTC)
        sig1 = Signature(
            id="sig-1",
            fingerprint="fp-1",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=now - timedelta(hours=2),
            last_seen=now - timedelta(hours=2),
            occurrence_count=10,
            status=SignatureStatus.NEW,
        )
        sig2 = Signature(
            id="sig-2",
            fingerprint="fp-2",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=now - timedelta(minutes=30),
            last_seen=now - timedelta(minutes=30),
            occurrence_count=10,
            status=SignatureStatus.NEW,
        )
        assert triage_engine.calculate_priority(
            sig2
        ) > triage_engine.calculate_priority(sig1)

    def test_calculate_priority_critical_tag_bonus(
        self, triage_engine: TriageEngine
    ) -> None:
        """Priority should increase with critical tag."""
        now = datetime.now(UTC)
        sig1 = Signature(
            id="sig-1",
            fingerprint="fp-1",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=now,
            last_seen=now,
            occurrence_count=10,
            status=SignatureStatus.NEW,
            tags=frozenset(),
        )
        sig2 = Signature(
            id="sig-2",
            fingerprint="fp-2",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=now,
            last_seen=now,
            occurrence_count=10,
            status=SignatureStatus.NEW,
            tags=frozenset(["critical"]),
        )
        assert triage_engine.calculate_priority(
            sig2
        ) > triage_engine.calculate_priority(sig1)


# ============================================================================
# PollService Tests
# ============================================================================


@pytest.mark.asyncio
class TestPollService:
    """Tests for the PollService."""

    async def test_poll_cycle_creates_new_signature(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        error_event: ErrorEvent,
    ) -> None:
        """Poll cycle should create a new signature for unknown error."""
        telemetry = FakeTelemetryPort()
        telemetry.add_error(error_event)
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        poll_service = PollService(
            telemetry, store, fingerprinter, triage_engine, investigator
        )

        result = await poll_service.execute_poll_cycle()

        assert result.errors_found == 1
        assert result.new_signatures == 1
        assert result.updated_signatures == 0
        assert len(store.signatures) == 1

    async def test_poll_cycle_updates_existing_signature(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        error_event: ErrorEvent,
        signature: Signature,
    ) -> None:
        """Poll cycle should update existing signature."""
        telemetry = FakeTelemetryPort()
        telemetry.add_error(error_event)
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        poll_service = PollService(
            telemetry, store, fingerprinter, triage_engine, investigator
        )

        # Pre-populate store with matching signature
        fp = fingerprinter.fingerprint(error_event)
        sig = Signature(
            id="sig-001",
            fingerprint=fp,
            error_type=error_event.error_type,
            service=error_event.service,
            message_template=fingerprinter.templatize_message(
                error_event.error_message
            ),
            stack_hash=fingerprinter.hash_stack(
                fingerprinter.normalize_stack(error_event.stack_frames)
            ),
            first_seen=error_event.timestamp,
            last_seen=error_event.timestamp,
            occurrence_count=1,
            status=SignatureStatus.NEW,
        )
        await store.save(sig)
        initial_count = sig.occurrence_count

        result = await poll_service.execute_poll_cycle()

        assert result.errors_found == 1
        assert result.new_signatures == 0
        assert result.updated_signatures == 1
        assert sig.occurrence_count == initial_count + 1

    async def test_poll_cycle_sets_max_severity_on_new_signature(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        error_event: ErrorEvent,
    ) -> None:
        """A newly created signature's max_severity should match the triggering error's severity."""
        telemetry = FakeTelemetryPort()
        telemetry.add_error(error_event)
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        poll_service = PollService(
            telemetry, store, fingerprinter, triage_engine, investigator
        )

        await poll_service.execute_poll_cycle()

        [sig] = store.signatures.values()
        assert sig.max_severity == error_event.severity

    async def test_poll_cycle_raises_max_severity_on_existing_signature(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        error_event: ErrorEvent,
    ) -> None:
        """Re-occurrence of a more severe error should raise the signature's max_severity."""
        fatal_event = ErrorEvent(
            trace_id=error_event.trace_id,
            span_id=error_event.span_id,
            service=error_event.service,
            error_type=error_event.error_type,
            error_message=error_event.error_message,
            stack_frames=error_event.stack_frames,
            timestamp=error_event.timestamp,
            attributes=dict(error_event.attributes),
            severity=Severity.FATAL,
        )
        telemetry = FakeTelemetryPort()
        telemetry.add_error(fatal_event)
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        poll_service = PollService(
            telemetry, store, fingerprinter, triage_engine, investigator
        )

        fp = fingerprinter.fingerprint(error_event)
        sig = Signature(
            id="sig-001",
            fingerprint=fp,
            error_type=error_event.error_type,
            service=error_event.service,
            message_template=fingerprinter.templatize_message(
                error_event.error_message
            ),
            stack_hash=fingerprinter.hash_stack(
                fingerprinter.normalize_stack(error_event.stack_frames)
            ),
            first_seen=error_event.timestamp,
            last_seen=error_event.timestamp,
            occurrence_count=1,
            status=SignatureStatus.NEW,
            max_severity=Severity.ERROR,
        )
        await store.save(sig)

        await poll_service.execute_poll_cycle()

        assert sig.max_severity == Severity.FATAL

    async def test_execute_resolution_cycle_with_no_diagnosed_signatures_resolves_nothing(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """execute_resolution_cycle() is a no-op when there are no DIAGNOSED signatures."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        poll_service = PollService(
            telemetry, store, fingerprinter, triage_engine, investigator
        )

        result = await poll_service.execute_resolution_cycle()

        assert result.signatures_resolved == 0

    async def test_investigation_cycle_calls_investigator(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        signature: Signature,
    ) -> None:
        """Investigation cycle should investigate pending signatures."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        poll_service = PollService(
            telemetry, store, fingerprinter, triage_engine, investigator
        )

        # Set signature to be pending
        signature.occurrence_count = 10
        signature.status = SignatureStatus.NEW
        store.pending_signatures = [signature]

        result = await poll_service.execute_investigation_cycle()

        assert len(result.diagnoses_produced) == 1
        assert result.investigations_attempted == 1
        assert result.investigations_failed == 0
        assert signature.status == SignatureStatus.DIAGNOSED
        assert signature.diagnosis is not None

    async def test_investigation_cycle_continues_after_one_fails(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """Investigation cycle should continue processing after one signature fails."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        notification = FakeNotificationPort()
        investigator_calls: list[str] = []

        # Create a diagnosis engine that fails on the first signature
        class PartiallyFailingDiagnosisPort(FakeDiagnosisPort):
            async def diagnose(self, context: InvestigationContext) -> Diagnosis:
                if len(investigator_calls) == 0:
                    investigator_calls.append("failed")
                    raise RuntimeError("Diagnosis failed for first signature")
                investigator_calls.append("succeeded")
                return await super().diagnose(context)

        diagnosis_engine = PartiallyFailingDiagnosisPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        poll_service = PollService(
            telemetry, store, fingerprinter, triage_engine, investigator
        )

        # Create two pending signatures
        now = datetime.now(UTC)
        sig1 = Signature(
            id="sig-1",
            fingerprint="fp-1",
            error_type="Error",
            service="service",
            message_template="msg1",
            stack_hash="hash1",
            first_seen=now,
            last_seen=now,
            occurrence_count=10,
            status=SignatureStatus.NEW,
        )
        sig2 = Signature(
            id="sig-2",
            fingerprint="fp-2",
            error_type="Error",
            service="service",
            message_template="msg2",
            stack_hash="hash2",
            first_seen=now,
            last_seen=now,
            occurrence_count=10,
            status=SignatureStatus.NEW,
        )
        store.pending_signatures = [sig1, sig2]

        # Execute investigation cycle
        result = await poll_service.execute_investigation_cycle()

        # Should have processed both signatures despite first failure
        # First signature should fail and revert to NEW
        # Second signature should succeed and be DIAGNOSED
        assert sig1.status == SignatureStatus.NEW  # Reverted due to diagnosis failure
        assert sig2.status == SignatureStatus.DIAGNOSED  # Successfully diagnosed
        assert len(result.diagnoses_produced) == 1  # Only successful diagnosis returned
        assert result.diagnoses_produced[0].root_cause == "Root cause for Error"
        assert result.investigations_attempted == 2
        assert result.investigations_failed == 1
        # Verify both signatures were attempted
        assert len(investigator_calls) == 2
        assert investigator_calls[0] == "failed"
        assert investigator_calls[1] == "succeeded"


class TestResolutionCycle:
    """Tests for PollService.execute_resolution_cycle() auto-close behavior."""

    @staticmethod
    def _diagnosed_signature(**overrides: Any) -> Signature:
        """Build a DIAGNOSED signature quiet since a fixed reference time."""
        defaults: dict[str, Any] = dict(
            id="sig-resolve-001",
            fingerprint="fp-resolve-001",
            error_type="ConnectionTimeoutError",
            service="payment-service",
            message_template="Failed to connect: timeout",
            stack_hash="hash-resolve-001",
            first_seen=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            last_seen=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            occurrence_count=5,
            status=SignatureStatus.DIAGNOSED,
        )
        defaults.update(overrides)
        return Signature(**defaults)

    async def test_default_threshold_resolves_signature_quiet_for_24_hours(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """A DIAGNOSED signature with no resolution_threshold_hours override
        resolves once quiet for the default 24-hour window."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry,
            store,
            fingerprinter,
            triage_engine,
            investigator,
            notification=notification,
            resolution_threshold_hours_default=24,
        )

        sig = self._diagnosed_signature(
            last_seen=datetime.now(UTC) - timedelta(hours=25)
        )
        await store.save(sig)

        result = await poll_service.execute_resolution_cycle()

        assert result.signatures_resolved == 1
        assert sig.status == SignatureStatus.RESOLVED
        assert notification.close_resolved_issue_call_count == 1
        assert notification.closed_resolved_issues[0].id == sig.id

    async def test_default_threshold_leaves_recently_seen_signature_alone(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """A DIAGNOSED signature still within the default window is left untouched."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry,
            store,
            fingerprinter,
            triage_engine,
            investigator,
            notification=notification,
            resolution_threshold_hours_default=24,
        )

        sig = self._diagnosed_signature(
            last_seen=datetime.now(UTC) - timedelta(hours=1)
        )
        await store.save(sig)

        result = await poll_service.execute_resolution_cycle()

        assert result.signatures_resolved == 0
        assert sig.status == SignatureStatus.DIAGNOSED
        assert notification.close_resolved_issue_call_count == 0

    async def test_custom_threshold_overrides_default(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """A signature's own resolution_threshold_hours takes precedence over
        the service-wide default, per Diagnosis.suggested_resolution_hours."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry,
            store,
            fingerprinter,
            triage_engine,
            investigator,
            notification=notification,
            resolution_threshold_hours_default=24,
        )

        # Quiet for 8 hours: would NOT resolve under the 24h default, but
        # DOES resolve under this signature's custom 6h threshold.
        sig = self._diagnosed_signature(
            last_seen=datetime.now(UTC) - timedelta(hours=8),
            resolution_threshold_hours=6,
        )
        await store.save(sig)

        result = await poll_service.execute_resolution_cycle()

        assert result.signatures_resolved == 1
        assert sig.status == SignatureStatus.RESOLVED

    async def test_custom_threshold_not_yet_elapsed_leaves_signature_alone(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """A custom threshold that hasn't elapsed yet keeps the signature DIAGNOSED,
        even though the (shorter) service default would have resolved it."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry,
            store,
            fingerprinter,
            triage_engine,
            investigator,
            notification=notification,
            resolution_threshold_hours_default=6,
        )

        sig = self._diagnosed_signature(
            last_seen=datetime.now(UTC) - timedelta(hours=8),
            resolution_threshold_hours=48,
        )
        await store.save(sig)

        result = await poll_service.execute_resolution_cycle()

        assert result.signatures_resolved == 0
        assert sig.status == SignatureStatus.DIAGNOSED

    async def test_resolution_cycle_without_notification_port_still_resolves(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """No notification port configured -> signature still resolves, just no issue-close call."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry, store, fingerprinter, triage_engine, investigator
        )

        sig = self._diagnosed_signature(
            last_seen=datetime.now(UTC) - timedelta(hours=25)
        )
        await store.save(sig)

        result = await poll_service.execute_resolution_cycle()

        assert result.signatures_resolved == 1
        assert sig.status == SignatureStatus.RESOLVED

    async def test_notification_close_failure_does_not_revert_resolution(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """A failure closing the issue is logged but the signature stays RESOLVED."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        notification.set_should_fail(True, "GitHub unavailable")
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry,
            store,
            fingerprinter,
            triage_engine,
            investigator,
            notification=notification,
        )

        sig = self._diagnosed_signature(
            last_seen=datetime.now(UTC) - timedelta(hours=25)
        )
        await store.save(sig)

        result = await poll_service.execute_resolution_cycle()

        assert result.signatures_resolved == 1
        assert sig.status == SignatureStatus.RESOLVED

    async def test_notification_close_failure_tags_signature_for_retry(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """A close failure is surfaced in the result and tagged for retry
        instead of silently leaving the issue dangling."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        notification.set_should_fail(True, "GitHub unavailable")
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry,
            store,
            fingerprinter,
            triage_engine,
            investigator,
            notification=notification,
        )

        sig = self._diagnosed_signature(
            last_seen=datetime.now(UTC) - timedelta(hours=25)
        )
        await store.save(sig)

        result = await poll_service.execute_resolution_cycle()

        assert result.signatures_resolved == 1
        assert result.issue_close_failures == 1
        assert sig.status == SignatureStatus.RESOLVED
        assert "issue-close-pending" in sig.tags

    async def test_pending_issue_close_is_retried_and_cleared_on_success(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """A signature tagged from a prior close failure is retried on the
        next resolution cycle and un-tagged once the retry succeeds."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry,
            store,
            fingerprinter,
            triage_engine,
            investigator,
            notification=notification,
        )

        sig = self._diagnosed_signature(
            last_seen=datetime.now(UTC) - timedelta(hours=25),
            status=SignatureStatus.RESOLVED,
            tags=frozenset({"issue-close-pending"}),
        )
        await store.save(sig)

        result = await poll_service.execute_resolution_cycle()

        assert result.signatures_resolved == 0
        assert result.issue_close_failures == 0
        assert notification.close_resolved_issue_call_count == 1
        assert "issue-close-pending" not in sig.tags

    async def test_store_update_failure_after_successful_write_keeps_resolution(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """If store.update() raises after its write already committed (e.g. a
        timeout), the signature must not be rolled back to DIAGNOSED - doing
        so would clobber a successful write and cause a double-resolve."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry,
            store,
            fingerprinter,
            triage_engine,
            investigator,
            notification=notification,
        )

        sig = self._diagnosed_signature(
            last_seen=datetime.now(UTC) - timedelta(hours=25)
        )
        await store.save(sig)
        store.update_fail_count = 1
        store.update_fail_after_write = True

        result = await poll_service.execute_resolution_cycle()

        assert result.signatures_resolved == 1
        assert sig.status == SignatureStatus.RESOLVED
        persisted = await store.get_by_id(sig.id)
        assert persisted is not None
        assert persisted.status == SignatureStatus.RESOLVED
        assert notification.close_resolved_issue_call_count == 1

    async def test_signature_recurrence_after_auto_resolution_returns_to_new(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        error_event: ErrorEvent,
    ) -> None:
        """FR16: once a resolved signature's error recurs, the next poll cycle
        occurrence brings it back to NEW rather than leaving it RESOLVED
        (which would otherwise silently swallow the recurrence)."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry,
            store,
            fingerprinter,
            triage_engine,
            investigator,
            notification=notification,
        )

        # Build the signature from an actual fingerprinted error, as the poll
        # cycle would, so a later occurrence of the same error dedups onto it.
        fp = fingerprinter.fingerprint(error_event)
        aged_timestamp = datetime.now(UTC) - timedelta(hours=25)
        sig = Signature(
            id="sig-recur-001",
            fingerprint=fp,
            error_type=error_event.error_type,
            service=error_event.service,
            message_template=fingerprinter.templatize_message(error_event.error_message),
            stack_hash=fingerprinter.hash_stack(
                fingerprinter.normalize_stack(error_event.stack_frames)
            ),
            first_seen=aged_timestamp,
            last_seen=aged_timestamp,
            occurrence_count=5,
            status=SignatureStatus.DIAGNOSED,
        )
        await store.save(sig)

        await poll_service.execute_resolution_cycle()
        resolved = await store.get_by_id(sig.id)
        assert resolved is not None
        assert resolved.status == SignatureStatus.RESOLVED

        # The error recurs: the same fingerprinted error comes through a new poll cycle.
        telemetry.add_error(error_event)
        poll_result = await poll_service.execute_poll_cycle()

        assert poll_result.updated_signatures == 1
        recurred = await store.get_by_id(sig.id)
        assert recurred is not None
        assert recurred.status == SignatureStatus.NEW

    async def test_store_update_failure_reverts_in_memory_status(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """If store.update() genuinely never commits, the in-memory signature
        must revert to DIAGNOSED rather than staying RESOLVED while the store
        still has it as DIAGNOSED - otherwise the signature would become
        invisible to get_all(status=DIAGNOSED) for the rest of the process
        lifetime.

        get_by_id() is backed by a snapshot taken at save() time, independent
        of the live object handed to update() - matching how a real store's
        persisted row is decoupled from the caller's in-memory mutations
        until a write actually commits.
        """

        class StoreFailingOnResolve(FakeSignatureStorePort):
            """Fails the update() call that persists the resolution."""

            def __init__(self) -> None:
                super().__init__()
                self._committed: dict[str, Signature] = {}

            async def save(self, signature: Signature) -> None:
                await super().save(signature)
                self._committed[signature.id] = copy.copy(signature)

            async def update(self, signature: Signature) -> None:
                raise RuntimeError("Store unavailable during resolve")

            async def get_by_id(self, signature_id: str) -> Signature | None:
                return self._committed.get(signature_id)

        telemetry = FakeTelemetryPort()
        store = StoreFailingOnResolve()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry, store, fingerprinter, triage_engine, investigator
        )

        sig = self._diagnosed_signature(
            last_seen=datetime.now(UTC) - timedelta(hours=25)
        )
        await store.save(sig)

        result = await poll_service.execute_resolution_cycle()

        assert result.signatures_resolved == 0
        assert sig.status == SignatureStatus.DIAGNOSED

    async def test_verification_failure_after_resolve_failure_reverts_safely(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """If the post-failure verification read (get_by_id) also fails, the
        cycle must not raise - it logs and falls back to reverting the
        in-memory signature to DIAGNOSED, the safe default when the store's
        true state can't be confirmed."""

        class StoreFailingOnResolveAndVerify(FakeSignatureStorePort):
            """Fails both the resolving update() and the verification read."""

            async def update(self, signature: Signature) -> None:
                raise RuntimeError("Store unavailable")

            async def get_by_id(self, signature_id: str) -> Signature | None:
                raise RuntimeError("Store unavailable for verification")

        telemetry = FakeTelemetryPort()
        store = StoreFailingOnResolveAndVerify()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry, store, fingerprinter, triage_engine, investigator
        )

        sig = self._diagnosed_signature(
            last_seen=datetime.now(UTC) - timedelta(hours=25)
        )
        await store.save(sig)

        result = await poll_service.execute_resolution_cycle()

        assert result.signatures_resolved == 0
        assert sig.status == SignatureStatus.DIAGNOSED


# ============================================================================
# Critical Bug Fixes Tests
# ============================================================================


class TestDatetimeAwareness:
    """Tests for fixing naive/aware datetime mixing."""

    def test_triage_uses_aware_datetimes(self, triage_engine: TriageEngine) -> None:
        """TriageEngine should use timezone-aware datetimes."""
        now = datetime.now(UTC)
        diagnosis = Diagnosis(
            root_cause="root",
            evidence=(),
            suggested_fix="fix",
            confidence="high",
            diagnosed_at=now - timedelta(hours=12),
            model="model",
            cost_usd=0.0,
        )
        sig = Signature(
            id="sig-001",
            fingerprint="fp-001",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=now,
            last_seen=now,
            occurrence_count=100,
            status=SignatureStatus.DIAGNOSED,
            diagnosis=diagnosis,
        )
        # Should not raise TypeError about comparing naive and aware datetimes
        assert not triage_engine.should_investigate(sig)

    @pytest.mark.asyncio
    async def test_poll_uses_aware_datetimes(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        error_event: ErrorEvent,
    ) -> None:
        """PollService should use timezone-aware datetimes."""
        telemetry = FakeTelemetryPort()
        telemetry.add_error(error_event)
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        poll_service = PollService(
            telemetry, store, fingerprinter, triage_engine, investigator
        )

        # Should not raise TypeError
        result = await poll_service.execute_poll_cycle()
        assert isinstance(result, PollResult)

    @pytest.mark.asyncio
    async def test_poll_batch_size_limiting(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """Test that poll service respects batch_size limit."""
        # Create 10 errors
        errors = [
            ErrorEvent(
                trace_id=f"trace-{i}",
                span_id=f"span-{i}",
                service="service",
                error_type="TestError",
                error_message=f"Error {i}",
                stack_frames=(),
                timestamp=datetime.now(UTC),
                attributes={},
                severity=Severity.ERROR,
            )
            for i in range(10)
        ]

        telemetry = FakeTelemetryPort()
        telemetry.add_errors(errors)
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        # Create poll service with batch_size of 5
        poll_service = PollService(
            telemetry,
            store,
            fingerprinter,
            triage_engine,
            investigator,
            batch_size=5,
        )

        # Execute poll cycle - should only process first 5 errors due to batch size limit
        result = await poll_service.execute_poll_cycle()
        assert (
            result.errors_found == 5
        )  # Only 5 errors processed due to batch size limit
        assert (
            result.new_signatures + result.updated_signatures
        ) == 5  # All processed are new


@pytest.mark.asyncio
class TestPollCycleErrorHandling:
    """Tests for comprehensive error handling in poll cycle."""

    async def test_poll_continues_after_individual_error(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
    ) -> None:
        """Poll cycle should continue processing after individual error processing fails."""

        class PartiallyBrokenFingerprinter(Fingerprinter):
            def __init__(self, broken_on_count: int = 2) -> None:
                super().__init__()
                self.call_count = 0
                self.broken_on_count = broken_on_count

            def fingerprint(self, error: ErrorEvent) -> str:
                self.call_count += 1
                if self.call_count == self.broken_on_count:
                    raise RuntimeError("Fingerprinter broke")
                return super().fingerprint(error)

        error1 = ErrorEvent(
            trace_id="trace-1",
            span_id="span-1",
            service="service",
            error_type="Error1",
            error_message="Error 1",
            stack_frames=(),
            timestamp=datetime.now(UTC),
            attributes={},
            severity=Severity.ERROR,
        )
        error2 = ErrorEvent(
            trace_id="trace-2",
            span_id="span-2",
            service="service",
            error_type="Error2",
            error_message="Error 2",
            stack_frames=(),
            timestamp=datetime.now(UTC),
            attributes={},
            severity=Severity.ERROR,
        )

        telemetry = FakeTelemetryPort()
        telemetry.add_errors([error1, error2])
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        fingerprinter = PartiallyBrokenFingerprinter(broken_on_count=2)
        poll_service = PollService(
            telemetry, store, fingerprinter, triage_engine, investigator
        )

        # Should process 2 errors but skip 1 due to fingerprinter failure
        result = await poll_service.execute_poll_cycle()
        assert result.errors_found == 2
        # Only 1 should be successfully processed (the first one, second will fail)
        assert (result.new_signatures + result.updated_signatures) >= 1


class TestDiagnosisParsingValidation:
    """Tests for strict diagnosis parsing that raises on errors."""

    def test_diagnosis_parser_requires_root_cause(
        self, triage_engine: TriageEngine
    ) -> None:
        """Diagnosis parser should raise if root_cause is missing."""
        from rounds.adapters.diagnosis._parsing import parse_diagnosis_result

        # Result missing root_cause
        result = {
            "evidence": ["test"],
            "suggested_fix": "fix",
            "confidence": "HIGH",
        }

        with pytest.raises(ValueError, match="root_cause"):
            parse_diagnosis_result(result, model="claude-opus")

    def test_diagnosis_parser_requires_valid_confidence(
        self, triage_engine: TriageEngine
    ) -> None:
        """Diagnosis parser should raise if confidence is invalid."""
        from rounds.adapters.diagnosis._parsing import parse_diagnosis_result

        # Result with invalid confidence
        result = {
            "root_cause": "root",
            "evidence": ["test"],
            "suggested_fix": "fix",
            "confidence": "INVALID",
        }

        with pytest.raises(ValueError, match="Invalid confidence"):
            parse_diagnosis_result(result, model="claude-opus")


@pytest.mark.asyncio
class TestPartialTraceRetrieval:
    """Tests for handling partial trace retrieval failures."""

    async def test_investigator_detects_incomplete_traces(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        signature: Signature,
    ) -> None:
        """Investigator should log when trace retrieval is incomplete."""
        # Create 5 events but only successfully fetch 3 traces
        events = [
            ErrorEvent(
                trace_id=f"trace-{i}",
                span_id=f"span-{i}",
                service="service",
                error_type="Error",
                error_message="Error",
                stack_frames=(),
                timestamp=datetime.now(UTC),
                attributes={},
                severity=Severity.ERROR,
            )
            for i in range(5)
        ]

        class PartialTelemetryForInvestigator(PartialTraceTelemetryPort):
            async def get_events_for_signature(
                self, fingerprint: str, limit: int = 5
            ) -> list[ErrorEvent]:
                return events

        telemetry = PartialTelemetryForInvestigator(fail_trace_count=2)
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        diagnosis = await investigator.investigate(signature)

        # Should still return a diagnosis even with partial trace data
        assert diagnosis is not None
        assert signature.status == SignatureStatus.DIAGNOSED


@pytest.mark.asyncio
class TestSuggestedResolutionHoursPropagation:
    """Tests that Diagnosis.suggested_resolution_hours propagates to Signature."""

    async def test_investigate_applies_suggested_resolution_hours(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        signature: Signature,
    ) -> None:
        """Investigator should copy a non-None suggestion onto the signature."""
        signature.occurrence_count = 10
        signature.status = SignatureStatus.NEW

        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        diagnosis_engine.set_default_diagnosis(
            Diagnosis(
                root_cause="Transient network blip",
                evidence=("Single occurrence, resolved on retry",),
                suggested_fix="No action required",
                confidence="high",
                diagnosed_at=datetime.now(UTC),
                model="claude-code",
                cost_usd=0.01,
                suggested_resolution_hours=6,
            )
        )
        notification = FakeNotificationPort()

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        await investigator.investigate(signature)

        assert signature.resolution_threshold_hours == 6

    async def test_investigate_leaves_resolution_threshold_unset_when_suggestion_is_none(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        signature: Signature,
    ) -> None:
        """Investigator should not touch resolution_threshold_hours when the diagnosis is uncertain."""
        signature.occurrence_count = 10
        signature.status = SignatureStatus.NEW

        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()  # canned response leaves suggested_resolution_hours=None
        notification = FakeNotificationPort()

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        await investigator.investigate(signature)

        assert signature.resolution_threshold_hours is None


class TestNotificationFailureHandling:
    """Tests for notification failure not reverting successful diagnosis."""

    async def test_notification_failure_preserves_diagnosis(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        signature: Signature,
    ) -> None:
        """If notification fails, diagnosis should stay persisted."""
        # Create a signature that will be diagnosed
        signature.occurrence_count = 10
        signature.status = SignatureStatus.NEW

        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FailingNotificationPort()

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        # Despite notification failure, diagnosis should be recorded
        # But the notification error should be exposed to the caller
        with pytest.raises(RuntimeError, match="Notification service is unavailable"):
            await investigator.investigate(signature)

        # Status should be DIAGNOSED (not reverted to NEW)
        assert signature.status == SignatureStatus.DIAGNOSED
        assert signature.diagnosis is not None

    async def test_diagnosis_persisted_before_notification(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        signature: Signature,
    ) -> None:
        """Diagnosis must be persisted before attempting notification."""
        signature.occurrence_count = 10
        signature.status = SignatureStatus.NEW

        class TrackingStore(FakeSignatureStorePort):
            def __init__(self) -> None:
                super().__init__()
                self.update_calls: list[tuple[str, SignatureStatus, Diagnosis | None]] = []

            async def get_by_id(self, signature_id: str) -> Signature | None:
                """Mock implementation."""
                for sig in self.signatures.values():
                    if sig.id == signature_id:
                        return sig
                return None

            async def get_all(
                self, status: SignatureStatus | None = None
            ) -> list[Signature]:
                """Mock implementation."""
                return await super().get_all(status)

            async def update(self, sig: Signature) -> None:
                self.update_calls.append((sig.fingerprint, sig.status, sig.diagnosis))
                await super().update(sig)

        telemetry = FakeTelemetryPort()
        store = TrackingStore()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FailingNotificationPort()

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        # Investigate - notification will fail and error should be exposed
        with pytest.raises(RuntimeError, match="Notification service is unavailable"):
            await investigator.investigate(signature)

        # Check that diagnosis was persisted before notification was attempted
        # Should have two updates: INVESTIGATING, then DIAGNOSED
        assert len(store.update_calls) >= 2
        # Last update should show DIAGNOSED status with diagnosis set
        _last_fingerprint, last_status, last_diagnosis = store.update_calls[-1]
        assert last_status == SignatureStatus.DIAGNOSED
        assert last_diagnosis is not None

    @pytest.mark.asyncio
    async def test_investigator_raises_on_store_update_failure_during_diagnosis_persistence(
        self, signature: Signature, triage_engine: TriageEngine
    ) -> None:
        """Test that investigator raises when store update fails after diagnosis."""

        # Custom store that fails on the second update (diagnosis persistence)
        class FailingStoreOnDiagnosisPersistence(FakeSignatureStorePort):
            def __init__(self) -> None:
                super().__init__()
                self.update_count = 0

            async def update(self, sig: Signature) -> None:
                self.update_count += 1
                # Fail on second update (diagnosis persistence)
                if self.update_count == 2:
                    raise Exception(
                        "Database connection failed during diagnosis persistence"
                    )
                await super().update(sig)

        telemetry = FakeTelemetryPort()
        store = FailingStoreOnDiagnosisPersistence()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        # Investigation should raise the store error
        with pytest.raises(Exception, match="Database connection failed"):
            await investigator.investigate(signature)


class TestAlertCooldownPersistence:
    """The domain service layer (not the notification adapter) owns recording
    and persisting Signature.last_alerted_at (see NotificationPort.report())."""

    @pytest.mark.asyncio
    async def test_investigate_persists_alert_cooldown_when_channel_reports_one(
        self, signature: Signature, triage_engine: TriageEngine
    ) -> None:
        """When report() returns a timestamp, the signature is updated and persisted."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        alerted_at = datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC)
        notification.report_alerted_at = alerted_at

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        await investigator.investigate(signature)

        assert signature.last_alerted_at == alerted_at
        assert store.updated_signatures[-1].last_alerted_at == alerted_at

    @pytest.mark.asyncio
    async def test_investigate_does_not_persist_cooldown_when_channel_reports_none(
        self, signature: Signature, triage_engine: TriageEngine
    ) -> None:
        """Channels with no cooldown concept (e.g. GitHub) leave last_alerted_at untouched."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()  # report_alerted_at defaults to None

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        await investigator.investigate(signature)

        assert signature.last_alerted_at is None

    @pytest.mark.asyncio
    async def test_cooldown_persist_retries_on_transient_store_failure(
        self, signature: Signature, triage_engine: TriageEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transient store failure while persisting the cooldown is retried, not fatal."""
        from rounds.core import investigator as investigator_module

        monkeypatch.setattr(
            investigator_module, "_ALERT_COOLDOWN_PERSIST_RETRY_DELAY_SECONDS", 0
        )

        class FlakyOnCooldownPersistStore(FakeSignatureStorePort):
            """Fails only on the third update() call (the cooldown persist attempt)."""

            def __init__(self) -> None:
                super().__init__()
                self.update_count = 0

            async def update(self, sig: Signature) -> None:
                self.update_count += 1
                if self.update_count == 3:
                    raise RuntimeError("Simulated transient store failure")
                await super().update(sig)

        telemetry = FakeTelemetryPort()
        store = FlakyOnCooldownPersistStore()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        alerted_at = datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC)
        notification.report_alerted_at = alerted_at

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        await investigator.investigate(signature)

        assert signature.last_alerted_at == alerted_at
        # investigating, diagnosed, failed cooldown attempt, retried cooldown attempt
        assert store.update_count == 4

    @pytest.mark.asyncio
    async def test_cooldown_persist_failure_does_not_raise_after_alert_sent(
        self, signature: Signature, triage_engine: TriageEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If every retry fails, investigate() still returns normally: the alert
        was already sent, and a lost cooldown write only risks a duplicate alert
        next cycle, which is the documented worst case."""
        from rounds.core import investigator as investigator_module

        monkeypatch.setattr(
            investigator_module, "_ALERT_COOLDOWN_PERSIST_RETRY_DELAY_SECONDS", 0
        )

        class AlwaysFailingCooldownPersistStore(FakeSignatureStorePort):
            """Fails on every update() call from the cooldown persist attempt onward."""

            def __init__(self) -> None:
                super().__init__()
                self.update_count = 0

            async def update(self, sig: Signature) -> None:
                self.update_count += 1
                if self.update_count >= 3:
                    raise RuntimeError("Simulated persistent store failure")
                await super().update(sig)

        telemetry = FakeTelemetryPort()
        store = AlwaysFailingCooldownPersistStore()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        alerted_at = datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC)
        notification.report_alerted_at = alerted_at

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        # Should not raise, despite the cooldown persist never succeeding.
        await investigator.investigate(signature)

        assert signature.last_alerted_at == alerted_at
        # investigating, diagnosed, then 3 failed cooldown attempts (exhausted)
        assert store.update_count == 5
        # Only the pre-notification updates (investigating, diagnosed) ever committed.
        assert len(store.updated_signatures) == 2


@pytest.mark.asyncio
class TestBudgetTracking:
    """Tests that Investigator and PollService record costs against a BudgetTracker.

    Guards against silent breakage of the budget safety feature: if the
    record_cost call signature or step names change, these tests should fail.
    """

    async def test_investigate_records_diagnose_and_confirm_costs(
        self,
        triage_engine: TriageEngine,
        signature: Signature,
    ) -> None:
        """investigate() should record "diagnose" cost from the diagnosis and a "confirm" cost."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        diagnosis_engine.set_default_cost(0.42)
        notification = FakeNotificationPort()
        budget_tracker = FakeBudgetTracker()

        investigator = Investigator(
            telemetry,
            store,
            diagnosis_engine,
            notification,
            triage_engine,
            "/app",
            budget_tracker=budget_tracker,
        )

        await investigator.investigate(signature)

        assert budget_tracker.recorded_costs == [
            ("diagnose", 0.42),
            ("confirm", 0.0),
        ]

    async def test_investigate_without_budget_tracker_does_not_raise(
        self,
        triage_engine: TriageEngine,
        signature: Signature,
    ) -> None:
        """investigate() should work fine when no budget_tracker is configured."""
        telemetry = FakeTelemetryPort()
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        diagnosis = await investigator.investigate(signature)
        assert diagnosis is not None

    async def test_poll_cycle_records_poll_and_fingerprint_costs(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        error_event: ErrorEvent,
    ) -> None:
        """execute_poll_cycle() should record "poll" and "fingerprint" costs."""
        telemetry = FakeTelemetryPort()
        telemetry.add_error(error_event)
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        budget_tracker = FakeBudgetTracker()

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry,
            store,
            fingerprinter,
            triage_engine,
            investigator,
            budget_tracker=budget_tracker,
        )

        await poll_service.execute_poll_cycle()

        assert budget_tracker.recorded_costs == [
            ("poll", 0.0),
            ("fingerprint", 0.0),
        ]

    async def test_poll_cycle_without_budget_tracker_does_not_raise(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        error_event: ErrorEvent,
    ) -> None:
        """execute_poll_cycle() should work fine when no budget_tracker is configured."""
        telemetry = FakeTelemetryPort()
        telemetry.add_error(error_event)
        store = FakeSignatureStorePort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )
        poll_service = PollService(
            telemetry, store, fingerprinter, triage_engine, investigator
        )

        result = await poll_service.execute_poll_cycle()
        assert result.errors_found == 1


@pytest.mark.asyncio
class TestManagementService:
    """Tests for ManagementService operations."""

    @pytest.mark.asyncio
    async def test_retriage_signature_from_diagnosed_status(
        self,
        diagnosis: Diagnosis,
    ) -> None:
        """Test that retriage_signature resets a DIAGNOSED signature to NEW status."""
        # Create a diagnosed signature
        signature = Signature(
            id="sig-001",
            fingerprint="fp-001",
            error_type="ConnectionTimeout",
            service="api",
            message_template="Connection timeout",
            stack_hash="stack-hash-001",
            first_seen=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            last_seen=datetime(2024, 1, 1, 12, 5, 0, tzinfo=UTC),
            occurrence_count=5,
            status=SignatureStatus.DIAGNOSED,
            diagnosis=diagnosis,
        )

        # Create management service with fakes
        store = FakeSignatureStorePort()
        await store.save(signature)

        telemetry = FakeTelemetryPort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        triage = TriageEngine()

        management_service = ManagementService(
            store=store,
            telemetry=telemetry,
            diagnosis_engine=diagnosis_engine,
            notification=notification,
            triage=triage,
            codebase_path=".",
        )

        # Retriage the signature
        await management_service.retriage_signature(signature.id)

        # Verify the signature is now NEW and diagnosis is cleared
        updated_signature = await store.get_by_id(signature.id)
        assert updated_signature is not None
        assert updated_signature.status == SignatureStatus.NEW
        assert updated_signature.diagnosis is None

    @pytest.mark.asyncio
    async def test_retriage_signature_not_found(self) -> None:
        """Test that retriage_signature raises ValueError for non-existent signature."""
        store = FakeSignatureStorePort()
        telemetry = FakeTelemetryPort()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FakeNotificationPort()
        triage = TriageEngine()

        management_service = ManagementService(
            store=store,
            telemetry=telemetry,
            diagnosis_engine=diagnosis_engine,
            notification=notification,
            triage=triage,
            codebase_path=".",
        )

        # Attempt to retriage non-existent signature
        with pytest.raises(ValueError, match="not found"):
            await management_service.retriage_signature("nonexistent-id")

    @pytest.mark.asyncio
    async def test_reinvestigate_restores_state_on_diagnosis_failure(
        self,
        diagnosis: Diagnosis,
    ) -> None:
        """reinvestigate() must restore original status and diagnosis when diagnosis fails."""
        original_diagnosis = diagnosis
        signature = Signature(
            id="sig-001",
            fingerprint="fp-001",
            error_type="ConnectionTimeout",
            service="api",
            message_template="Connection timeout",
            stack_hash="stack-hash-001",
            first_seen=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            last_seen=datetime(2024, 1, 1, 12, 5, 0, tzinfo=UTC),
            occurrence_count=5,
            status=SignatureStatus.DIAGNOSED,
            diagnosis=original_diagnosis,
        )

        store = FakeSignatureStorePort()
        await store.save(signature)

        telemetry = FakeTelemetryPort()
        diagnosis_engine = FakeDiagnosisPort()
        diagnosis_engine.set_should_fail(True, "LLM temporarily unavailable")
        notification = FakeNotificationPort()
        triage = TriageEngine()

        management_service = ManagementService(
            store=store,
            telemetry=telemetry,
            diagnosis_engine=diagnosis_engine,
            notification=notification,
            triage=triage,
            codebase_path=".",
        )

        with pytest.raises(RuntimeError, match="LLM temporarily unavailable"):
            await management_service.reinvestigate(signature.id)

        # Original state must be restored after diagnosis failure
        restored = await store.get_by_id(signature.id)
        assert restored is not None
        assert restored.status == SignatureStatus.DIAGNOSED
        assert restored.diagnosis == original_diagnosis

    @pytest.mark.asyncio
    async def test_reinvestigate_restores_state_when_store_also_fails(
        self,
        diagnosis: Diagnosis,
    ) -> None:
        """reinvestigate() must re-raise the diagnosis error even if the restore store.update() also fails."""
        original_diagnosis = diagnosis
        signature = Signature(
            id="sig-002",
            fingerprint="fp-002",
            error_type="TimeoutError",
            service="worker",
            message_template="Worker timeout",
            stack_hash="stack-hash-002",
            first_seen=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            last_seen=datetime(2024, 1, 1, 12, 5, 0, tzinfo=UTC),
            occurrence_count=3,
            status=SignatureStatus.DIAGNOSED,
            diagnosis=original_diagnosis,
        )

        class StoreFailingOnRestore(FakeSignatureStorePort):
            """Fails on the second update (the restore call after diagnosis failure)."""

            def __init__(self) -> None:
                super().__init__()
                self.update_count = 0

            async def update(self, sig: Signature) -> None:
                self.update_count += 1
                if self.update_count == 2:
                    raise RuntimeError("Store unavailable during restore")
                await super().update(sig)

        store = StoreFailingOnRestore()
        await store.save(signature)

        telemetry = FakeTelemetryPort()
        diagnosis_engine = FakeDiagnosisPort()
        diagnosis_engine.set_should_fail(True, "LLM temporarily unavailable")
        notification = FakeNotificationPort()
        triage = TriageEngine()

        management_service = ManagementService(
            store=store,
            telemetry=telemetry,
            diagnosis_engine=diagnosis_engine,
            notification=notification,
            triage=triage,
            codebase_path=".",
        )

        # The original diagnosis error must be re-raised even when the restore store
        # update also fails (the store error is logged and swallowed).
        with pytest.raises(RuntimeError, match="LLM temporarily unavailable"):
            await management_service.reinvestigate(signature.id)
