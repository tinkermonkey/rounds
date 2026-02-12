"""Tests for newly implemented gap-filling features.

Covers:
- ManagementService (core implementation of ManagementPort)
- CLICommandHandler (CLI commands adapter)
- MarkdownNotificationAdapter (markdown file notification)
- GitHubIssueNotificationAdapter (GitHub issue creation)
- JaegerTelemetryAdapter (Jaeger trace backend)
- GrafanaStackTelemetryAdapter (Grafana Stack backend)
"""

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rounds.adapters.cli.commands import CLICommandHandler, run_command
from rounds.adapters.notification.github_issues import GitHubIssueNotificationAdapter
from rounds.adapters.notification.markdown import MarkdownNotificationAdapter
from rounds.adapters.telemetry.grafana_stack import GrafanaStackTelemetryAdapter
from rounds.adapters.telemetry.jaeger import JaegerTelemetryAdapter
from rounds.core.management_service import ManagementService
from rounds.core.models import (
    Confidence,
    Diagnosis,
    Severity,
    Signature,
    SignatureStatus,
    StackFrame,
)
from rounds.tests.fakes.store import FakeSignatureStorePort
from rounds.tests.fakes.telemetry import FakeTelemetryPort
from rounds.tests.fakes.diagnosis import FakeDiagnosisPort


# --- ManagementService Tests ---


@pytest.mark.asyncio
class TestManagementService:
    """Test suite for ManagementService (core implementation of ManagementPort)."""

    @pytest.fixture
    def store(self) -> FakeSignatureStorePort:
        """Create a fake store for testing."""
        return FakeSignatureStorePort()

    @pytest.fixture
    def service(self, store: FakeSignatureStorePort) -> ManagementService:
        """Create a ManagementService with fake dependencies."""
        telemetry = FakeTelemetryPort()
        diagnosis_engine = FakeDiagnosisPort()
        return ManagementService(
            store=store,
            telemetry=telemetry,
            diagnosis_engine=diagnosis_engine,
        )

    @pytest.fixture
    def sample_signature(self) -> Signature:
        """Create a sample signature for testing."""
        return Signature(
            id="sig-123",
            fingerprint="abc123",
            error_type="TimeoutError",
            service="auth-service",
            message_template="Connection timeout after {duration}ms",
            stack_hash="stack-123",
            first_seen=datetime.now(tz=timezone.utc),
            last_seen=datetime.now(tz=timezone.utc),
            occurrence_count=5,
            status=SignatureStatus.NEW,
        )

    async def test_mute_signature(
        self, service: ManagementService, store: FakeSignatureStorePort,
        sample_signature: Signature
    ) -> None:
        """Test muting a signature."""
        # Setup
        await store.save(sample_signature)

        # Execute
        await service.mute_signature("sig-123", "Investigating separately")

        # Verify
        updated = await store.get_by_id("sig-123")
        assert updated is not None
        assert updated.status == SignatureStatus.MUTED

    async def test_mute_nonexistent_signature(self, service: ManagementService) -> None:
        """Test muting a signature that doesn't exist."""
        with pytest.raises(ValueError, match="not found"):
            await service.mute_signature("nonexistent")

    async def test_resolve_signature(
        self, service: ManagementService, store: FakeSignatureStorePort,
        sample_signature: Signature
    ) -> None:
        """Test resolving a signature."""
        await store.save(sample_signature)

        await service.resolve_signature("sig-123", "Upgraded connection pool size")

        updated = await store.get_by_id("sig-123")
        assert updated is not None
        assert updated.status == SignatureStatus.RESOLVED

    async def test_retriage_signature(
        self, service: ManagementService, store: FakeSignatureStorePort,
        sample_signature: Signature
    ) -> None:
        """Test retriaging a signature."""
        # Set up signature with diagnosis
        diagnosis = Diagnosis(
            root_cause="Connection limit exceeded",
            evidence=("Pool size 10", "Concurrent requests 15"),
            suggested_fix="Increase pool size",
            confidence="high",
            diagnosed_at=datetime.now(tz=timezone.utc),
            model="claude-3",
            cost_usd=0.50,
        )
        sample_signature.diagnosis = diagnosis
        sample_signature.status = SignatureStatus.DIAGNOSED

        await store.save(sample_signature)

        # Execute retriage
        await service.retriage_signature("sig-123")

        # Verify
        updated = await store.get_by_id("sig-123")
        assert updated is not None
        assert updated.status == SignatureStatus.NEW
        assert updated.diagnosis is None

    async def test_get_signature_details(
        self, service: ManagementService, store: FakeSignatureStorePort,
        sample_signature: Signature
    ) -> None:
        """Test retrieving signature details."""
        diagnosis = Diagnosis(
            root_cause="Connection limit",
            evidence=("Pool exhausted",),
            suggested_fix="Increase pool size",
            confidence="high",
            diagnosed_at=datetime.now(tz=timezone.utc),
            model="claude-3",
            cost_usd=0.50,
        )
        sample_signature.diagnosis = diagnosis

        await store.save(sample_signature)

        details = await service.get_signature_details("sig-123")

        assert details["id"] == "sig-123"
        assert details["service"] == "auth-service"
        assert details["error_type"] == "TimeoutError"
        assert details["status"] == "new"
        assert details["occurrence_count"] == 5
        assert details["diagnosis"] is not None
        assert details["diagnosis"]["confidence"] == "high"

    async def test_get_signature_details_without_diagnosis(
        self, service: ManagementService, store: FakeSignatureStorePort,
        sample_signature: Signature
    ) -> None:
        """Test retrieving details for a signature without diagnosis."""
        await store.save(sample_signature)

        details = await service.get_signature_details("sig-123")

        assert details["diagnosis"] is None

    async def test_list_signatures_no_filter(
        self, service: ManagementService, store: FakeSignatureStorePort,
        sample_signature: Signature
    ) -> None:
        """Test listing all signatures without status filter."""
        # Setup multiple signatures with different statuses
        sig1 = Signature(
            id="sig-1",
            fingerprint="fp-1",
            error_type="ValueError",
            service="api",
            message_template="Invalid value",
            stack_hash="hash-1",
            first_seen=datetime.now(tz=timezone.utc),
            last_seen=datetime.now(tz=timezone.utc),
            occurrence_count=1,
            status=SignatureStatus.NEW,
        )
        sig2 = Signature(
            id="sig-2",
            fingerprint="fp-2",
            error_type="TimeoutError",
            service="api",
            message_template="Timeout",
            stack_hash="hash-2",
            first_seen=datetime.now(tz=timezone.utc),
            last_seen=datetime.now(tz=timezone.utc),
            occurrence_count=2,
            status=SignatureStatus.DIAGNOSED,
        )

        await store.save(sig1)
        await store.save(sig2)
        store.mark_pending(sig1)
        store.mark_pending(sig2)

        # List without filter should return all pending signatures
        result = await service.list_signatures()
        assert len(result) == 2

    async def test_list_signatures_filter_by_status(
        self, service: ManagementService, store: FakeSignatureStorePort,
        sample_signature: Signature
    ) -> None:
        """Test listing signatures filtered by status."""
        # Setup signatures with different statuses
        sig_new = sample_signature  # NEW status
        sig_diagnosed = Signature(
            id="sig-diagnosed",
            fingerprint="fp-diagnosed",
            error_type="RuntimeError",
            service="worker",
            message_template="Runtime error",
            stack_hash="hash-diagnosed",
            first_seen=datetime.now(tz=timezone.utc),
            last_seen=datetime.now(tz=timezone.utc),
            occurrence_count=5,
            status=SignatureStatus.DIAGNOSED,
        )
        sig_muted = Signature(
            id="sig-muted",
            fingerprint="fp-muted",
            error_type="DeprecationWarning",
            service="legacy",
            message_template="Deprecated",
            stack_hash="hash-muted",
            first_seen=datetime.now(tz=timezone.utc),
            last_seen=datetime.now(tz=timezone.utc),
            occurrence_count=3,
            status=SignatureStatus.MUTED,
        )

        await store.save(sig_new)
        await store.save(sig_diagnosed)
        await store.save(sig_muted)
        store.mark_pending(sig_new)
        store.mark_pending(sig_diagnosed)
        store.mark_pending(sig_muted)

        # Filter by NEW status
        new_sigs = await service.list_signatures(status=SignatureStatus.NEW)
        assert len(new_sigs) == 1
        assert new_sigs[0].id == "sig-123"

        # Filter by DIAGNOSED status
        diagnosed_sigs = await service.list_signatures(status=SignatureStatus.DIAGNOSED)
        assert len(diagnosed_sigs) == 1
        assert diagnosed_sigs[0].id == "sig-diagnosed"

        # Filter by MUTED status
        muted_sigs = await service.list_signatures(status=SignatureStatus.MUTED)
        assert len(muted_sigs) == 1
        assert muted_sigs[0].id == "sig-muted"

    async def test_list_signatures_empty_filter(
        self, service: ManagementService, store: FakeSignatureStorePort,
        sample_signature: Signature
    ) -> None:
        """Test listing with a filter that matches no signatures."""
        await store.save(sample_signature)
        store.mark_pending(sample_signature)

        # Filter by RESOLVED status (none match)
        resolved_sigs = await service.list_signatures(status=SignatureStatus.RESOLVED)
        assert len(resolved_sigs) == 0

    async def test_reinvestigate_signature(
        self, service: ManagementService, store: FakeSignatureStorePort,
        sample_signature: Signature
    ) -> None:
        """Test reinvestigating a signature."""
        # Set initial status to DIAGNOSED
        sample_signature.status = SignatureStatus.DIAGNOSED
        await store.save(sample_signature)

        # Execute reinvestigation
        diagnosis = await service.reinvestigate("sig-123")

        # Verify the diagnosis was returned
        assert diagnosis is not None
        assert diagnosis.root_cause is not None

        # Verify signature was updated to DIAGNOSED status
        updated = await store.get_by_id("sig-123")
        assert updated is not None
        assert updated.status == SignatureStatus.DIAGNOSED
        assert updated.diagnosis is not None

    async def test_reinvestigate_nonexistent_signature(
        self, service: ManagementService
    ) -> None:
        """Test reinvestigating a signature that doesn't exist."""
        with pytest.raises(ValueError, match="not found"):
            await service.reinvestigate("nonexistent")


# --- CLICommandHandler Tests ---


@pytest.mark.asyncio
class TestCLICommandHandler:
    """Test suite for CLICommandHandler."""

    @pytest.fixture
    def mock_management(self) -> AsyncMock:
        """Create a mock ManagementPort."""
        return AsyncMock()

    @pytest.fixture
    def handler(self, mock_management: AsyncMock) -> CLICommandHandler:
        """Create a CLICommandHandler with mock management."""
        return CLICommandHandler(mock_management)

    @pytest.fixture
    def sample_details(self) -> dict[str, Any]:
        """Create sample signature details."""
        return {
            "id": "sig-123",
            "fingerprint": "abc123",
            "service": "api-service",
            "error_type": "ValueError",
            "status": "new",
            "occurrence_count": 5,
            "first_seen": "2024-01-01T00:00:00",
            "last_seen": "2024-01-02T00:00:00",
            "message_template": "Invalid value: {value}",
            "diagnosis": {
                "root_cause": "Missing validation",
                "confidence": "high",
                "suggested_fix": "Add input validation",
            },
            "related_signatures": [
                {"id": "sig-456", "service": "api-service", "occurrence_count": 3}
            ],
        }

    async def test_mute_signature_command(self, handler: CLICommandHandler, mock_management: AsyncMock) -> None:
        """Test mute signature command."""
        result = await handler.mute_signature("sig-123", "Fixed in v2.0", verbose=False)

        assert result["status"] == "success"
        assert result["operation"] == "mute"
        assert result["signature_id"] == "sig-123"
        assert result["reason"] == "Fixed in v2.0"
        mock_management.mute_signature.assert_called_once_with("sig-123", "Fixed in v2.0")

    async def test_mute_signature_error(self, handler: CLICommandHandler, mock_management: AsyncMock) -> None:
        """Test mute command with error."""
        mock_management.mute_signature.side_effect = ValueError("Signature not found")

        result = await handler.mute_signature("nonexistent")

        assert result["status"] == "error"
        assert "not found" in result["message"]

    async def test_resolve_signature_command(
        self, handler: CLICommandHandler, mock_management: AsyncMock
    ) -> None:
        """Test resolve signature command."""
        result = await handler.resolve_signature("sig-123", "Upgraded connection pool")

        assert result["status"] == "success"
        assert result["operation"] == "resolve"
        assert result["fix_applied"] == "Upgraded connection pool"

    async def test_retriage_signature_command(
        self, handler: CLICommandHandler, mock_management: AsyncMock
    ) -> None:
        """Test retriage signature command."""
        result = await handler.retriage_signature("sig-123")

        assert result["status"] == "success"
        assert result["operation"] == "retriage"

    async def test_get_details_json_format(
        self, handler: CLICommandHandler, mock_management: AsyncMock,
        sample_details: dict[str, Any]
    ) -> None:
        """Test getting details in JSON format."""
        mock_management.get_signature_details.return_value = sample_details

        result = await handler.get_signature_details("sig-123", output_format="json")

        assert result["status"] == "success"
        assert result["operation"] == "get_details"
        assert result["data"]["id"] == "sig-123"

    async def test_get_details_text_format(
        self, handler: CLICommandHandler, mock_management: AsyncMock,
        sample_details: dict[str, Any]
    ) -> None:
        """Test getting details in text format."""
        mock_management.get_signature_details.return_value = sample_details

        result = await handler.get_signature_details("sig-123", output_format="text")

        assert result["status"] == "success"
        text = result["data"]
        assert "Signature ID: sig-123" in text
        assert "Service: api-service" in text
        assert "Status: new" in text

    async def test_get_details_invalid_format(
        self, handler: CLICommandHandler, mock_management: AsyncMock,
        sample_details: dict[str, Any]
    ) -> None:
        """Test getting details with invalid format."""
        mock_management.get_signature_details.return_value = sample_details

        result = await handler.get_signature_details("sig-123", output_format="invalid")

        assert result["status"] == "error"
        assert "Unsupported format" in result["message"]


# --- MarkdownNotificationAdapter Tests ---


@pytest.mark.asyncio
class TestMarkdownNotificationAdapter:
    """Test suite for MarkdownNotificationAdapter."""

    @pytest.fixture
    def temp_file(self) -> Path:
        """Create a temporary markdown file."""
        fd, path = tempfile.mkstemp(suffix=".md")
        import os
        os.close(fd)
        yield Path(path)
        Path(path).unlink()

    @pytest.fixture
    def adapter(self, temp_file: Path) -> MarkdownNotificationAdapter:
        """Create a MarkdownNotificationAdapter."""
        return MarkdownNotificationAdapter(str(temp_file))

    @pytest.fixture
    def sample_signature(self) -> Signature:
        """Create a sample signature."""
        return Signature(
            id="sig-123",
            fingerprint="abc123",
            error_type="TimeoutError",
            service="api-service",
            message_template="Connection timeout",
            stack_hash="stack-123",
            first_seen=datetime.now(tz=timezone.utc),
            last_seen=datetime.now(tz=timezone.utc),
            occurrence_count=5,
            status=SignatureStatus.NEW,
        )

    @pytest.fixture
    def sample_diagnosis(self) -> Diagnosis:
        """Create a sample diagnosis."""
        return Diagnosis(
            root_cause="Connection pool exhausted",
            evidence=("10 concurrent requests", "Pool size 5"),
            suggested_fix="Increase pool size to 20",
            confidence="high",
            diagnosed_at=datetime.now(tz=timezone.utc),
            model="claude-3",
            cost_usd=0.50,
        )

    async def test_report_appends_to_file(
        self, adapter: MarkdownNotificationAdapter, temp_file: Path,
        sample_signature: Signature, sample_diagnosis: Diagnosis
    ) -> None:
        """Test that report is appended to file."""
        await adapter.report(sample_signature, sample_diagnosis)

        content = temp_file.read_text()
        assert "Diagnosis Report" in content
        assert "TimeoutError" in content
        assert "api-service" in content
        assert "Connection pool exhausted" in content

    async def test_report_summary_appends_to_file(
        self, adapter: MarkdownNotificationAdapter, temp_file: Path
    ) -> None:
        """Test that summary report is appended to file."""
        stats = {
            "total_signatures": 42,
            "total_errors_seen": 150,
            "by_status": {"new": 10, "investigated": 32},
            "by_service": {"api": 20, "auth": 15, "db": 7},
        }

        await adapter.report_summary(stats)

        content = temp_file.read_text()
        assert "Summary Report" in content
        assert "**Total Signatures**: 42" in content
        assert "**NEW**: 10" in content

    async def test_multiple_reports_appended(
        self, adapter: MarkdownNotificationAdapter, temp_file: Path,
        sample_signature: Signature, sample_diagnosis: Diagnosis
    ) -> None:
        """Test that multiple reports are appended."""
        await adapter.report(sample_signature, sample_diagnosis)
        await adapter.report(sample_signature, sample_diagnosis)

        content = temp_file.read_text()
        # Should have two report headers
        assert content.count("Diagnosis Report") == 2


# --- GitHubIssueNotificationAdapter Tests ---


class TestGitHubIssueNotificationAdapter:
    """Test suite for GitHubIssueNotificationAdapter."""

    @pytest.fixture
    def adapter(self) -> GitHubIssueNotificationAdapter:
        """Create a GitHubIssueNotificationAdapter."""
        return GitHubIssueNotificationAdapter(
            repo_owner="test-org",
            repo_name="test-repo",
            github_token="test-token",
        )

    @pytest.fixture
    def sample_signature(self) -> Signature:
        """Create a sample signature."""
        return Signature(
            id="sig-123",
            fingerprint="abc123",
            error_type="DatabaseError",
            service="payment-service",
            message_template="Connection refused",
            stack_hash="stack-123",
            first_seen=datetime.now(tz=timezone.utc),
            last_seen=datetime.now(tz=timezone.utc),
            occurrence_count=10,
            status=SignatureStatus.NEW,
        )

    @pytest.fixture
    def sample_diagnosis(self) -> Diagnosis:
        """Create a sample diagnosis."""
        return Diagnosis(
            root_cause="Database server overloaded",
            evidence=("100 open connections", "CPU at 95%"),
            suggested_fix="Scale database vertically or add replicas",
            confidence="high",
            diagnosed_at=datetime.now(tz=timezone.utc),
            model="claude-3",
            cost_usd=0.75,
        )

    def test_format_issue_title(
        self, adapter: GitHubIssueNotificationAdapter, sample_signature: Signature
    ) -> None:
        """Test formatting GitHub issue title."""
        title = adapter._format_issue_title(sample_signature)

        assert "[payment-service]" in title
        assert "DatabaseError" in title

    def test_format_issue_body(
        self, adapter: GitHubIssueNotificationAdapter, sample_signature: Signature,
        sample_diagnosis: Diagnosis
    ) -> None:
        """Test formatting GitHub issue body."""
        body = adapter._format_issue_body(sample_signature, sample_diagnosis)

        assert "Error Information" in body
        assert "DatabaseError" in body
        assert "Root Cause Analysis" in body
        assert "Database server overloaded" in body
        assert "Suggested Fix" in body

    def test_format_summary_body(self, adapter: GitHubIssueNotificationAdapter) -> None:
        """Test formatting summary report as GitHub issue."""
        stats = {
            "total_signatures": 42,
            "total_errors_seen": 150,
            "by_status": {"new": 10, "investigated": 32},
            "by_service": {"api": 20, "auth": 15},
        }

        body = adapter._format_summary_body(stats)

        assert "Diagnostic Summary" in body
        assert "**Total Signatures**: 42" in body


# --- JaegerTelemetryAdapter Tests ---


@pytest.mark.asyncio
class TestJaegerTelemetryAdapter:
    """Test suite for JaegerTelemetryAdapter."""

    @pytest.fixture
    def adapter(self) -> JaegerTelemetryAdapter:
        """Create a JaegerTelemetryAdapter."""
        return JaegerTelemetryAdapter(api_url="http://localhost:16686")

    @pytest.mark.asyncio
    async def test_adapter_lifecycle(self, adapter: JaegerTelemetryAdapter) -> None:
        """Test adapter initialization and cleanup."""
        async with adapter:
            pass
        # If we get here, cleanup was successful


# --- GrafanaStackTelemetryAdapter Tests ---


@pytest.mark.asyncio
class TestGrafanaStackTelemetryAdapter:
    """Test suite for GrafanaStackTelemetryAdapter."""

    @pytest.fixture
    def adapter(self) -> GrafanaStackTelemetryAdapter:
        """Create a GrafanaStackTelemetryAdapter."""
        return GrafanaStackTelemetryAdapter(
            tempo_url="http://localhost:3200",
            loki_url="http://localhost:3100",
        )

    @pytest.mark.asyncio
    async def test_adapter_lifecycle(self, adapter: GrafanaStackTelemetryAdapter) -> None:
        """Test adapter initialization and cleanup."""
        async with adapter:
            pass


# --- Integration Tests with run_command ---


@pytest.mark.asyncio
class TestRunCommand:
    """Test suite for CLI command runner."""

    @pytest.fixture
    def mock_management(self) -> AsyncMock:
        """Create a mock ManagementPort."""
        return AsyncMock()

    async def test_run_mute_command(self, mock_management: AsyncMock) -> None:
        """Test running mute command."""
        result = await run_command(
            mock_management,
            "mute",
            {"signature_id": "sig-123", "reason": "Fixed"},
        )

        assert result["status"] == "success"
        mock_management.mute_signature.assert_called_once()

    async def test_run_resolve_command(self, mock_management: AsyncMock) -> None:
        """Test running resolve command."""
        result = await run_command(
            mock_management,
            "resolve",
            {"signature_id": "sig-123", "fix_applied": "Patched"},
        )

        assert result["status"] == "success"

    async def test_run_retriage_command(self, mock_management: AsyncMock) -> None:
        """Test running retriage command."""
        result = await run_command(
            mock_management,
            "retriage",
            {"signature_id": "sig-123"},
        )

        assert result["status"] == "success"

    async def test_run_details_command(self, mock_management: AsyncMock) -> None:
        """Test running details command."""
        mock_management.get_signature_details.return_value = {
            "id": "sig-123",
            "service": "test",
        }

        result = await run_command(
            mock_management,
            "details",
            {"signature_id": "sig-123"},
        )

        assert result["status"] == "success"

    async def test_run_unknown_command(self, mock_management: AsyncMock) -> None:
        """Test running unknown command."""
        with pytest.raises(ValueError, match="Unknown command"):
            await run_command(mock_management, "unknown", {})
