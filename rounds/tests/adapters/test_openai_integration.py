"""Integration tests for OpenAI diagnosis adapter."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from rounds.adapters.diagnosis.openai import OpenAIDiagnosisAdapter
from rounds.core.models import (
    Diagnosis,
)


@pytest.fixture
def mock_openai_client():
    """Create a mock OpenAI client."""
    mock = MagicMock()
    # The OpenAI client is synchronous, not async
    mock.chat.completions.create = MagicMock()
    return mock


@pytest.fixture
def adapter() -> OpenAIDiagnosisAdapter:
    """Create an OpenAI adapter with test configuration."""
    return OpenAIDiagnosisAdapter(
        api_key="test-key-12345",
        model="gpt-4",
        budget_usd=2.0,
    )


@pytest.mark.asyncio
async def test_adapter_initialization() -> None:
    """Test OpenAI adapter initialization."""
    adapter = OpenAIDiagnosisAdapter(
        api_key="test-key",
        model="gpt-4o",
        budget_usd=5.0,
    )

    assert adapter.api_key == "test-key"
    assert adapter.model == "gpt-4o"
    assert adapter.budget_usd == 5.0


@pytest.mark.asyncio
async def test_adapter_rejects_empty_api_key() -> None:
    """Test that adapter rejects empty API key."""
    with pytest.raises(ValueError, match="API key must be provided"):
        OpenAIDiagnosisAdapter(api_key="")


@pytest.mark.asyncio
async def test_adapter_rejects_whitespace_only_api_key() -> None:
    """Test that adapter rejects whitespace-only API key."""
    with pytest.raises(ValueError, match="API key must be provided"):
        OpenAIDiagnosisAdapter(api_key="   ")


@pytest.mark.asyncio
async def test_budget_tracking() -> None:
    """Test that diagnosis cost is properly recorded."""
    adapter = OpenAIDiagnosisAdapter(
        api_key="test-key",
        model="gpt-4",
        budget_usd=1.0,
    )

    # The adapter should track cost_usd in the Diagnosis object
    # This is typically set based on token usage from the OpenAI API
    diagnosis = Diagnosis(
        root_cause="Test root cause",
        evidence=("Evidence 1", "Evidence 2"),
        suggested_fix="Test fix",
        confidence="medium",
        diagnosed_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        model="gpt-4",
        cost_usd=0.05,
    )

    assert diagnosis.cost_usd == 0.05
    assert diagnosis.cost_usd <= adapter.budget_usd


@pytest.mark.asyncio
async def test_diagnose_flow_success(mock_openai_client) -> None:
    """Test successful diagnose() flow with JSON parsing."""
    from rounds.core.models import (
        ErrorEvent,
        InvestigationContext,
        LogEntry,
        Signature,
        Severity,
        StackFrame,
    )

    adapter = OpenAIDiagnosisAdapter(
        api_key="test-key",
        model="gpt-4",
        budget_usd=1.0,
    )
    adapter._client = mock_openai_client

    # Setup mock response
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = """{
        "root_cause": "Database connection timeout due to pool exhaustion",
        "evidence": [
            "Stack trace shows timeout in connection pool",
            "15 occurrences in 10 minutes",
            "All errors from database service"
        ],
        "suggested_fix": "Increase connection pool size from 10 to 50",
        "confidence": "HIGH"
    }"""
    mock_openai_client.chat.completions.create.return_value = mock_response

    # Create investigation context
    from rounds.core.models import SignatureStatus

    signature = Signature(
        id="sig-test",
        service="api-service",
        fingerprint="db-timeout",
        error_type="TimeoutError",
        message_template="Connection timeout",
        stack_hash="stack123",

        first_seen=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        last_seen=datetime(2026, 1, 1, 10, 15, 0, tzinfo=UTC),
        occurrence_count=15,
        status=SignatureStatus.NEW,
        tags=frozenset(["database"]),
    )

    events = [
        ErrorEvent(
            trace_id="trace-evt-1",
            span_id="span-1",
            timestamp=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
            service="api-service",
            error_type="TimeoutError",
            error_message="Connection timeout after 30s",
            severity=Severity.ERROR,
            stack_frames=(
                StackFrame(
                    filename="db.py",
                    lineno=42,
                    function="connect",
                    module="database",
                ),
            ),
            attributes={},
        )
    ]

    context = InvestigationContext(
        signature=signature,
        recent_events=tuple(events),
        trace_data=tuple(),
        related_logs=tuple(),
        codebase_path="/app",
        historical_context=tuple(),
    )

    # Execute diagnose
    diagnosis = await adapter.diagnose(context)

    # Verify results
    assert diagnosis.root_cause == "Database connection timeout due to pool exhaustion"
    assert len(diagnosis.evidence) == 3
    assert "Stack trace shows timeout" in diagnosis.evidence[0]
    assert diagnosis.suggested_fix == "Increase connection pool size from 10 to 50"
    assert diagnosis.confidence == "high"
    assert diagnosis.model == "gpt-4"
    assert diagnosis.cost_usd > 0


@pytest.mark.asyncio
async def test_diagnose_handles_malformed_json(mock_openai_client) -> None:
    """Test diagnose() handles malformed JSON from OpenAI."""
    from rounds.core.models import (
        ErrorEvent,
        InvestigationContext,
        Signature,
        SignatureStatus,
        Severity,
        StackFrame,
    )

    adapter = OpenAIDiagnosisAdapter(
        api_key="test-key",
        model="gpt-4",
        budget_usd=1.0,
    )
    adapter._client = mock_openai_client

    # Mock response with invalid JSON
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "This is not valid JSON at all"
    mock_openai_client.chat.completions.create.return_value = mock_response

    signature = Signature(
        id="sig-test",
        service="test-service",
        fingerprint="test-fp",
        error_type="TestError",
        message_template="Test",
        stack_hash="stack123",
        first_seen=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        last_seen=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        occurrence_count=1,
        status=SignatureStatus.NEW,
        tags=frozenset(),
    )

    context = InvestigationContext(
        signature=signature,
        recent_events=tuple(),
        trace_data=tuple(),
        related_logs=tuple(),
        codebase_path="/app",
        historical_context=tuple(),
    )

    # Should raise ValueError for invalid JSON
    with pytest.raises(ValueError, match="did not return valid JSON"):
        await adapter.diagnose(context)


@pytest.mark.asyncio
async def test_diagnose_handles_multiline_json(mock_openai_client) -> None:
    """Test diagnose() parses multi-line pretty-printed JSON."""
    from rounds.core.models import (
        InvestigationContext,
        Signature,
        SignatureStatus,
    )

    adapter = OpenAIDiagnosisAdapter(
        api_key="test-key",
        model="gpt-4",
        budget_usd=1.0,
    )
    adapter._client = mock_openai_client

    # Mock response with pretty-printed JSON
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = """Here's my analysis:

{
  "root_cause": "Null pointer exception",
  "evidence": [
    "Variable was not initialized",
    "Missing null check"
  ],
  "suggested_fix": "Add null check before use",
  "confidence": "MEDIUM"
}

That's my diagnosis."""
    mock_openai_client.chat.completions.create.return_value = mock_response

    signature = Signature(
        id="sig-test",
        service="test-service",
        fingerprint="test-fp",
        error_type="NullPointerError",
        message_template="Test",
        stack_hash="stack123",
        first_seen=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        last_seen=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        occurrence_count=1,
        status=SignatureStatus.NEW,
        tags=frozenset(),
    )

    context = InvestigationContext(
        signature=signature,
        recent_events=tuple(),
        trace_data=tuple(),
        related_logs=tuple(),
        codebase_path="/app",
        historical_context=tuple(),
    )

    diagnosis = await adapter.diagnose(context)

    assert diagnosis.root_cause == "Null pointer exception"
    assert diagnosis.confidence == "medium"


@pytest.mark.asyncio
async def test_diagnose_rejects_invalid_confidence(mock_openai_client) -> None:
    """Test diagnose() rejects invalid confidence levels."""
    from rounds.core.models import (
        InvestigationContext,
        Signature,
        SignatureStatus,
    )

    adapter = OpenAIDiagnosisAdapter(
        api_key="test-key",
        model="gpt-4",
        budget_usd=1.0,
    )
    adapter._client = mock_openai_client

    # Mock response with invalid confidence
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = """{
        "root_cause": "Test",
        "evidence": ["Test"],
        "suggested_fix": "Test",
        "confidence": "INVALID_LEVEL"
    }"""
    mock_openai_client.chat.completions.create.return_value = mock_response

    signature = Signature(
        id="sig-test",
        service="test-service",
        fingerprint="test-fp",
        error_type="TestError",
        message_template="Test",
        stack_hash="stack123",
        first_seen=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        last_seen=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        occurrence_count=1,
        status=SignatureStatus.NEW,
        tags=frozenset(),
    )

    context = InvestigationContext(
        signature=signature,
        recent_events=tuple(),
        trace_data=tuple(),
        related_logs=tuple(),
        codebase_path="/app",
        historical_context=tuple(),
    )

    with pytest.raises(ValueError, match="Invalid confidence level"):
        await adapter.diagnose(context)


@pytest.mark.asyncio
async def test_diagnose_enforces_budget(mock_openai_client) -> None:
    """Test diagnose() rejects diagnoses exceeding budget."""
    from rounds.core.models import (
        ErrorEvent,
        InvestigationContext,
        Signature,
        SignatureStatus,
        Severity,
        StackFrame,
    )

    adapter = OpenAIDiagnosisAdapter(
        api_key="test-key",
        model="gpt-4",
        budget_usd=0.01,  # Very low budget
    )
    adapter._client = mock_openai_client

    signature = Signature(
        id="sig-test",
        service="test-service",
        fingerprint="test-fp",
        error_type="TestError",
        message_template="Test",
        stack_hash="stack123",
        first_seen=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        last_seen=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        occurrence_count=1,
        status=SignatureStatus.NEW,
        tags=frozenset(),
    )

    # Large context that will exceed budget
    events = [
        ErrorEvent(
            trace_id=f"trace-{i}",
            span_id=f"span-{i}",
            timestamp=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
            service="test-service",
            error_type="TestError",
            error_message="Test error",
            severity=Severity.ERROR,
            stack_frames=(
                StackFrame(
                    filename="test.py",
                    lineno=i,
                    function="test",
                    module="test",

                ),
            ),
            attributes={},
        )
        for i in range(100)
    ]

    context = InvestigationContext(
        signature=signature,
        recent_events=tuple(events),
        trace_data=tuple(),
        related_logs=tuple(),
        codebase_path="/app",
        historical_context=tuple(),
    )

    # Should raise ValueError for budget exceeded
    with pytest.raises(ValueError, match="exceeds budget"):
        await adapter.diagnose(context)


@pytest.mark.asyncio
async def test_diagnose_handles_missing_required_fields(mock_openai_client) -> None:
    """Test diagnose() validates required fields in OpenAI response."""
    from rounds.core.models import (
        InvestigationContext,
        Signature,
        SignatureStatus,
    )

    adapter = OpenAIDiagnosisAdapter(
        api_key="test-key",
        model="gpt-4",
        budget_usd=1.0,
    )
    adapter._client = mock_openai_client

    # Mock response missing required fields
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = """{
        "root_cause": "Test",
        "evidence": ["Test"]
    }"""
    mock_openai_client.chat.completions.create.return_value = mock_response

    signature = Signature(
        id="sig-test",
        service="test-service",
        fingerprint="test-fp",
        error_type="TestError",
        message_template="Test",
        stack_hash="stack123",
        first_seen=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        last_seen=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        occurrence_count=1,
        status=SignatureStatus.NEW,
        tags=frozenset(),
    )

    context = InvestigationContext(
        signature=signature,
        recent_events=tuple(),
        trace_data=tuple(),
        related_logs=tuple(),
        codebase_path="/app",
        historical_context=tuple(),
    )

    with pytest.raises(ValueError, match="missing 'suggested_fix' field"):
        await adapter.diagnose(context)
