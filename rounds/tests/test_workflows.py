"""End-to-end integration tests for core workflows.

These tests verify that the core services (Fingerprinter, TriageEngine,
Investigator, PollService) work together correctly using fake adapters.
"""

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

# Add the rounds directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from rounds.core.fingerprint import Fingerprinter
from rounds.core.triage import TriageEngine
from rounds.core.investigator import Investigator
from rounds.core.poll_service import PollService
from rounds.core.models import (
    Confidence,
    Diagnosis,
    ErrorEvent,
    Severity,
    SignatureStatus,
    StackFrame,
)
from tests.fakes import (
    FakeTelemetryPort,
    FakeSignatureStorePort,
    FakeDiagnosisPort,
    FakeNotificationPort,
)


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def fingerprinter() -> Fingerprinter:
    """Create a Fingerprinter instance."""
    return Fingerprinter()


@pytest.fixture
def triage_engine() -> TriageEngine:
    """Create a TriageEngine with test configuration."""
    return TriageEngine(
        min_occurrence_for_investigation=3,
        investigation_cooldown_hours=24,
        high_confidence_threshold="high",
    )


@pytest.fixture
def telemetry_port() -> FakeTelemetryPort:
    """Create a fake telemetry port."""
    return FakeTelemetryPort()


@pytest.fixture
def store_port() -> FakeSignatureStorePort:
    """Create a fake signature store."""
    return FakeSignatureStorePort()


@pytest.fixture
def diagnosis_port() -> FakeDiagnosisPort:
    """Create a fake diagnosis port."""
    return FakeDiagnosisPort()


@pytest.fixture
def notification_port() -> FakeNotificationPort:
    """Create a fake notification port."""
    return FakeNotificationPort()


@pytest.fixture
def error_event() -> ErrorEvent:
    """Create a sample error event."""
    return ErrorEvent(
        trace_id="trace-001",
        span_id="span-001",
        service="test-service",
        error_type="TimeoutError",
        error_message="Request timed out after 30 seconds",
        stack_frames=(
            StackFrame(
                module="app.handler",
                function="handle_request",
                filename="handler.py",
                lineno=42,
            ),
        ),
        timestamp=datetime.now(timezone.utc),
        attributes={"user_id": "user-123", "endpoint": "/api/orders"},
        severity=Severity.ERROR,
    )


# ============================================================================
# Poll Cycle Workflow Tests
# ============================================================================


@pytest.mark.asyncio
class TestPollCycleWorkflow:
    """Test the complete poll cycle workflow."""

    async def test_poll_detects_new_error(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        telemetry_port: FakeTelemetryPort,
        store_port: FakeSignatureStorePort,
        diagnosis_port: FakeDiagnosisPort,
        notification_port: FakeNotificationPort,
        error_event: ErrorEvent,
    ) -> None:
        """Poll cycle should detect and store new errors."""
        # Setup: add error to telemetry
        telemetry_port.add_error(error_event)

        # Create services
        investigator = Investigator(
            telemetry=telemetry_port,
            store=store_port,
            diagnosis_engine=diagnosis_port,
            notification=notification_port,
            triage=triage_engine,
            codebase_path="./",
        )
        poll_service = PollService(
            telemetry=telemetry_port,
            store=store_port,
            fingerprinter=fingerprinter,
            triage=triage_engine,
            investigator=investigator,
            lookback_minutes=60,
        )

        # Execute: poll cycle
        result = await poll_service.execute_poll_cycle()

        # Assert: error was detected and signature created
        assert result.errors_found == 1
        assert result.new_signatures == 1
        assert result.updated_signatures == 0
        assert len(store_port.saved_signatures) == 1

    async def test_poll_updates_existing_signature(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        telemetry_port: FakeTelemetryPort,
        store_port: FakeSignatureStorePort,
        diagnosis_port: FakeDiagnosisPort,
        notification_port: FakeNotificationPort,
        error_event: ErrorEvent,
    ) -> None:
        """Poll cycle should update existing signatures on repeat errors."""
        # Setup: add single error
        telemetry_port.add_error(error_event)

        # Create services
        investigator = Investigator(
            telemetry=telemetry_port,
            store=store_port,
            diagnosis_engine=diagnosis_port,
            notification=notification_port,
            triage=triage_engine,
            codebase_path="./",
        )
        poll_service = PollService(
            telemetry=telemetry_port,
            store=store_port,
            fingerprinter=fingerprinter,
            triage=triage_engine,
            investigator=investigator,
            lookback_minutes=60,
        )

        # Execute: first poll
        result1 = await poll_service.execute_poll_cycle()
        assert result1.new_signatures == 1
        assert result1.updated_signatures == 0
        initial_count = store_port.saved_signatures[0].occurrence_count

        # Setup: add more of the same error
        telemetry_port.reset()
        telemetry_port.add_error(error_event)

        # Execute: second poll
        result2 = await poll_service.execute_poll_cycle()

        # Assert: signature was updated, not created again
        assert result2.new_signatures == 0
        assert result2.updated_signatures == 1
        # Occurrence count should have increased
        updated_sig = store_port.signatures[fingerprinter.fingerprint(error_event)]
        assert updated_sig.occurrence_count > initial_count

    async def test_poll_deduplicates_same_error(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        telemetry_port: FakeTelemetryPort,
        store_port: FakeSignatureStorePort,
        diagnosis_port: FakeDiagnosisPort,
        notification_port: FakeNotificationPort,
        error_event: ErrorEvent,
    ) -> None:
        """Poll cycle should deduplicate identical errors in a single cycle."""
        # Setup: add same error multiple times
        errors = [error_event for _ in range(5)]
        telemetry_port.add_errors(errors)

        # Create services
        investigator = Investigator(
            telemetry=telemetry_port,
            store=store_port,
            diagnosis_engine=diagnosis_port,
            notification=notification_port,
            triage=triage_engine,
            codebase_path="./",
        )
        poll_service = PollService(
            telemetry=telemetry_port,
            store=store_port,
            fingerprinter=fingerprinter,
            triage=triage_engine,
            investigator=investigator,
            lookback_minutes=60,
        )

        # Execute: poll cycle
        result = await poll_service.execute_poll_cycle()

        # Assert: errors deduplicated into one signature
        assert result.errors_found == 5
        assert result.new_signatures == 1
        assert len(store_port.saved_signatures) == 1
        # Signature should have occurrence_count of 5
        sig = store_port.saved_signatures[0]
        assert sig.occurrence_count == 5


# ============================================================================
# Investigation Workflow Tests
# ============================================================================


@pytest.mark.asyncio
class TestInvestigationWorkflow:
    """Test the complete investigation workflow."""

    async def test_investigation_diagnoses_pending_signature(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        telemetry_port: FakeTelemetryPort,
        store_port: FakeSignatureStorePort,
        diagnosis_port: FakeDiagnosisPort,
        notification_port: FakeNotificationPort,
        error_event: ErrorEvent,
    ) -> None:
        """Investigation cycle should diagnose pending signatures."""
        from rounds.core.models import Signature

        # Setup: create a signature and mark it pending
        fp = fingerprinter.fingerprint(error_event)
        sig = Signature(
            id="sig-001",
            fingerprint=fp,
            error_type=error_event.error_type,
            service=error_event.service,
            message_template=error_event.error_message,
            stack_hash="hash",
            first_seen=error_event.timestamp,
            last_seen=error_event.timestamp,
            occurrence_count=5,
            status=SignatureStatus.NEW,
        )
        await store_port.save(sig)
        store_port.mark_pending(sig)

        # Configure diagnosis to return HIGH confidence so it gets reported
        from rounds.core.models import Diagnosis

        diagnosis_port.set_default_diagnosis(
            Diagnosis(
                root_cause="Test root cause",
                evidence=("Test evidence",),
                suggested_fix="Test fix",
                confidence="high",
                diagnosed_at=datetime.now(),
                model="test-model",
                cost_usd=0.1,
            )
        )

        telemetry_port.add_error(error_event)

        # Create services
        investigator = Investigator(
            telemetry=telemetry_port,
            store=store_port,
            diagnosis_engine=diagnosis_port,
            notification=notification_port,
            triage=triage_engine,
            codebase_path="./",
        )
        poll_service = PollService(
            telemetry=telemetry_port,
            store=store_port,
            fingerprinter=fingerprinter,
            triage=triage_engine,
            investigator=investigator,
            lookback_minutes=60,
        )

        # Execute: investigation cycle
        diagnoses = await poll_service.execute_investigation_cycle()

        # Assert: signature was diagnosed and reported
        assert len(diagnoses) == 1
        assert len(notification_port.reported_diagnoses) == 1

    async def test_investigation_respects_triage_rules(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        telemetry_port: FakeTelemetryPort,
        store_port: FakeSignatureStorePort,
        diagnosis_port: FakeDiagnosisPort,
        notification_port: FakeNotificationPort,
        error_event: ErrorEvent,
    ) -> None:
        """Investigation cycle should respect triage rules."""
        # Setup: create a muted signature
        from rounds.core.models import Signature

        fp = fingerprinter.fingerprint(error_event)
        sig = Signature(
            id="sig-001",
            fingerprint=fp,
            error_type=error_event.error_type,
            service=error_event.service,
            message_template=error_event.error_message,
            stack_hash="hash",
            first_seen=error_event.timestamp,
            last_seen=error_event.timestamp,
            occurrence_count=5,
            status=SignatureStatus.MUTED,
        )
        await store_port.save(sig)
        store_port.mark_pending(sig)

        telemetry_port.add_error(error_event)

        # Create services
        investigator = Investigator(
            telemetry=telemetry_port,
            store=store_port,
            diagnosis_engine=diagnosis_port,
            notification=notification_port,
            triage=triage_engine,
            codebase_path="./",
        )
        poll_service = PollService(
            telemetry=telemetry_port,
            store=store_port,
            fingerprinter=fingerprinter,
            triage=triage_engine,
            investigator=investigator,
            lookback_minutes=60,
        )

        # Execute: investigation cycle
        diagnoses = await poll_service.execute_investigation_cycle()

        # Assert: muted signature was not investigated
        assert len(diagnoses) == 0
        assert len(notification_port.reported_diagnoses) == 0


# ============================================================================
# Error Recovery Tests
# ============================================================================


@pytest.mark.asyncio
class TestErrorRecovery:
    """Test error recovery in workflows."""

    async def test_poll_handles_diagnosis_failure(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        telemetry_port: FakeTelemetryPort,
        store_port: FakeSignatureStorePort,
        diagnosis_port: FakeDiagnosisPort,
        notification_port: FakeNotificationPort,
        error_event: ErrorEvent,
    ) -> None:
        """Poll cycle should handle diagnosis failures gracefully."""
        # Setup: configure diagnosis to fail
        diagnosis_port.set_should_fail(True, "Diagnosis service is down")

        telemetry_port.add_error(error_event)

        # Create services
        investigator = Investigator(
            telemetry=telemetry_port,
            store=store_port,
            diagnosis_engine=diagnosis_port,
            notification=notification_port,
            triage=triage_engine,
            codebase_path="./",
        )
        poll_service = PollService(
            telemetry=telemetry_port,
            store=store_port,
            fingerprinter=fingerprinter,
            triage=triage_engine,
            investigator=investigator,
            lookback_minutes=60,
        )

        # Execute: poll cycle should not crash despite diagnosis failure
        result = await poll_service.execute_poll_cycle()

        # Assert: error was still detected and signature created
        assert result.errors_found == 1
        assert result.new_signatures == 1

    async def test_poll_handles_notification_failure(
        self,
        fingerprinter: Fingerprinter,
        triage_engine: TriageEngine,
        telemetry_port: FakeTelemetryPort,
        store_port: FakeSignatureStorePort,
        diagnosis_port: FakeDiagnosisPort,
        notification_port: FakeNotificationPort,
        error_event: ErrorEvent,
    ) -> None:
        """Poll cycle should handle notification failures gracefully."""
        # Setup: create a pending high-count signature
        from rounds.core.models import Signature

        fp = fingerprinter.fingerprint(error_event)
        sig = Signature(
            id="sig-001",
            fingerprint=fp,
            error_type=error_event.error_type,
            service=error_event.service,
            message_template=error_event.error_message,
            stack_hash="hash",
            first_seen=error_event.timestamp,
            last_seen=error_event.timestamp,
            occurrence_count=10,  # Above threshold
            status=SignatureStatus.NEW,
        )
        await store_port.save(sig)
        store_port.mark_pending(sig)

        # Configure notification to fail
        notification_port.set_should_fail(True, "Notification service is down")

        telemetry_port.add_error(error_event)

        # Create services
        investigator = Investigator(
            telemetry=telemetry_port,
            store=store_port,
            diagnosis_engine=diagnosis_port,
            notification=notification_port,
            triage=triage_engine,
            codebase_path="./",
        )
        poll_service = PollService(
            telemetry=telemetry_port,
            store=store_port,
            fingerprinter=fingerprinter,
            triage=triage_engine,
            investigator=investigator,
            lookback_minutes=60,
        )

        # Execute: investigation cycle should handle notification failure
        diagnoses = await poll_service.execute_investigation_cycle()

        # Assert: diagnosis was generated even though notification failed
        assert len(diagnoses) == 1
        diagnosis = diagnoses[0]
        assert diagnosis.root_cause is not None
        assert diagnosis.confidence == "medium"
