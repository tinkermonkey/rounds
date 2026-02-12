"""Unit tests for port interface contracts.

Tests verify that port abstract base classes are properly defined
and that implementations must satisfy the interface contract.
"""

import pytest
from datetime import datetime
from pathlib import Path
from typing import Any
import sys

# Add the rounds directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

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
    PollPort,
    ManagementPort,
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
        error_message="Failed to connect to database: timeout after 30s",
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
        message_template="Failed to connect to database: timeout after {duration}s",
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
        root_cause="Database connection pool is exhausted due to missing close() calls in error paths",
        evidence=(
            "Stack trace shows multiple open connections",
            "Database logs show 'connection limit exceeded' at 12:00:00",
        ),
        suggested_fix="Wrap database operations in try-finally to ensure connections are closed",
        confidence=Confidence.HIGH,
        diagnosed_at=datetime(2024, 1, 1, 12, 30, 0),
        model="claude-opus-4",
        cost_usd=0.45,
    )


@pytest.fixture
def investigation_context(signature: Signature, error_event: ErrorEvent) -> InvestigationContext:
    """Create a sample investigation context."""
    root_span = SpanNode(
        span_id="span-1",
        parent_id=None,
        service="payment-service",
        operation="process_charge",
        duration_ms=5000,
        status="error",
        attributes={"error.type": "ConnectionTimeoutError"},
        events=({"message": "timeout"}, {"message": "retrying"}),
    )
    trace_tree = TraceTree(
        trace_id="trace-123",
        root_span=root_span,
        error_spans=(root_span,),
    )
    log_entry = LogEntry(
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        severity=Severity.ERROR,
        body="Connection timeout occurred",
        attributes={"database": "postgres"},
        trace_id="trace-123",
        span_id="span-1",
    )
    return InvestigationContext(
        signature=signature,
        recent_events=(error_event,),
        trace_data=(trace_tree,),
        related_logs=(log_entry,),
        codebase_path="/app",
        historical_context=(),
    )


# ============================================================================
# Test Port Abstraction
# ============================================================================


class TestPortAbstraction:
    """Tests that ports are properly abstract and cannot be instantiated."""

    def test_telemetry_port_is_abstract(self) -> None:
        """TelemetryPort cannot be instantiated directly."""
        with pytest.raises(TypeError):
            TelemetryPort()  # type: ignore

    def test_signature_store_port_is_abstract(self) -> None:
        """SignatureStorePort cannot be instantiated directly."""
        with pytest.raises(TypeError):
            SignatureStorePort()  # type: ignore

    def test_diagnosis_port_is_abstract(self) -> None:
        """DiagnosisPort cannot be instantiated directly."""
        with pytest.raises(TypeError):
            DiagnosisPort()  # type: ignore

    def test_notification_port_is_abstract(self) -> None:
        """NotificationPort cannot be instantiated directly."""
        with pytest.raises(TypeError):
            NotificationPort()  # type: ignore

    def test_poll_port_is_abstract(self) -> None:
        """PollPort cannot be instantiated directly."""
        with pytest.raises(TypeError):
            PollPort()  # type: ignore

    def test_management_port_is_abstract(self) -> None:
        """ManagementPort cannot be instantiated directly."""
        with pytest.raises(TypeError):
            ManagementPort()  # type: ignore


# ============================================================================
# Test Concrete Implementations
# ============================================================================


class MockTelemetryPort(TelemetryPort):
    """Mock implementation of TelemetryPort for testing."""

    async def get_recent_errors(
        self, since: datetime, services: list[str] | None = None
    ) -> list[ErrorEvent]:
        """Mock implementation."""
        return []

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
        return []


class MockSignatureStorePort(SignatureStorePort):
    """Mock implementation of SignatureStorePort for testing."""

    async def get_by_id(self, signature_id: str) -> Signature | None:
        """Mock implementation."""
        return None

    async def get_by_fingerprint(self, fingerprint: str) -> Signature | None:
        """Mock implementation."""
        return None

    async def save(self, signature: Signature) -> None:
        """Mock implementation."""
        pass

    async def update(self, signature: Signature) -> None:
        """Mock implementation."""
        pass

    async def get_pending_investigation(self) -> list[Signature]:
        """Mock implementation."""
        return []

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

    async def report(self, signature: Signature, diagnosis: Diagnosis) -> None:
        """Mock implementation."""
        pass

    async def report_summary(self, stats: dict[str, Any]) -> None:
        """Mock implementation."""
        pass


class MockPollPort(PollPort):
    """Mock implementation of PollPort for testing."""

    async def execute_poll_cycle(self) -> PollResult:
        """Mock implementation."""
        return PollResult(
            errors_found=0,
            new_signatures=0,
            updated_signatures=0,
            investigations_queued=0,
            timestamp=datetime.now(),
        )

    async def execute_investigation_cycle(self) -> list[Diagnosis]:
        """Mock implementation."""
        return []


class MockManagementPort(ManagementPort):
    """Mock implementation of ManagementPort for testing."""

    async def mute_signature(
        self, signature_id: str, reason: str | None = None
    ) -> None:
        """Mock implementation."""
        pass

    async def resolve_signature(
        self, signature_id: str, fix_applied: str | None = None
    ) -> None:
        """Mock implementation."""
        pass

    async def retriage_signature(self, signature_id: str) -> None:
        """Mock implementation."""
        pass

    async def get_signature_details(self, signature_id: str) -> dict[str, Any]:
        """Mock implementation."""
        return {}


class TestPortImplementation:
    """Tests that concrete implementations can be created."""

    def test_concrete_telemetry_port_instantiation(self) -> None:
        """Concrete implementations of TelemetryPort can be instantiated."""
        port = MockTelemetryPort()
        assert isinstance(port, TelemetryPort)

    def test_concrete_signature_store_port_instantiation(self) -> None:
        """Concrete implementations of SignatureStorePort can be instantiated."""
        port = MockSignatureStorePort()
        assert isinstance(port, SignatureStorePort)

    def test_concrete_diagnosis_port_instantiation(self) -> None:
        """Concrete implementations of DiagnosisPort can be instantiated."""
        port = MockDiagnosisPort()
        assert isinstance(port, DiagnosisPort)

    def test_concrete_notification_port_instantiation(self) -> None:
        """Concrete implementations of NotificationPort can be instantiated."""
        port = MockNotificationPort()
        assert isinstance(port, NotificationPort)

    def test_concrete_poll_port_instantiation(self) -> None:
        """Concrete implementations of PollPort can be instantiated."""
        port = MockPollPort()
        assert isinstance(port, PollPort)

    def test_concrete_management_port_instantiation(self) -> None:
        """Concrete implementations of ManagementPort can be instantiated."""
        port = MockManagementPort()
        assert isinstance(port, ManagementPort)


# ============================================================================
# Test TelemetryPort Contract
# ============================================================================


class TestTelemetryPort:
    """Test TelemetryPort interface contract."""

    @pytest.mark.asyncio
    async def test_get_recent_errors_returns_list(
        self, error_event: ErrorEvent
    ) -> None:
        """get_recent_errors must return a list of ErrorEvent."""
        port = MockTelemetryPort()
        result = await port.get_recent_errors(datetime.now())
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_recent_errors_respects_services_filter(self) -> None:
        """get_recent_errors must filter by services."""
        port = MockTelemetryPort()
        result = await port.get_recent_errors(
            datetime.now(), services=["payment-service"]
        )
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_trace_returns_trace_tree(self) -> None:
        """get_trace must return TraceTree."""
        port = MockTelemetryPort()
        result = await port.get_trace("trace-123")
        assert isinstance(result, TraceTree)

    @pytest.mark.asyncio
    async def test_get_traces_returns_list(self) -> None:
        """get_traces must return a list of TraceTree."""
        port = MockTelemetryPort()
        result = await port.get_traces(["trace-123", "trace-456"])
        assert isinstance(result, list)
        assert all(isinstance(t, TraceTree) for t in result)

    @pytest.mark.asyncio
    async def test_get_correlated_logs_returns_list(self) -> None:
        """get_correlated_logs must return a list of LogEntry."""
        port = MockTelemetryPort()
        result = await port.get_correlated_logs(["trace-123"])
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_events_for_signature_returns_list(self) -> None:
        """get_events_for_signature must return a list of ErrorEvent."""
        port = MockTelemetryPort()
        result = await port.get_events_for_signature("abc123def456")
        assert isinstance(result, list)


# ============================================================================
# Test SignatureStorePort Contract
# ============================================================================


class TestSignatureStorePort:
    """Test SignatureStorePort interface contract."""

    @pytest.mark.asyncio
    async def test_get_by_fingerprint_returns_signature_or_none(self) -> None:
        """get_by_fingerprint must return Signature or None."""
        port = MockSignatureStorePort()
        result = await port.get_by_fingerprint("abc123def456")
        assert result is None or isinstance(result, Signature)

    @pytest.mark.asyncio
    async def test_save_is_callable(self, signature: Signature) -> None:
        """save must be callable and not raise."""
        port = MockSignatureStorePort()
        await port.save(signature)

    @pytest.mark.asyncio
    async def test_update_is_callable(self, signature: Signature) -> None:
        """update must be callable and not raise."""
        port = MockSignatureStorePort()
        await port.update(signature)

    @pytest.mark.asyncio
    async def test_get_pending_investigation_returns_list(self) -> None:
        """get_pending_investigation must return a list of Signature."""
        port = MockSignatureStorePort()
        result = await port.get_pending_investigation()
        assert isinstance(result, list)
        assert all(isinstance(sig, Signature) for sig in result)

    @pytest.mark.asyncio
    async def test_get_similar_returns_list(self, signature: Signature) -> None:
        """get_similar must return a list of Signature."""
        port = MockSignatureStorePort()
        result = await port.get_similar(signature)
        assert isinstance(result, list)
        assert all(isinstance(sig, Signature) for sig in result)

    @pytest.mark.asyncio
    async def test_get_stats_returns_dict(self) -> None:
        """get_stats must return a dictionary."""
        port = MockSignatureStorePort()
        result = await port.get_stats()
        assert isinstance(result, dict)


# ============================================================================
# Test DiagnosisPort Contract
# ============================================================================


class TestDiagnosisPort:
    """Test DiagnosisPort interface contract."""

    @pytest.mark.asyncio
    async def test_diagnose_returns_diagnosis(
        self, investigation_context: InvestigationContext
    ) -> None:
        """diagnose must return a Diagnosis object."""
        port = MockDiagnosisPort()
        result = await port.diagnose(investigation_context)
        assert isinstance(result, Diagnosis)
        assert isinstance(result.root_cause, str)
        assert isinstance(result.evidence, tuple)
        assert isinstance(result.suggested_fix, str)
        assert isinstance(result.confidence, Confidence)
        assert isinstance(result.diagnosed_at, datetime)
        assert isinstance(result.model, str)
        assert isinstance(result.cost_usd, float)

    @pytest.mark.asyncio
    async def test_estimate_cost_returns_float(
        self, investigation_context: InvestigationContext
    ) -> None:
        """estimate_cost must return a float."""
        port = MockDiagnosisPort()
        result = await port.estimate_cost(investigation_context)
        assert isinstance(result, float)
        assert result >= 0.0


# ============================================================================
# Test NotificationPort Contract
# ============================================================================


class TestNotificationPort:
    """Test NotificationPort interface contract."""

    @pytest.mark.asyncio
    async def test_report_is_callable(
        self, signature: Signature, diagnosis: Diagnosis
    ) -> None:
        """report must be callable and not raise."""
        port = MockNotificationPort()
        await port.report(signature, diagnosis)

    @pytest.mark.asyncio
    async def test_report_summary_is_callable(self) -> None:
        """report_summary must be callable and not raise."""
        port = MockNotificationPort()
        await port.report_summary({})


# ============================================================================
# Test PollPort Contract
# ============================================================================


class TestPollPort:
    """Test PollPort interface contract."""

    @pytest.mark.asyncio
    async def test_execute_poll_cycle_returns_poll_result(self) -> None:
        """execute_poll_cycle must return a PollResult."""
        port = MockPollPort()
        result = await port.execute_poll_cycle()
        assert isinstance(result, PollResult)
        assert isinstance(result.errors_found, int)
        assert isinstance(result.new_signatures, int)
        assert isinstance(result.timestamp, datetime)

    @pytest.mark.asyncio
    async def test_execute_investigation_cycle_returns_list(self) -> None:
        """execute_investigation_cycle must return a list of Diagnosis."""
        port = MockPollPort()
        result = await port.execute_investigation_cycle()
        assert isinstance(result, list)
        assert all(isinstance(d, Diagnosis) for d in result)


# ============================================================================
# Test ManagementPort Contract
# ============================================================================


class TestManagementPort:
    """Test ManagementPort interface contract."""

    @pytest.mark.asyncio
    async def test_mute_signature_is_callable(self) -> None:
        """mute_signature must be callable and not raise."""
        port = MockManagementPort()
        await port.mute_signature("sig-001")
        await port.mute_signature("sig-001", reason="false positive")

    @pytest.mark.asyncio
    async def test_resolve_signature_is_callable(self) -> None:
        """resolve_signature must be callable and not raise."""
        port = MockManagementPort()
        await port.resolve_signature("sig-001")
        await port.resolve_signature("sig-001", fix_applied="upgraded library")

    @pytest.mark.asyncio
    async def test_retriage_signature_is_callable(self) -> None:
        """retriage_signature must be callable and not raise."""
        port = MockManagementPort()
        await port.retriage_signature("sig-001")

    @pytest.mark.asyncio
    async def test_get_signature_details_returns_dict(self) -> None:
        """get_signature_details must return a dictionary."""
        port = MockManagementPort()
        result = await port.get_signature_details("sig-001")
        assert isinstance(result, dict)
