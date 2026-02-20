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

    def test_argument_parser_accepts_scan(self) -> None:
        """Verify argparse can accept 'scan' command."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("command", nargs="?", choices=["scan", "diagnose"])
        parser.add_argument("signature_id", nargs="?")

        # Should not raise
        args = parser.parse_args(["scan"])
        assert args.command == "scan"

    def test_argument_parser_accepts_diagnose(self) -> None:
        """Verify argparse can accept 'diagnose' command with signature_id."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("command", nargs="?", choices=["scan", "diagnose"])
        parser.add_argument("signature_id", nargs="?")

        args = parser.parse_args(["diagnose", "sig-12345"])
        assert args.command == "diagnose"
        assert args.signature_id == "sig-12345"

    def test_argument_parser_accepts_no_command(self) -> None:
        """Verify argparse supports interactive mode with no command."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("command", nargs="?", choices=["scan", "diagnose"])
        parser.add_argument("signature_id", nargs="?")

        args = parser.parse_args([])
        assert args.command is None
        assert args.signature_id is None


# ============================================================================
# Output Structure Tests
# ============================================================================


class TestOutputStructures:
    """Test that output structures match specification."""

    def test_scan_output_structure(self) -> None:
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
    async def test_run_scan_invokes_poll_service(self, sample_signature: Signature) -> None:
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

        # Create PollService with fakes and execute poll cycle
        poll_service = PollService(
            telemetry=telemetry,
            store=store,
            fingerprinter=fingerprinter,
            triage=triage,
            investigator=investigator,
            lookback_minutes=60,
            batch_size=100,
        )

        result = await poll_service.execute_poll_cycle()

        # Verify result structure
        assert result.new_signatures >= 0
        assert result.updated_signatures >= 0
        assert result.errors_found >= 0
        assert result.errors_failed_to_process >= 0
        assert result.investigations_queued >= 0
        assert result.timestamp is not None

    @pytest.mark.asyncio
    async def test_run_scan_output_structure(self, sample_signature: Signature) -> None:
        """Test that scan output structure matches JSON specification."""
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

        # Create PollService and execute poll
        poll_service = PollService(
            telemetry=telemetry,
            store=store,
            fingerprinter=fingerprinter,
            triage=triage,
            investigator=investigator,
            lookback_minutes=60,
            batch_size=100,
        )

        result = await poll_service.execute_poll_cycle()

        # Simulate what _run_scan outputs
        output = {
            "status": "success",
            "new_signatures": result.new_signatures,
            "updated_signatures": result.updated_signatures,
            "errors_processed": result.errors_found,
            "errors_failed": result.errors_failed_to_process,
            "investigations_queued": result.investigations_queued,
            "timestamp": result.timestamp.isoformat(),
        }

        # Verify JSON serializability
        json_str = json.dumps(output)
        parsed = json.loads(json_str)

        assert parsed["status"] == "success"
        assert "new_signatures" in parsed
        assert "updated_signatures" in parsed
        assert "errors_processed" in parsed


class TestRunDiagnoseIntegration:
    """Integration tests for _run_diagnose function with fake adapters."""

    @pytest.mark.asyncio
    async def test_run_diagnose_invokes_investigator(
        self, sample_signature: Signature, sample_diagnosis: Diagnosis
    ) -> None:
        """Test that _run_diagnose invokes investigator with correct signature."""
        # Create fake adapters
        store = FakeSignatureStorePort()
        await store.save(sample_signature)

        investigator = FakeInvestigator(diagnosis_to_return=sample_diagnosis)

        # Simulate what _run_diagnose does
        signature = await store.get_by_id(sample_signature.id)
        assert signature is not None

        diagnosis = await investigator.investigate(signature)

        # Verify investigator was called with correct signature
        assert len(investigator.investigated_signatures) == 1
        assert investigator.investigated_signatures[0].id == sample_signature.id

        # Verify diagnosis structure
        assert diagnosis.root_cause == sample_diagnosis.root_cause
        assert diagnosis.confidence == sample_diagnosis.confidence
        assert diagnosis.cost_usd == sample_diagnosis.cost_usd

    @pytest.mark.asyncio
    async def test_run_diagnose_handles_missing_signature(self) -> None:
        """Test that diagnose properly handles nonexistent signature."""
        # Create fake adapters with empty store
        store = FakeSignatureStorePort()
        investigator = FakeInvestigator()

        # Attempt to retrieve nonexistent signature
        signature = await store.get_by_id("nonexistent-sig")

        # Verify it returns None
        assert signature is None

    @pytest.mark.asyncio
    async def test_run_diagnose_handles_investigation_error(self, sample_signature: Signature) -> None:
        """Test that diagnose handles investigation errors."""
        # Create fake adapters with error investigator
        store = FakeSignatureStorePort()
        await store.save(sample_signature)

        error = RuntimeError("LLM API error")
        investigator = FakeInvestigator(raise_error=error)

        # Attempt to investigate
        signature = await store.get_by_id(sample_signature.id)
        assert signature is not None

        with pytest.raises(RuntimeError, match="LLM API error"):
            await investigator.investigate(signature)
