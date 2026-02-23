"""Unit tests for StdoutNotificationAdapter."""

import asyncio
from datetime import datetime, timezone
from io import StringIO
import sys

import pytest

from rounds.adapters.notification.stdout import StdoutNotificationAdapter
from rounds.core.models import Diagnosis, Signature, Severity


@pytest.fixture
def sample_signature():
    """Create a sample signature for testing."""
    from rounds.core.models import SignatureStatus

    return Signature(
        id="sig-001",
        service="test-service",
        fingerprint="error-fingerprint-001",
        error_type="ValueError",
        message_template="Invalid input: {value}",
        stack_hash="abc123def456",
        first_seen=datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        last_seen=datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        occurrence_count=5,
        status=SignatureStatus.NEW,
        tags=["test", "critical"],
    )


@pytest.fixture
def sample_diagnosis():
    """Create a sample diagnosis for testing."""
    return Diagnosis(
        root_cause="Invalid input",
        evidence=("No validation present", "Type check missing"),
        suggested_fix="Add validation",
        confidence="high",
        model="claude-3-5-sonnet-20241022",
        cost_usd=0.05,
        diagnosed_at=datetime(2026, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_send_basic_output(sample_signature, sample_diagnosis):
    """Test basic output formatting and structure."""
    adapter = StdoutNotificationAdapter()

    # Capture stdout
    captured_output = StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured_output

    try:
        await adapter.report(sample_signature, sample_diagnosis)
        output = captured_output.getvalue()

        # Verify key information is present
        assert "test-service" in output
        assert "ValueError" in output
        assert "HIGH" in output or "high" in output
        assert "Invalid input" in output
        assert "Add validation" in output
        assert "claude-3-5-sonnet-20241022" in output
        assert "DIAGNOSIS REPORT" in output
        assert "ROOT CAUSE" in output
        assert "EVIDENCE" in output

    finally:
        sys.stdout = old_stdout


@pytest.mark.asyncio
async def test_send_with_empty_tags(sample_signature, sample_diagnosis):
    """Test output with signature that has no tags."""
    from rounds.core.models import SignatureStatus

    signature_no_tags = Signature(
        id="sig-002",
        service="test-service",
        fingerprint="error-fingerprint-002",
        error_type="RuntimeError",
        message_template="Runtime error occurred",
        stack_hash="xyz789",
        first_seen=datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        last_seen=datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        occurrence_count=1,
        status=SignatureStatus.NEW,
        tags=[],
    )

    adapter = StdoutNotificationAdapter()

    captured_output = StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured_output

    try:
        await adapter.report(signature_no_tags, sample_diagnosis)
        output = captured_output.getvalue()

        # Should still produce valid output
        assert "RuntimeError" in output
        assert "DIAGNOSIS REPORT" in output

    finally:
        sys.stdout = old_stdout


@pytest.mark.asyncio
async def test_send_with_low_confidence(sample_signature):
    """Test output with low confidence diagnosis."""
    low_confidence_diagnosis = Diagnosis(
        root_cause="Unknown",
        evidence=("Insufficient data",),
        suggested_fix="More investigation needed",
        model="claude-3-5-sonnet-20241022",
        confidence="low",
        cost_usd=0.02,
        diagnosed_at=datetime(2026, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
    )

    adapter = StdoutNotificationAdapter()

    captured_output = StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured_output

    try:
        await adapter.report(sample_signature, low_confidence_diagnosis)
        output = captured_output.getvalue()

        assert "low" in output.lower()
        assert "Unknown" in output

    finally:
        sys.stdout = old_stdout


@pytest.mark.asyncio
async def test_send_with_complex_json(sample_signature):
    """Test output with complex nested diagnosis."""
    complex_diagnosis = Diagnosis(
        root_cause="DB timeout - Connection pool exhausted",
        evidence=("Pool size: 10", "Peak connections: 15", "Timeout after 30s"),
        suggested_fix="Increase pool size from 10 to 50",
        model="claude-3-5-sonnet-20241022",
        confidence="medium",
        cost_usd=0.08,
        diagnosed_at=datetime(2026, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
    )

    adapter = StdoutNotificationAdapter()

    captured_output = StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured_output

    try:
        await adapter.report(sample_signature, complex_diagnosis)
        output = captured_output.getvalue()

        # Should handle complex diagnosis gracefully
        assert "DB timeout" in output
        assert "Increase pool size" in output

    finally:
        sys.stdout = old_stdout


@pytest.mark.asyncio
async def test_send_with_special_characters(sample_diagnosis):
    """Test output with special characters in signature fields."""
    from rounds.core.models import SignatureStatus

    special_signature = Signature(
        id="sig-003",
        service="test-service-with-dashes",
        fingerprint="error-with-unicode-λ-test",
        error_type="ValueError: Can't parse 'foo'",
        message_template="Parse error: {input}",
        stack_hash="special123",
        first_seen=datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        last_seen=datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        occurrence_count=3,
        status=SignatureStatus.NEW,
        tags=["tag-with-dashes", "unicode-test-λ"],
    )

    adapter = StdoutNotificationAdapter()

    captured_output = StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured_output

    try:
        await adapter.report(special_signature, sample_diagnosis)
        output = captured_output.getvalue()

        # Should handle special characters without errors
        assert "test-service-with-dashes" in output
        assert "ValueError" in output

    finally:
        sys.stdout = old_stdout


@pytest.mark.asyncio
async def test_send_high_occurrence_count(sample_diagnosis):
    """Test output with very high occurrence count."""
    from rounds.core.models import SignatureStatus

    high_count_signature = Signature(
        id="sig-004",
        service="test-service",
        fingerprint="frequent-error",
        error_type="TimeoutError",
        message_template="Operation timed out",
        stack_hash="timeout999",
        first_seen=datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        last_seen=datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        occurrence_count=9999,
        status=SignatureStatus.NEW,
        tags=["critical"],
    )

    adapter = StdoutNotificationAdapter()

    captured_output = StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured_output

    try:
        await adapter.report(high_count_signature, sample_diagnosis)
        output = captured_output.getvalue()

        assert "9999" in output or "9,999" in output

    finally:
        sys.stdout = old_stdout
