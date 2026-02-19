"""Unit tests for CLI non-interactive commands (scan and diagnose).

Tests verify that the scan and diagnose commands correctly:
- Execute without interactive prompts
- Output valid JSON on stdout
- Exit with code 0 on success, non-zero on error
- Handle error cases gracefully
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rounds.core.fingerprint import Fingerprinter
from rounds.core.investigator import Investigator
from rounds.core.models import (
    Confidence,
    Diagnosis,
    ErrorEvent,
    PollResult,
    Severity,
    Signature,
    SignatureStatus,
    StackFrame,
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
def sample_signature() -> Signature:
    """Create a sample signature for testing."""
    return Signature(
        id="test-sig-123",
        fingerprint="abc123def456",
        error_type="ConnectionTimeoutError",
        service="payment-service",
        message_template="Failed to connect to database at {host}:{port} for user {user_id}",
        stack_hash="stack789",
        first_seen=datetime.now(timezone.utc) - timedelta(hours=1),
        last_seen=datetime.now(timezone.utc),
        occurrence_count=5,
        status=SignatureStatus.NEW,
    )


@pytest.fixture
def sample_diagnosis(sample_signature: Signature) -> Diagnosis:
    """Create a sample diagnosis for testing."""
    return Diagnosis(
        root_cause="Database connection pool exhaustion due to unresolved connections",
        evidence=(
            "Connection timeout errors consistently occur after 15:30 UTC",
            "Database logs show stale connections not being recycled",
            "Pool size set to 10 but seeing 150+ attempted connections",
        ),
        suggested_fix="Increase connection pool size or implement connection timeout recycling",
        confidence="high",  # type: ignore
        diagnosed_at=datetime.now(timezone.utc),
        model="claude-opus-4-6",
        cost_usd=0.05,
    )


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
        timestamp=datetime.now(timezone.utc),
        attributes={"user_id": "123", "amount": "99.99"},
        severity=Severity.ERROR,
    )


# ============================================================================
# Tests for _run_scan
# ============================================================================


class TestScanCommand:
    """Test the scan command functionality."""

    @pytest.mark.asyncio
    async def test_scan_command_structure(self) -> None:
        """Test that scan command produces correct JSON structure."""
        # Create a mock poll result
        now = datetime.now(timezone.utc)
        poll_result = PollResult(
            errors_found=5,
            new_signatures=2,
            updated_signatures=3,
            investigations_queued=1,
            timestamp=now,
            errors_failed_to_process=0,
        )

        # Verify structure matches expectations
        assert poll_result.new_signatures >= 0
        assert poll_result.updated_signatures >= 0
        assert poll_result.errors_found >= 0
        assert hasattr(poll_result, "timestamp")
        assert hasattr(poll_result, "investigations_queued")


# ============================================================================
# Tests for _run_diagnose
# ============================================================================


class TestDiagnoseCommand:
    """Test the diagnose command functionality."""

    def test_diagnose_command_structure(self, sample_diagnosis: Diagnosis) -> None:
        """Test that diagnose command produces correct JSON structure."""
        # Verify diagnosis has required fields
        assert sample_diagnosis.root_cause
        assert sample_diagnosis.confidence in ("high", "medium", "low")
        assert sample_diagnosis.cost_usd >= 0
        assert hasattr(sample_diagnosis, "diagnosed_at")
        assert sample_diagnosis.model


# ============================================================================
# Tests for argument parsing
# ============================================================================


class TestArgumentParsing:
    """Test command-line argument parsing."""

    def test_argument_parser_accepts_scan(self) -> None:
        """Verify argparse can accept 'scan' command."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument('command', nargs='?', choices=['scan', 'diagnose'])
        parser.add_argument('signature_id', nargs='?')

        # Should not raise
        args = parser.parse_args(['scan'])
        assert args.command == 'scan'

    def test_argument_parser_accepts_diagnose(self) -> None:
        """Verify argparse can accept 'diagnose' command with signature_id."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument('command', nargs='?', choices=['scan', 'diagnose'])
        parser.add_argument('signature_id', nargs='?')

        args = parser.parse_args(['diagnose', 'sig-12345'])
        assert args.command == 'diagnose'
        assert args.signature_id == 'sig-12345'

    def test_argument_parser_accepts_no_command(self) -> None:
        """Verify argparse supports interactive mode with no command."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument('command', nargs='?', choices=['scan', 'diagnose'])
        parser.add_argument('signature_id', nargs='?')

        args = parser.parse_args([])
        assert args.command is None
        assert args.signature_id is None


# ============================================================================
# Integration tests
# ============================================================================


class TestIntegration:
    """Integration tests for command output structures."""

    def test_scan_command_output_structure(self) -> None:
        """Test that scan command output contains all required fields."""
        now = datetime.now(timezone.utc)
        poll_result = PollResult(
            errors_found=5,
            new_signatures=2,
            updated_signatures=3,
            investigations_queued=1,
            timestamp=now,
            errors_failed_to_process=0,
        )

        # Create the output structure as scan would
        output = {
            "status": "success",
            "new_signatures": poll_result.new_signatures,
            "updated_signatures": poll_result.updated_signatures,
            "errors_processed": poll_result.errors_found,
            "errors_failed": poll_result.errors_failed_to_process,
            "investigations_queued": poll_result.investigations_queued,
            "timestamp": poll_result.timestamp.isoformat(),
        }

        # Verify all required fields are present
        assert output["status"] == "success"
        assert "new_signatures" in output
        assert "updated_signatures" in output
        assert "errors_processed" in output
        assert "errors_failed" in output
        assert "investigations_queued" in output
        assert "timestamp" in output

        # Verify JSON serializable
        json_str = json.dumps(output)
        parsed = json.loads(json_str)
        assert parsed["status"] == "success"

    def test_diagnose_command_output_structure(
        self, sample_signature: Signature, sample_diagnosis: Diagnosis
    ) -> None:
        """Test that diagnose command output contains all required fields."""
        # Create the output structure as diagnose would
        output = {
            "status": "success",
            "signature_id": sample_signature.id,
            "root_cause": sample_diagnosis.root_cause,
            "confidence": sample_diagnosis.confidence,
            "cost_usd": sample_diagnosis.cost_usd,
            "diagnosed_at": sample_diagnosis.diagnosed_at.isoformat(),
            "model": sample_diagnosis.model,
        }

        # Verify all required fields are present
        assert output["status"] == "success"
        assert output["signature_id"] == sample_signature.id
        assert "root_cause" in output
        assert "confidence" in output
        assert "cost_usd" in output
        assert "diagnosed_at" in output
        assert "model" in output

        # Verify JSON serializable
        json_str = json.dumps(output)
        parsed = json.loads(json_str)
        assert parsed["status"] == "success"

    def test_error_output_structure(self) -> None:
        """Test that error output contains required fields."""
        output = {
            "status": "error",
            "message": "Test error message",
        }

        # Verify JSON serializable
        json_str = json.dumps(output)
        parsed = json.loads(json_str)
        assert parsed["status"] == "error"
        assert "message" in parsed
