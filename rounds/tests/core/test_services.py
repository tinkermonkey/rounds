"""Unit tests for core domain services.

Tests verify that Fingerprinter, TriageEngine, Investigator, and PollService
implement the core diagnostic logic correctly.
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import sys

# Add the rounds directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.fingerprint import Fingerprinter
from core.triage import TriageEngine
from core.investigator import Investigator
from core.poll_service import PollService
from core.models import (
    Confidence,
    Diagnosis,
    ErrorEvent,
    InvestigationContext,
    LogEntry,
    PollResult,
    Severity,
    Signature,
    SignatureStatus,
    StackFrame,
    TraceTree,
    SpanNode,
)
from core.ports import (
    TelemetryPort,
    SignatureStorePort,
    DiagnosisPort,
    NotificationPort,
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
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
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
        first_seen=datetime(2024, 1, 1, 12, 0, 0),
        last_seen=datetime(2024, 1, 1, 12, 5, 0),
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
        confidence=Confidence.HIGH,
        diagnosed_at=datetime(2024, 1, 1, 12, 30, 0),
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
        high_confidence_threshold=Confidence.HIGH,
    )


# ============================================================================
# Mock Implementations
# ============================================================================


class MockTelemetryPort(TelemetryPort):
    """Mock implementation of TelemetryPort for testing."""

    def __init__(self, errors: list[ErrorEvent] | None = None):
        self.errors = errors or []
        self.get_events_called = False

    async def get_recent_errors(
        self, since: datetime, services: list[str] | None = None
    ) -> list[ErrorEvent]:
        """Mock implementation."""
        return self.errors

    async def get_trace(self, trace_id: str) -> TraceTree:
        """Mock implementation."""
        root_span = SpanNode(
            span_id="span-1",
            parent_id=None,
            service="test-service",
            operation="test-op",
            duration_ms=0,
            status="ok",
            attributes={},
            events=(),
        )
        return TraceTree(trace_id=trace_id, root_span=root_span, error_spans=())

    async def get_traces(self, trace_ids: list[str]) -> list[TraceTree]:
        """Mock implementation."""
        root_span = SpanNode(
            span_id="span-1",
            parent_id=None,
            service="test-service",
            operation="test-op",
            duration_ms=0,
            status="ok",
            attributes={},
            events=(),
        )
        return [
            TraceTree(trace_id=tid, root_span=root_span, error_spans=())
            for tid in trace_ids
        ]

    async def get_correlated_logs(
        self, trace_ids: list[str], window_minutes: int = 5
    ) -> list[LogEntry]:
        """Mock implementation."""
        return []

    async def get_events_for_signature(
        self, fingerprint: str, limit: int = 5
    ) -> list[ErrorEvent]:
        """Mock implementation."""
        self.get_events_called = True
        return self.errors


class MockSignatureStorePort(SignatureStorePort):
    """Mock implementation of SignatureStorePort for testing."""

    def __init__(self):
        self.signatures: dict[str, Signature] = {}
        self.pending_signatures: list[Signature] = []

    async def get_by_fingerprint(self, fingerprint: str) -> Signature | None:
        """Mock implementation."""
        return self.signatures.get(fingerprint)

    async def save(self, signature: Signature) -> None:
        """Mock implementation."""
        self.signatures[signature.fingerprint] = signature

    async def update(self, signature: Signature) -> None:
        """Mock implementation."""
        self.signatures[signature.fingerprint] = signature

    async def get_pending_investigation(self) -> list[Signature]:
        """Mock implementation."""
        return self.pending_signatures

    async def get_similar(
        self, signature: Signature, limit: int = 5
    ) -> list[Signature]:
        """Mock implementation."""
        return []

    async def get_stats(self) -> dict[str, Any]:
        """Mock implementation."""
        return {}


class MockDiagnosisPort(DiagnosisPort):
    """Mock implementation of DiagnosisPort for testing."""

    async def diagnose(self, context: InvestigationContext) -> Diagnosis:
        """Mock implementation."""
        return Diagnosis(
            root_cause="Mock root cause",
            evidence=("mock evidence",),
            suggested_fix="Mock fix",
            confidence=Confidence.HIGH,
            diagnosed_at=datetime.now(),
            model="mock-model",
            cost_usd=0.0,
        )

    async def estimate_cost(self, context: InvestigationContext) -> float:
        """Mock implementation."""
        return 0.0


class MockNotificationPort(NotificationPort):
    """Mock implementation of NotificationPort for testing."""

    def __init__(self):
        self.reported_diagnoses: list[tuple[Signature, Diagnosis]] = []

    async def report(self, signature: Signature, diagnosis: Diagnosis) -> None:
        """Mock implementation."""
        self.reported_diagnoses.append((signature, diagnosis))

    async def report_summary(self, stats: dict[str, Any]) -> None:
        """Mock implementation."""
        pass


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
        now = datetime.now()
        diagnosis = Diagnosis(
            root_cause="root",
            evidence=(),
            suggested_fix="fix",
            confidence=Confidence.HIGH,
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
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            occurrence_count=100,
            status=SignatureStatus.DIAGNOSED,
            diagnosis=diagnosis,
        )
        assert not triage_engine.should_investigate(sig)

    def test_should_investigate_after_cooldown_expires(
        self, triage_engine: TriageEngine
    ) -> None:
        """Should investigate if cooldown period has expired."""
        now = datetime.now()
        diagnosis = Diagnosis(
            root_cause="root",
            evidence=(),
            suggested_fix="fix",
            confidence=Confidence.HIGH,
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
            first_seen=datetime.now(),
            last_seen=datetime.now(),
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
        diagnosis = Diagnosis(
            root_cause="root",
            evidence=(),
            suggested_fix="fix",
            confidence=Confidence.MEDIUM,
            diagnosed_at=datetime.now(),
            model="model",
            cost_usd=0.0,
        )
        signature.status = SignatureStatus.NEW
        assert triage_engine.should_notify(signature, diagnosis)

    def test_should_not_notify_low_confidence(
        self, triage_engine: TriageEngine, signature: Signature
    ) -> None:
        """Should not notify low confidence diagnoses."""
        diagnosis = Diagnosis(
            root_cause="root",
            evidence=(),
            suggested_fix="fix",
            confidence=Confidence.LOW,
            diagnosed_at=datetime.now(),
            model="model",
            cost_usd=0.0,
        )
        assert not triage_engine.should_notify(signature, diagnosis)

    def test_should_notify_critical_tag(
        self, triage_engine: TriageEngine, signature: Signature
    ) -> None:
        """Should notify signatures tagged as critical."""
        diagnosis = Diagnosis(
            root_cause="root",
            evidence=(),
            suggested_fix="fix",
            confidence=Confidence.LOW,
            diagnosed_at=datetime.now(),
            model="model",
            cost_usd=0.0,
        )
        signature.tags = frozenset(["critical"])
        assert triage_engine.should_notify(signature, diagnosis)

    def test_calculate_priority_frequency_component(
        self, triage_engine: TriageEngine
    ) -> None:
        """Priority should increase with occurrence count."""
        sig1 = Signature(
            id="sig-1",
            fingerprint="fp-1",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=datetime.now(),
            last_seen=datetime.now(),
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
            first_seen=datetime.now(),
            last_seen=datetime.now(),
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
        now = datetime.now()
        sig1 = Signature(
            id="sig-1",
            fingerprint="fp-1",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=now,
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
            first_seen=now,
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
        sig1 = Signature(
            id="sig-1",
            fingerprint="fp-1",
            error_type="Error",
            service="service",
            message_template="msg",
            stack_hash="hash",
            first_seen=datetime.now(),
            last_seen=datetime.now(),
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
            first_seen=datetime.now(),
            last_seen=datetime.now(),
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
        telemetry = MockTelemetryPort(errors=[error_event])
        store = MockSignatureStorePort()
        diagnosis_engine = MockDiagnosisPort()
        notification = MockNotificationPort()
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
        telemetry = MockTelemetryPort(errors=[error_event])
        store = MockSignatureStorePort()
        diagnosis_engine = MockDiagnosisPort()
        notification = MockNotificationPort()
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
            stack_hash=fingerprinter._hash_stack(
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
        telemetry = MockTelemetryPort()
        store = MockSignatureStorePort()
        diagnosis_engine = MockDiagnosisPort()
        notification = MockNotificationPort()
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

        diagnoses = await poll_service.execute_investigation_cycle()

        assert len(diagnoses) == 1
        assert signature.status == SignatureStatus.DIAGNOSED
        assert signature.diagnosis is not None
