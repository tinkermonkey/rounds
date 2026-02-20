"""Unit tests for core domain services.

Tests verify that Fingerprinter, TriageEngine, Investigator, and PollService
implement the core diagnostic logic correctly.
"""

from datetime import UTC, datetime, timedelta

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

    def __init__(self, fail_trace_count: int = 2):
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
                module="app.service", function="process", filename="service.py", lineno=42
            ),
            StackFrame(
                module="app.db", function="query", filename="db.py", lineno=15
            ),
        )

        normalized = fingerprinter.normalize_stack(frames)

        assert len(normalized) == 2
        assert normalized[0].lineno is None
        assert normalized[1].lineno is None
        assert normalized[0].module == "app.service"
        assert normalized[1].function == "query"

    def test_templatize_message_replaces_ips(self, fingerprinter: Fingerprinter) -> None:
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

    def test_templatize_message_replaces_ids(self, fingerprinter: Fingerprinter) -> None:
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

    def test_templatize_message_replaces_uuids(self, fingerprinter: Fingerprinter) -> None:
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
        with pytest.raises(ValueError, match="min_occurrence_for_investigation must be positive"):
            TriageEngine(
                min_occurrence_for_investigation=0,
                investigation_cooldown_hours=1,
            )

        with pytest.raises(ValueError, match="min_occurrence_for_investigation must be positive"):
            TriageEngine(
                min_occurrence_for_investigation=-1,
                investigation_cooldown_hours=1,
            )

    def test_constructor_validates_cooldown(self) -> None:
        """Test that TriageEngine rejects investigation_cooldown_hours <= 0."""
        with pytest.raises(ValueError, match="investigation_cooldown_hours must be positive"):
            TriageEngine(
                min_occurrence_for_investigation=1,
                investigation_cooldown_hours=0,
            )

        with pytest.raises(ValueError, match="investigation_cooldown_hours must be positive"):
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
        assert triage_engine.calculate_priority(sig2) > triage_engine.calculate_priority(
            sig1
        )

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
        assert triage_engine.calculate_priority(sig2) > triage_engine.calculate_priority(
            sig1
        )

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
        assert triage_engine.calculate_priority(sig2) > triage_engine.calculate_priority(
            sig1
        )


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
            async def diagnose(self, context):
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
        assert result.errors_found == 5  # Only 5 errors processed due to batch size limit
        assert (result.new_signatures + result.updated_signatures) == 5  # All processed are new


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
            def __init__(self, broken_on_count: int = 2):
                super().__init__()
                self.call_count = 0
                self.broken_on_count = broken_on_count

            def fingerprint(self, error):
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
        from rounds.adapters.diagnosis.claude_code import ClaudeCodeDiagnosisAdapter

        adapter = ClaudeCodeDiagnosisAdapter()

        # Create a mock context (not used in parsing)
        sig = Signature(
            id="sig",
            fingerprint="fp",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=datetime.now(UTC),
            last_seen=datetime.now(UTC),
            occurrence_count=1,
            status=SignatureStatus.NEW,
        )
        context = InvestigationContext(
            signature=sig,
            recent_events=(),
            trace_data=(),
            related_logs=(),
            codebase_path="/app",
            historical_context=(),
        )

        # Result missing root_cause
        result = {
            "evidence": ["test"],
            "suggested_fix": "fix",
            "confidence": "HIGH",
        }

        with pytest.raises(ValueError, match="root_cause"):
            adapter._parse_diagnosis_result(result, context)

    def test_diagnosis_parser_requires_valid_confidence(
        self, triage_engine: TriageEngine
    ) -> None:
        """Diagnosis parser should raise if confidence is invalid."""
        from rounds.adapters.diagnosis.claude_code import ClaudeCodeDiagnosisAdapter

        adapter = ClaudeCodeDiagnosisAdapter()

        sig = Signature(
            id="sig",
            fingerprint="fp",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=datetime.now(UTC),
            last_seen=datetime.now(UTC),
            occurrence_count=1,
            status=SignatureStatus.NEW,
        )
        context = InvestigationContext(
            signature=sig,
            recent_events=(),
            trace_data=(),
            related_logs=(),
            codebase_path="/app",
            historical_context=(),
        )

        # Result with invalid confidence
        result = {
            "root_cause": "root",
            "evidence": ["test"],
            "suggested_fix": "fix",
            "confidence": "INVALID",
        }

        with pytest.raises(ValueError, match="Invalid confidence"):
            adapter._parse_diagnosis_result(result, context)


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
            async def get_events_for_signature(self, fingerprint, limit=5):
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
        diagnosis = await investigator.investigate(signature)

        assert diagnosis is not None
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
            def __init__(self):
                super().__init__()
                self.update_calls = []

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

            async def update(self, sig):
                self.update_calls.append((sig.fingerprint, sig.status, sig.diagnosis))
                await super().update(sig)

        telemetry = FakeTelemetryPort()
        store = TrackingStore()
        diagnosis_engine = FakeDiagnosisPort()
        notification = FailingNotificationPort()

        investigator = Investigator(
            telemetry, store, diagnosis_engine, notification, triage_engine, "/app"
        )

        # Investigate - notification will fail
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
                    raise Exception("Database connection failed during diagnosis persistence")
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

        management_service = ManagementService(
            store=store,
            telemetry=telemetry,
            diagnosis_engine=diagnosis_engine,
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

        management_service = ManagementService(
            store=store,
            telemetry=telemetry,
            diagnosis_engine=diagnosis_engine,
        )

        # Attempt to retriage non-existent signature
        with pytest.raises(ValueError, match="not found"):
            await management_service.retriage_signature("nonexistent-id")
