"""Unit tests for fake adapter implementations.

These tests verify that fake adapters work correctly as test doubles
and can be used confidently in tests of core domain logic.
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import sys

# Add the rounds directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rounds.core.models import (
    Confidence,
    Diagnosis,
    ErrorEvent,
    InvestigationContext,
    LogEntry,
    PollResult,
    Severity,
    Signature,
    SignatureStatus,
    SpanNode,
    StackFrame,
    TraceTree,
)
from tests.fakes import (
    FakeTelemetryPort,
    FakeSignatureStorePort,
    FakeDiagnosisPort,
    FakeNotificationPort,
    FakePollPort,
    FakeManagementPort,
)


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def error_event() -> ErrorEvent:
    """Create a sample error event."""
    return ErrorEvent(
        trace_id="trace-123",
        span_id="span-456",
        service="payment-service",
        error_type="ConnectionTimeoutError",
        error_message="Failed to connect to database",
        stack_frames=(
            StackFrame(
                module="payment.service",
                function="process_charge",
                filename="service.py",
                lineno=42,
            ),
        ),
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        attributes={"user_id": "123"},
        severity=Severity.ERROR,
    )


@pytest.fixture
def signature() -> Signature:
    """Create a sample signature."""
    return Signature(
        id="sig-001",
        fingerprint="abc123def456",
        error_type="ConnectionTimeoutError",
        service="payment-service",
        message_template="Failed to connect to database",
        stack_hash="hash-stack-001",
        first_seen=datetime(2024, 1, 1, 12, 0, 0),
        last_seen=datetime(2024, 1, 1, 12, 5, 0),
        occurrence_count=5,
        status=SignatureStatus.NEW,
    )


@pytest.fixture
def diagnosis() -> Diagnosis:
    """Create a sample diagnosis."""
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
def trace() -> TraceTree:
    """Create a sample trace."""
    root_span = SpanNode(
        span_id="span-1",
        parent_id=None,
        service="payment-service",
        operation="process_charge",
        duration_ms=5000,
        status="error",
        attributes={"error.type": "ConnectionTimeoutError"},
        events=(),
    )
    return TraceTree(
        trace_id="trace-123",
        root_span=root_span,
        error_spans=(root_span,),
    )


@pytest.fixture
def log_entry() -> LogEntry:
    """Create a sample log entry."""
    return LogEntry(
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        severity=Severity.ERROR,
        body="Connection timeout occurred",
        attributes={"database": "postgres"},
        trace_id="trace-123",
        span_id="span-1",
    )


# ============================================================================
# FakeTelemetryPort Tests
# ============================================================================


class TestFakeTelemetryPort:
    """Test FakeTelemetryPort implementation."""

    @pytest.mark.asyncio
    async def test_add_and_get_errors(self, error_event: ErrorEvent) -> None:
        """Should store and retrieve error events."""
        port = FakeTelemetryPort()
        port.add_error(error_event)

        errors = await port.get_recent_errors(
            error_event.timestamp - timedelta(minutes=1)
        )
        assert len(errors) == 1
        assert errors[0].trace_id == error_event.trace_id

    @pytest.mark.asyncio
    async def test_get_recent_errors_filters_by_time(
        self, error_event: ErrorEvent
    ) -> None:
        """Should filter errors by timestamp."""
        port = FakeTelemetryPort()
        port.add_error(error_event)

        # Query before error happened
        errors = await port.get_recent_errors(
            error_event.timestamp + timedelta(minutes=1)
        )
        assert len(errors) == 0

        # Query after error happened
        errors = await port.get_recent_errors(
            error_event.timestamp - timedelta(minutes=1)
        )
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_get_recent_errors_filters_by_service(
        self, error_event: ErrorEvent
    ) -> None:
        """Should filter errors by service name."""
        port = FakeTelemetryPort()
        port.add_error(error_event)

        errors = await port.get_recent_errors(
            datetime(2024, 1, 1, 11, 0, 0), services=["payment-service"]
        )
        assert len(errors) == 1

        errors = await port.get_recent_errors(
            datetime(2024, 1, 1, 11, 0, 0), services=["unknown-service"]
        )
        assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_add_and_get_traces(self, trace: TraceTree) -> None:
        """Should store and retrieve traces."""
        port = FakeTelemetryPort()
        port.add_trace(trace)

        retrieved = await port.get_trace(trace.trace_id)
        assert retrieved.trace_id == trace.trace_id

    @pytest.mark.asyncio
    async def test_get_trace_returns_synthetic_if_not_found(self) -> None:
        """Should return synthetic trace if not found."""
        port = FakeTelemetryPort()
        trace = await port.get_trace("unknown-trace-id")
        assert trace.trace_id == "unknown-trace-id"

    @pytest.mark.asyncio
    async def test_get_traces_multiple(self) -> None:
        """Should retrieve multiple traces."""
        port = FakeTelemetryPort()

        root_span_1 = SpanNode(
            span_id="span-1", parent_id=None, service="svc1", operation="op1",
            duration_ms=100, status="ok", attributes={}, events=(),
        )
        trace1 = TraceTree(trace_id="trace-1", root_span=root_span_1, error_spans=())

        root_span_2 = SpanNode(
            span_id="span-2", parent_id=None, service="svc2", operation="op2",
            duration_ms=200, status="ok", attributes={}, events=(),
        )
        trace2 = TraceTree(trace_id="trace-2", root_span=root_span_2, error_spans=())

        port.add_traces([trace1, trace2])

        traces = await port.get_traces(["trace-1", "trace-2"])
        assert len(traces) == 2

    @pytest.mark.asyncio
    async def test_get_correlated_logs(self, log_entry: LogEntry) -> None:
        """Should retrieve logs correlated with trace IDs."""
        port = FakeTelemetryPort()
        port.add_log(log_entry)

        logs = await port.get_correlated_logs(["trace-123"])
        assert len(logs) == 1
        assert logs[0].trace_id == "trace-123"

    @pytest.mark.asyncio
    async def test_get_correlated_logs_filters_by_trace_id(
        self, log_entry: LogEntry
    ) -> None:
        """Should only return logs for specified trace IDs."""
        port = FakeTelemetryPort()
        port.add_log(log_entry)

        logs = await port.get_correlated_logs(["trace-999"])
        assert len(logs) == 0

    @pytest.mark.asyncio
    async def test_get_events_for_signature(self, error_event: ErrorEvent) -> None:
        """Should retrieve events for a specific signature."""
        port = FakeTelemetryPort()
        port.add_signature_events("fp-001", [error_event])

        events = await port.get_events_for_signature("fp-001", limit=10)
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_get_events_for_signature_respects_limit(
        self, error_event: ErrorEvent
    ) -> None:
        """Should respect the limit parameter."""
        port = FakeTelemetryPort()
        errors = [
            error_event,
            ErrorEvent(
                trace_id="trace-2", span_id="span-2", service="service2",
                error_type="Error", error_message="Error 2",
                stack_frames=(), timestamp=datetime.now(),
                attributes={}, severity=Severity.ERROR,
            ),
        ]
        port.add_signature_events("fp-001", errors)

        events = await port.get_events_for_signature("fp-001", limit=1)
        assert len(events) == 1

    def test_reset(self, error_event: ErrorEvent, trace: TraceTree) -> None:
        """Should reset all data on reset()."""
        port = FakeTelemetryPort()
        port.add_error(error_event)
        port.add_trace(trace)

        assert len(port.errors) > 0
        assert len(port.traces) > 0

        port.reset()

        assert len(port.errors) == 0
        assert len(port.traces) == 0
        assert port.get_recent_errors_call_count == 0


# ============================================================================
# FakeSignatureStorePort Tests
# ============================================================================


class TestFakeSignatureStorePort:
    """Test FakeSignatureStorePort implementation."""

    @pytest.mark.asyncio
    async def test_save_and_get(self, signature: Signature) -> None:
        """Should save and retrieve signatures."""
        store = FakeSignatureStorePort()
        await store.save(signature)

        retrieved = await store.get_by_fingerprint(signature.fingerprint)
        assert retrieved is not None
        assert retrieved.fingerprint == signature.fingerprint

    @pytest.mark.asyncio
    async def test_update_signature(self, signature: Signature) -> None:
        """Should update existing signatures."""
        store = FakeSignatureStorePort()
        await store.save(signature)

        signature.occurrence_count = 10
        await store.update(signature)

        retrieved = await store.get_by_fingerprint(signature.fingerprint)
        assert retrieved is not None
        assert retrieved.occurrence_count == 10

    @pytest.mark.asyncio
    async def test_get_pending_investigation(self, signature: Signature) -> None:
        """Should retrieve pending investigation signatures."""
        store = FakeSignatureStorePort()
        await store.save(signature)
        store.mark_pending(signature)

        pending = await store.get_pending_investigation()
        assert len(pending) == 1
        assert pending[0].fingerprint == signature.fingerprint

    @pytest.mark.asyncio
    async def test_get_similar_signatures(self, signature: Signature) -> None:
        """Should find similar signatures by error type and service."""
        store = FakeSignatureStorePort()

        sig1 = Signature(
            id="sig-1",
            fingerprint="fp-1",
            error_type="TimeoutError",
            service="payment-service",
            message_template="msg",
            stack_hash="hash",
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            occurrence_count=5,
            status=SignatureStatus.NEW,
        )
        sig2 = Signature(
            id="sig-2",
            fingerprint="fp-2",
            error_type="TimeoutError",
            service="payment-service",
            message_template="msg2",
            stack_hash="hash2",
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            occurrence_count=3,
            status=SignatureStatus.NEW,
        )
        sig3 = Signature(
            id="sig-3",
            fingerprint="fp-3",
            error_type="ValueError",
            service="different-service",
            message_template="msg3",
            stack_hash="hash3",
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            occurrence_count=2,
            status=SignatureStatus.NEW,
        )

        await store.save(sig1)
        await store.save(sig2)
        await store.save(sig3)

        similar = await store.get_similar(sig1)
        assert len(similar) == 1
        assert similar[0].fingerprint == "fp-2"

    @pytest.mark.asyncio
    async def test_get_stats(self, signature: Signature) -> None:
        """Should return statistics about stored signatures."""
        store = FakeSignatureStorePort()
        await store.save(signature)

        stats = await store.get_stats()
        assert stats["total_signatures"] == 1
        assert stats["saved_count"] == 1

    @pytest.mark.asyncio
    async def test_tracked_operations(self, signature: Signature) -> None:
        """Should track all operations for assertions."""
        store = FakeSignatureStorePort()

        await store.get_by_fingerprint("fp-001")
        await store.save(signature)
        await store.update(signature)
        await store.get_pending_investigation()

        assert len(store.get_by_fingerprint_calls) == 1
        assert len(store.saved_signatures) == 1
        assert len(store.updated_signatures) == 1

    @pytest.mark.asyncio
    async def test_reset(self, signature: Signature) -> None:
        """Should reset all data on reset()."""
        store = FakeSignatureStorePort()

        await store.save(signature)

        assert len(store.signatures) > 0
        store.reset()
        assert len(store.signatures) == 0
        assert len(store.saved_signatures) == 0


# ============================================================================
# FakeDiagnosisPort Tests
# ============================================================================


class TestFakeDiagnosisPort:
    """Test FakeDiagnosisPort implementation."""

    @pytest.mark.asyncio
    async def test_diagnose_returns_default(self, signature: Signature) -> None:
        """Should return default diagnosis if set."""
        port = FakeDiagnosisPort()
        diagnosis = Diagnosis(
            root_cause="Test root cause",
            evidence=("test evidence",),
            suggested_fix="test fix",
            confidence=Confidence.HIGH,
            diagnosed_at=datetime.now(),
            model="test-model",
            cost_usd=0.5,
        )
        port.set_default_diagnosis(diagnosis)

        context = InvestigationContext(
            signature=signature,
            recent_events=(),
            trace_data=(),
            related_logs=(),
            codebase_path="/app",
            historical_context=(),
        )

        result = await port.diagnose(context)
        assert result.root_cause == diagnosis.root_cause

    @pytest.mark.asyncio
    async def test_diagnose_signature_specific(self, signature: Signature) -> None:
        """Should return signature-specific diagnosis if available."""
        port = FakeDiagnosisPort()
        diagnosis1 = Diagnosis(
            root_cause="Cause 1",
            evidence=(),
            suggested_fix="fix",
            confidence=Confidence.HIGH,
            diagnosed_at=datetime.now(),
            model="model",
            cost_usd=0.0,
        )
        diagnosis2 = Diagnosis(
            root_cause="Cause 2",
            evidence=(),
            suggested_fix="fix",
            confidence=Confidence.HIGH,
            diagnosed_at=datetime.now(),
            model="model",
            cost_usd=0.0,
        )

        port.set_diagnosis_for_signature("fp-001", diagnosis1)
        port.set_diagnosis_for_signature("fp-002", diagnosis2)

        sig1 = Signature(
            id="sig-1", fingerprint="fp-001", error_type="Error",
            service="svc", message_template="msg", stack_hash="hash",
            first_seen=datetime.now(), last_seen=datetime.now(),
            occurrence_count=5, status=SignatureStatus.NEW,
        )
        context = InvestigationContext(
            signature=sig1, recent_events=(), trace_data=(),
            related_logs=(), codebase_path="/", historical_context=(),
        )

        result = await port.diagnose(context)
        assert result.root_cause == "Cause 1"

    @pytest.mark.asyncio
    async def test_estimate_cost(self, signature: Signature) -> None:
        """Should return cost estimate."""
        port = FakeDiagnosisPort()
        port.set_default_cost(1.5)

        context = InvestigationContext(
            signature=signature,
            recent_events=(),
            trace_data=(),
            related_logs=(),
            codebase_path="/app",
            historical_context=(),
        )

        cost = await port.estimate_cost(context)
        assert cost == 1.5

    @pytest.mark.asyncio
    async def test_should_fail(self, signature: Signature) -> None:
        """Should fail when configured to do so."""
        port = FakeDiagnosisPort()
        port.set_should_fail(True, "Test error")

        context = InvestigationContext(
            signature=signature,
            recent_events=(),
            trace_data=(),
            related_logs=(),
            codebase_path="/app",
            historical_context=(),
        )

        with pytest.raises(RuntimeError, match="Test error"):
            await port.diagnose(context)

    def test_reset(self, diagnosis: Diagnosis) -> None:
        """Should reset all data on reset()."""
        port = FakeDiagnosisPort()
        port.set_default_diagnosis(diagnosis)

        assert port.default_diagnosis is not None
        port.reset()
        assert port.default_diagnosis is None


# ============================================================================
# FakeNotificationPort Tests
# ============================================================================


class TestFakeNotificationPort:
    """Test FakeNotificationPort implementation."""

    @pytest.mark.asyncio
    async def test_report_diagnosis(
        self, signature: Signature, diagnosis: Diagnosis
    ) -> None:
        """Should capture reported diagnoses."""
        port = FakeNotificationPort()
        await port.report(signature, diagnosis)

        assert port.get_reported_diagnosis_count() == 1
        last = port.get_last_diagnosis_report()
        assert last is not None
        assert last[0].fingerprint == signature.fingerprint

    @pytest.mark.asyncio
    async def test_report_summary(self) -> None:
        """Should capture reported summaries."""
        port = FakeNotificationPort()
        summary = {"total": 10, "investigated": 5}
        await port.report_summary(summary)

        assert len(port.reported_summaries) == 1
        assert port.reported_summaries[0] == summary

    @pytest.mark.asyncio
    async def test_get_reported_diagnoses_for_signature(
        self, diagnosis: Diagnosis
    ) -> None:
        """Should retrieve diagnoses for specific signatures."""
        port = FakeNotificationPort()

        sig1 = Signature(
            id="sig-1", fingerprint="fp-1", error_type="Error",
            service="svc", message_template="msg", stack_hash="hash",
            first_seen=datetime.now(), last_seen=datetime.now(),
            occurrence_count=5, status=SignatureStatus.NEW,
        )
        sig2 = Signature(
            id="sig-2", fingerprint="fp-2", error_type="Error",
            service="svc", message_template="msg", stack_hash="hash",
            first_seen=datetime.now(), last_seen=datetime.now(),
            occurrence_count=5, status=SignatureStatus.NEW,
        )

        await port.report(sig1, diagnosis)
        await port.report(sig2, diagnosis)

        sig1_reports = port.get_reported_diagnoses_for_signature("sig-1")
        assert len(sig1_reports) == 1

    @pytest.mark.asyncio
    async def test_should_fail(
        self, signature: Signature, diagnosis: Diagnosis
    ) -> None:
        """Should fail when configured to do so."""
        port = FakeNotificationPort()
        port.set_should_fail(True, "Test error")

        with pytest.raises(RuntimeError, match="Test error"):
            await port.report(signature, diagnosis)

    @pytest.mark.asyncio
    async def test_reset(self, signature: Signature, diagnosis: Diagnosis) -> None:
        """Should reset all data on reset()."""
        port = FakeNotificationPort()

        await port.report(signature, diagnosis)
        await port.report_summary({})

        assert len(port.reported_diagnoses) > 0
        port.reset()
        assert len(port.reported_diagnoses) == 0


# ============================================================================
# FakePollPort Tests
# ============================================================================


class TestFakePollPort:
    """Test FakePollPort implementation."""

    @pytest.mark.asyncio
    async def test_execute_poll_cycle(self) -> None:
        """Should execute poll cycles and return results."""
        port = FakePollPort()
        result = PollResult(
            errors_found=10,
            new_signatures=3,
            updated_signatures=2,
            investigations_queued=1,
            timestamp=datetime.now(),
        )
        port.set_default_poll_result(result)

        poll_result = await port.execute_poll_cycle()
        assert poll_result.errors_found == 10

    @pytest.mark.asyncio
    async def test_execute_investigation_cycle(self) -> None:
        """Should execute investigation cycles and return diagnoses."""
        port = FakePollPort()
        diagnosis = Diagnosis(
            root_cause="Test", evidence=(), suggested_fix="fix",
            confidence=Confidence.HIGH, diagnosed_at=datetime.now(),
            model="model", cost_usd=0.0,
        )
        port.set_default_investigation_result([diagnosis])

        result = await port.execute_investigation_cycle()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_queued_results(self) -> None:
        """Should return queued results in order."""
        port = FakePollPort()
        result1 = PollResult(
            errors_found=1, new_signatures=0, updated_signatures=0,
            investigations_queued=0, timestamp=datetime.now(),
        )
        result2 = PollResult(
            errors_found=2, new_signatures=0, updated_signatures=0,
            investigations_queued=0, timestamp=datetime.now(),
        )

        port.add_poll_result(result1)
        port.add_poll_result(result2)

        r1 = await port.execute_poll_cycle()
        assert r1.errors_found == 1
        r2 = await port.execute_poll_cycle()
        assert r2.errors_found == 2

    def test_reset(self) -> None:
        """Should reset all data on reset()."""
        port = FakePollPort()
        port.set_default_poll_result(PollResult(
            errors_found=1, new_signatures=0, updated_signatures=0,
            investigations_queued=0, timestamp=datetime.now(),
        ))

        assert port.default_poll_result is not None
        port.reset()
        assert port.default_poll_result is None


# ============================================================================
# FakeManagementPort Tests
# ============================================================================


class TestFakeManagementPort:
    """Test FakeManagementPort implementation."""

    @pytest.mark.asyncio
    async def test_mute_signature(self) -> None:
        """Should track muted signatures."""
        port = FakeManagementPort()
        await port.mute_signature("sig-001", reason="false positive")

        assert port.is_signature_muted("sig-001")
        assert port.get_mute_reason("sig-001") == "false positive"

    @pytest.mark.asyncio
    async def test_resolve_signature(self) -> None:
        """Should track resolved signatures."""
        port = FakeManagementPort()
        await port.resolve_signature("sig-001", fix_applied="upgraded library")

        assert port.is_signature_resolved("sig-001")
        assert port.get_fix_applied("sig-001") == "upgraded library"

    @pytest.mark.asyncio
    async def test_retriage_signature(self) -> None:
        """Should track retriaged signatures."""
        port = FakeManagementPort()
        await port.retriage_signature("sig-001")

        assert port.is_signature_retriaged("sig-001")

    @pytest.mark.asyncio
    async def test_get_signature_details(self) -> None:
        """Should return pre-configured signature details."""
        port = FakeManagementPort()
        details = {"status": "new", "count": 5}
        port.set_signature_details("sig-001", details)

        result = await port.get_signature_details("sig-001")
        assert result == details

    @pytest.mark.asyncio
    async def test_should_fail(self) -> None:
        """Should fail when configured to do so."""
        port = FakeManagementPort()
        port.set_should_fail(True, "Test error")

        with pytest.raises(RuntimeError, match="Test error"):
            await port.mute_signature("sig-001")

    @pytest.mark.asyncio
    async def test_reset(self) -> None:
        """Should reset all data on reset()."""
        port = FakeManagementPort()

        await port.mute_signature("sig-001")
        await port.resolve_signature("sig-002")
        await port.retriage_signature("sig-003")

        assert len(port.muted_signatures) > 0
        port.reset()
        assert len(port.muted_signatures) == 0
        assert len(port.resolved_signatures) == 0
        assert len(port.retriaged_signatures) == 0
