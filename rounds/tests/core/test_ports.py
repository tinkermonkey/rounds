"""Unit tests for port interface contracts.

Tests verify that port abstract base classes are properly defined
and that implementations must satisfy the interface contract.
"""

import pytest
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from pathlib import Path
import sys

# Add the rounds directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.models import (
    Confidence,
    Diagnosis,
    ErrorEvent,
    InvestigationContext,
    LogEntry,
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
        self,
        service: str | None = None,
        since_timestamp: datetime | None = None,
        limit: int = 100,
    ) -> list[ErrorEvent]:
        """Mock implementation."""
        return []

    async def get_trace(self, trace_id: str) -> TraceTree | None:
        """Mock implementation."""
        return None

    async def get_logs_for_trace(
        self, trace_id: str, limit: int = 50
    ) -> list[LogEntry]:
        """Mock implementation."""
        return []

    async def get_related_errors(
        self,
        error_type: str,
        service: str,
        since_timestamp: datetime,
        limit: int = 10,
    ) -> list[ErrorEvent]:
        """Mock implementation."""
        return []


class MockSignatureStorePort(SignatureStorePort):
    """Mock implementation of SignatureStorePort for testing."""

    async def create(self, signature: Signature) -> str:
        """Mock implementation."""
        return signature.id

    async def get_by_id(self, signature_id: str) -> Signature | None:
        """Mock implementation."""
        return None

    async def get_by_fingerprint(self, fingerprint: str) -> Signature | None:
        """Mock implementation."""
        return None

    async def update(self, signature: Signature) -> None:
        """Mock implementation."""
        pass

    async def query(
        self,
        service: str | None = None,
        status: SignatureStatus | None = None,
        error_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Signature], int]:
        """Mock implementation."""
        return ([], 0)

    async def delete(self, signature_id: str) -> None:
        """Mock implementation."""
        pass


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

    async def notify(self, signature: Signature, diagnosis: Diagnosis) -> None:
        """Mock implementation."""
        pass


class MockPollPort(PollPort):
    """Mock implementation of PollPort for testing."""

    async def poll_and_investigate(self) -> None:
        """Mock implementation."""
        pass

    async def get_poll_summary(self) -> dict[str, Any]:
        """Mock implementation."""
        return {}


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
        result = await port.get_recent_errors()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_recent_errors_respects_limit(self) -> None:
        """get_recent_errors must respect the limit parameter."""
        port = MockTelemetryPort()
        result = await port.get_recent_errors(limit=50)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_recent_errors_respects_service_filter(self) -> None:
        """get_recent_errors must filter by service."""
        port = MockTelemetryPort()
        result = await port.get_recent_errors(service="payment-service")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_trace_returns_trace_or_none(self) -> None:
        """get_trace must return TraceTree or None."""
        port = MockTelemetryPort()
        result = await port.get_trace("trace-123")
        assert result is None or isinstance(result, TraceTree)

    @pytest.mark.asyncio
    async def test_get_logs_for_trace_returns_list(self) -> None:
        """get_logs_for_trace must return a list of LogEntry."""
        port = MockTelemetryPort()
        result = await port.get_logs_for_trace("trace-123")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_related_errors_returns_list(self) -> None:
        """get_related_errors must return a list of ErrorEvent."""
        port = MockTelemetryPort()
        result = await port.get_related_errors(
            error_type="ConnectionTimeoutError",
            service="payment-service",
            since_timestamp=datetime.now(),
        )
        assert isinstance(result, list)


# ============================================================================
# Test SignatureStorePort Contract
# ============================================================================


class TestSignatureStorePort:
    """Test SignatureStorePort interface contract."""

    @pytest.mark.asyncio
    async def test_create_returns_id(self, signature: Signature) -> None:
        """create must return the signature ID."""
        port = MockSignatureStorePort()
        result = await port.create(signature)
        assert isinstance(result, str)
        assert result == signature.id

    @pytest.mark.asyncio
    async def test_get_by_id_returns_signature_or_none(
        self, signature: Signature
    ) -> None:
        """get_by_id must return Signature or None."""
        port = MockSignatureStorePort()
        result = await port.get_by_id("sig-001")
        assert result is None or isinstance(result, Signature)

    @pytest.mark.asyncio
    async def test_get_by_fingerprint_returns_signature_or_none(self) -> None:
        """get_by_fingerprint must return Signature or None."""
        port = MockSignatureStorePort()
        result = await port.get_by_fingerprint("abc123def456")
        assert result is None or isinstance(result, Signature)

    @pytest.mark.asyncio
    async def test_update_is_callable(self, signature: Signature) -> None:
        """update must be callable and not raise."""
        port = MockSignatureStorePort()
        await port.update(signature)

    @pytest.mark.asyncio
    async def test_query_returns_tuple(self) -> None:
        """query must return tuple of (signatures, total_count)."""
        port = MockSignatureStorePort()
        result = await port.query()
        assert isinstance(result, tuple)
        assert len(result) == 2
        sigs, count = result
        assert isinstance(sigs, list)
        assert isinstance(count, int)

    @pytest.mark.asyncio
    async def test_query_respects_filters(self) -> None:
        """query must accept filter parameters."""
        port = MockSignatureStorePort()
        result = await port.query(
            service="payment-service",
            status=SignatureStatus.NEW,
            error_type="ConnectionTimeoutError",
            limit=50,
            offset=0,
        )
        assert isinstance(result, tuple)

    @pytest.mark.asyncio
    async def test_delete_is_callable(self) -> None:
        """delete must be callable and not raise."""
        port = MockSignatureStorePort()
        await port.delete("sig-001")


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
    async def test_notify_is_callable(
        self, signature: Signature, diagnosis: Diagnosis
    ) -> None:
        """notify must be callable and not raise."""
        port = MockNotificationPort()
        await port.notify(signature, diagnosis)


# ============================================================================
# Test PollPort Contract
# ============================================================================


class TestPollPort:
    """Test PollPort interface contract."""

    @pytest.mark.asyncio
    async def test_poll_and_investigate_is_callable(self) -> None:
        """poll_and_investigate must be callable and not raise."""
        port = MockPollPort()
        await port.poll_and_investigate()

    @pytest.mark.asyncio
    async def test_get_poll_summary_returns_dict(self) -> None:
        """get_poll_summary must return a dictionary."""
        port = MockPollPort()
        result = await port.get_poll_summary()
        assert isinstance(result, dict)


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
