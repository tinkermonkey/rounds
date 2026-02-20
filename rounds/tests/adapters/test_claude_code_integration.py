"""Integration tests for Claude Code diagnosis adapter."""

from datetime import UTC, datetime

import pytest

from rounds.adapters.diagnosis.claude_code import ClaudeCodeDiagnosisAdapter
from rounds.core.models import (
    ErrorEvent,
    InvestigationContext,
    Severity,
    Signature,
    SignatureStatus,
    StackFrame,
)


@pytest.fixture
def adapter() -> ClaudeCodeDiagnosisAdapter:
    """Create a Claude Code adapter with test configuration."""
    return ClaudeCodeDiagnosisAdapter(
        model="claude-opus",
        budget_usd=1.0,
    )


@pytest.fixture
def investigation_context() -> InvestigationContext:
    """Create a sample investigation context for testing."""
    error_event = ErrorEvent(
        trace_id="trace-001",
        span_id="span-001",
        service="api-service",
        error_type="TimeoutError",
        error_message="Request timed out after 30 seconds",
        stack_frames=(
            StackFrame(
                module="api.handler",
                function="process_request",
                filename="handler.py",
                lineno=42,
            ),
        ),
        timestamp=datetime.now(UTC),
        attributes={},
        severity=Severity.ERROR,
    )

    signature = Signature(
        id="sig-001",
        fingerprint="fp-001",
        error_type="TimeoutError",
        service="api-service",
        message_template="Request timed out",
        stack_hash="stack-001",
        first_seen=datetime.now(UTC),
        last_seen=datetime.now(UTC),
        occurrence_count=5,
        status=SignatureStatus.NEW,
    )

    return InvestigationContext(
        signature=signature,
        recent_events=(error_event,),
        trace_data=(),
        related_logs=(),
        codebase_path=".",
        historical_context=(),
    )


@pytest.mark.asyncio
async def test_adapter_initialization() -> None:
    """Test Claude Code adapter initialization."""
    adapter = ClaudeCodeDiagnosisAdapter(
        model="claude-sonnet",
        budget_usd=2.5,
    )

    assert adapter.model == "claude-sonnet"
    assert adapter.budget_usd == 2.5


@pytest.mark.asyncio
async def test_cost_estimation(
    adapter: ClaudeCodeDiagnosisAdapter,
    investigation_context: InvestigationContext,
) -> None:
    """Test that cost estimation works correctly."""
    cost = await adapter.estimate_cost(investigation_context)

    # Should have base cost of $0.30
    assert cost >= 0.30
    # Should be reasonable (within budget)
    assert cost <= adapter.budget_usd


@pytest.mark.asyncio
async def test_budget_exceeded_raises_error(
    investigation_context: InvestigationContext,
) -> None:
    """Test that exceeding budget raises ValueError."""
    # Create adapter with very low budget
    adapter = ClaudeCodeDiagnosisAdapter(
        model="claude-opus",
        budget_usd=0.05,  # Very low budget that will be exceeded
    )

    # Attempt to diagnose should raise ValueError due to budget
    with pytest.raises(ValueError, match="exceeds budget"):
        await adapter.diagnose(investigation_context)
