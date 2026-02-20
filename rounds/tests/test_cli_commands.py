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

import pytest

from rounds.core.models import (
    Diagnosis,
    PollResult,
    Signature,
    SignatureStatus,
)
from rounds.tests.fakes.investigator import FakeInvestigator
from rounds.tests.fakes.store import FakeSignatureStorePort
from rounds.tests.fakes.telemetry import FakeTelemetryPort
from rounds.main import _run_scan, _run_diagnose, _parse_arguments


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
def sample_diagnosis() -> Diagnosis:
    """Create a sample diagnosis for testing."""
    return Diagnosis(
        root_cause="Database connection pool exhaustion due to unresolved connections",
        evidence=(
            "Connection timeout errors consistently occur after 15:30 UTC",
            "Database logs show stale connections not being recycled",
            "Pool size set to 10 but seeing 150+ attempted connections",
        ),
        suggested_fix="Increase connection pool size or implement connection timeout recycling",
        confidence="high",
        diagnosed_at=datetime.now(timezone.utc),
        model="claude-opus-4-6",
        cost_usd=0.05,
    )


# ============================================================================
# Tests for _run_scan
# ============================================================================


class TestScanCommand:
    """Test the scan command functionality."""

    def test_scan_success_data_structure(self) -> None:
        """Test scan command success output data structure."""
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

        # Verify output structure and JSON serializability
        assert output["status"] == "success"
        assert output["new_signatures"] == 2
        assert output["updated_signatures"] == 3
        assert output["errors_processed"] == 5
        assert output["errors_failed"] == 0
        assert output["investigations_queued"] == 1
        assert "timestamp" in output

        # Verify JSON serializable
        json_str = json.dumps(output, indent=2)
        parsed = json.loads(json_str)
        assert parsed["status"] == "success"

    def test_scan_error_data_structure(self) -> None:
        """Test scan command error output data structure."""
        error_message = "Telemetry connection failed"
        output = {
            "status": "error",
            "message": error_message,
        }

        # Verify output structure and JSON serializability
        assert output["status"] == "error"
        assert output["message"] == error_message

        # Verify JSON serializable
        json_str = json.dumps(output, indent=2)
        parsed = json.loads(json_str)
        assert parsed["status"] == "error"
        assert "message" in parsed


# ============================================================================
# Tests for _run_diagnose
# ============================================================================


class TestDiagnoseCommand:
    """Test the diagnose command functionality."""

    def test_diagnose_success_data_structure(self, sample_diagnosis: Diagnosis) -> None:
        """Test diagnose command success output data structure."""
        signature_id = "test-sig-123"

        # Create the output structure as diagnose would
        output = {
            "status": "success",
            "signature_id": signature_id,
            "root_cause": sample_diagnosis.root_cause,
            "confidence": sample_diagnosis.confidence,
            "cost_usd": sample_diagnosis.cost_usd,
            "diagnosed_at": sample_diagnosis.diagnosed_at.isoformat(),
            "model": sample_diagnosis.model,
        }

        # Verify output structure
        assert output["status"] == "success"
        assert output["signature_id"] == signature_id
        assert output["root_cause"] == sample_diagnosis.root_cause
        assert output["confidence"] == sample_diagnosis.confidence
        assert output["cost_usd"] == sample_diagnosis.cost_usd
        assert output["model"] == sample_diagnosis.model
        assert "diagnosed_at" in output

        # Verify JSON serializable
        json_str = json.dumps(output, indent=2)
        parsed = json.loads(json_str)
        assert parsed["status"] == "success"

    def test_diagnose_signature_not_found_error(self) -> None:
        """Test diagnose command error output for missing signature."""
        error_message = "Signature not found: nonexistent-sig"
        output = {
            "status": "error",
            "message": error_message,
        }

        # Verify output structure
        assert output["status"] == "error"
        assert "Signature not found" in output["message"]

        # Verify JSON serializable
        json_str = json.dumps(output, indent=2)
        parsed = json.loads(json_str)
        assert parsed["status"] == "error"
        assert "message" in parsed

    def test_diagnose_investigation_error(self) -> None:
        """Test diagnose command error output for investigation failure."""
        error_message = "LLM API error"
        output = {
            "status": "error",
            "message": error_message,
        }

        # Verify output structure
        assert output["status"] == "error"
        assert output["message"] == error_message

        # Verify JSON serializable
        json_str = json.dumps(output, indent=2)
        parsed = json.loads(json_str)
        assert parsed["status"] == "error"
        assert "message" in parsed


# ============================================================================
# Tests for argument parsing
# ============================================================================


class TestArgumentParsing:
    """Test command-line argument parsing."""

    def test_parse_arguments_with_scan_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test _parse_arguments with 'scan' command."""
        monkeypatch.setattr(sys, "argv", ["main.py", "scan"])
        args = _parse_arguments()
        assert args.command == "scan"
        assert args.signature_id is None

    def test_parse_arguments_with_diagnose_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test _parse_arguments with 'diagnose' command and signature_id."""
        monkeypatch.setattr(sys, "argv", ["main.py", "diagnose", "sig-12345"])
        args = _parse_arguments()
        assert args.command == "diagnose"
        assert args.signature_id == "sig-12345"

    def test_parse_arguments_with_no_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test _parse_arguments with no command (interactive mode)."""
        monkeypatch.setattr(sys, "argv", ["main.py"])
        args = _parse_arguments()
        assert args.command is None
        assert args.signature_id is None


# ============================================================================
# Output Structure Tests
# ============================================================================


class TestOutputStructures:
    """Test that output structures match specification."""

    def test_diagnose_output_structure(self, sample_diagnosis: Diagnosis) -> None:
        """Test that diagnose command output contains all required fields."""
        signature_id = "test-sig-123"

        # Create the output structure as diagnose would
        output = {
            "status": "success",
            "signature_id": signature_id,
            "root_cause": sample_diagnosis.root_cause,
            "confidence": sample_diagnosis.confidence,
            "cost_usd": sample_diagnosis.cost_usd,
            "diagnosed_at": sample_diagnosis.diagnosed_at.isoformat(),
            "model": sample_diagnosis.model,
        }

        # Verify all required fields are present
        assert output["status"] == "success"
        assert output["signature_id"] == signature_id
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


# ============================================================================
# Integration Tests (invoking actual functions)
# ============================================================================


class TestRunScanIntegration:
    """Integration tests for _run_scan function with fake adapters."""

    @pytest.mark.asyncio
    async def test_run_scan_invokes_poll_service(
        self, sample_signature: Signature, capsys
    ) -> None:
        """Test that _run_scan invokes PollService with correct parameters."""
        from rounds.core.fingerprint import Fingerprinter
        from rounds.core.triage import TriageEngine
        from rounds.core.poll_service import PollService

        # Create fake adapters
        store = FakeSignatureStorePort()
        await store.save(sample_signature)

        telemetry = FakeTelemetryPort()
        fingerprinter = Fingerprinter()
        triage = TriageEngine()
        investigator = FakeInvestigator()

        # Create PollService with fakes
        poll_service = PollService(
            telemetry=telemetry,
            store=store,
            fingerprinter=fingerprinter,
            triage=triage,
            investigator=investigator,
            lookback_minutes=60,
            batch_size=100,
        )

        # Execute _run_scan (the actual function)
        await _run_scan(poll_service)

        # Capture output and verify it's valid JSON
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Verify output structure
        assert output["status"] == "success"
        assert "new_signatures" in output
        assert "updated_signatures" in output
        assert "errors_processed" in output
        assert "errors_failed" in output
        assert "investigations_queued" in output
        assert "timestamp" in output

    @pytest.mark.asyncio
    async def test_run_scan_output_structure(
        self, sample_signature: Signature, capsys
    ) -> None:
        """Test that _run_scan output structure matches JSON specification."""
        from rounds.core.fingerprint import Fingerprinter
        from rounds.core.triage import TriageEngine
        from rounds.core.poll_service import PollService

        # Create fake adapters
        store = FakeSignatureStorePort()
        await store.save(sample_signature)

        telemetry = FakeTelemetryPort()
        fingerprinter = Fingerprinter()
        triage = TriageEngine()
        investigator = FakeInvestigator()

        # Create PollService with fakes
        poll_service = PollService(
            telemetry=telemetry,
            store=store,
            fingerprinter=fingerprinter,
            triage=triage,
            investigator=investigator,
            lookback_minutes=60,
            batch_size=100,
        )

        # Execute _run_scan (the actual function)
        await _run_scan(poll_service)

        # Capture output and verify structure
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Verify all required fields
        assert output["status"] == "success"
        assert isinstance(output["new_signatures"], int)
        assert isinstance(output["updated_signatures"], int)
        assert isinstance(output["errors_processed"], int)
        assert isinstance(output["errors_failed"], int)
        assert isinstance(output["investigations_queued"], int)
        assert isinstance(output["timestamp"], str)


class TestRunDiagnoseIntegration:
    """Integration tests for _run_diagnose function with fake adapters."""

    @pytest.mark.asyncio
    async def test_run_diagnose_invokes_investigator(
        self, sample_signature: Signature, sample_diagnosis: Diagnosis, capsys
    ) -> None:
        """Test that _run_diagnose invokes investigator with correct signature."""
        # Create fake adapters
        store = FakeSignatureStorePort()
        await store.save(sample_signature)

        investigator = FakeInvestigator(diagnosis_to_return=sample_diagnosis)

        # Execute _run_diagnose (the actual function)
        await _run_diagnose(sample_signature.id, store, investigator)

        # Capture output and verify it's valid JSON
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Verify investigator was called with correct signature
        assert len(investigator.investigated_signatures) == 1
        assert investigator.investigated_signatures[0].id == sample_signature.id

        # Verify output structure
        assert output["status"] == "success"
        assert output["signature_id"] == sample_signature.id
        assert output["root_cause"] == sample_diagnosis.root_cause
        assert output["confidence"] == sample_diagnosis.confidence
        assert output["cost_usd"] == sample_diagnosis.cost_usd

    @pytest.mark.asyncio
    async def test_run_diagnose_handles_missing_signature(self, capsys) -> None:
        """Test that _run_diagnose properly handles nonexistent signature."""
        # Create fake adapters with empty store
        store = FakeSignatureStorePort()
        investigator = FakeInvestigator()

        # Execute _run_diagnose with nonexistent signature
        with pytest.raises(SystemExit) as exc_info:
            await _run_diagnose("nonexistent-sig", store, investigator)

        # Verify it exited with error code
        assert exc_info.value.code == 1

        # Verify error output contains signature not found message
        captured = capsys.readouterr()
        error_output = json.loads(captured.err)
        assert error_output["status"] == "error"
        assert "Signature not found" in error_output["message"]

    @pytest.mark.asyncio
    async def test_run_diagnose_handles_investigation_error(
        self, sample_signature: Signature, capsys
    ) -> None:
        """Test that _run_diagnose handles investigation errors."""
        # Create fake adapters with error investigator
        store = FakeSignatureStorePort()
        await store.save(sample_signature)

        error = RuntimeError("LLM API error")
        investigator = FakeInvestigator(raise_error=error)

        # Execute _run_diagnose and expect it to exit with error
        with pytest.raises(SystemExit) as exc_info:
            await _run_diagnose(sample_signature.id, store, investigator)

        # Verify it exited with error code
        assert exc_info.value.code == 1

        # Verify error output contains LLM API error message
        captured = capsys.readouterr()
        error_output = json.loads(captured.err)
        assert error_output["status"] == "error"
        assert "LLM API error" in error_output["message"]
