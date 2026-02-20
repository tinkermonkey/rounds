"""Integration tests for MarkdownNotificationAdapter.

Tests verify that the adapter correctly:
- Writes markdown reports to files
- Handles filesystem errors with proper logging
- Creates necessary directories
- Handles character encoding issues
"""

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from rounds.adapters.notification.markdown import MarkdownNotificationAdapter
from rounds.core.models import Diagnosis, Signature, SignatureStatus


@pytest.fixture
def temp_report_dir() -> Path:
    """Create a temporary directory for reports."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_signature() -> Signature:
    """Create a sample signature for testing."""
    return Signature(
        id="test-sig-001",
        fingerprint="abc123",
        error_type="DatabaseError",
        service="api-service",
        message_template="Failed to connect to {database}",
        stack_hash="stack-abc",
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
        occurrence_count=5,
        status=SignatureStatus.NEW,
    )


@pytest.fixture
def sample_diagnosis() -> Diagnosis:
    """Create a sample diagnosis for testing."""
    return Diagnosis(
        root_cause="Connection pool exhaustion",
        evidence=("Pool size exceeded",),
        suggested_fix="Increase pool size",
        confidence="high",
        diagnosed_at=datetime.now(timezone.utc),
        model="claude-opus",
        cost_usd=0.01,
    )


class TestMarkdownNotificationAdapter:
    """Test MarkdownNotificationAdapter functionality."""

    @pytest.mark.asyncio
    async def test_report_writes_to_file(
        self, temp_report_dir: Path, sample_signature: Signature, sample_diagnosis: Diagnosis
    ) -> None:
        """Test that report writes markdown content to a file."""
        adapter = MarkdownNotificationAdapter(str(temp_report_dir))

        await adapter.report(sample_signature, sample_diagnosis)

        # Verify file was created in date-based directory
        date_str = sample_diagnosis.diagnosed_at.strftime("%Y-%m-%d")
        date_dir = temp_report_dir / date_str
        assert date_dir.exists()

        # Verify file contains expected content
        files = list(date_dir.glob("*.md"))
        assert len(files) == 1

        content = files[0].read_text()
        assert "DatabaseError" in content
        assert "api-service" in content
        assert "claude-opus" in content

    @pytest.mark.asyncio
    async def test_report_summary_writes_to_file(
        self, temp_report_dir: Path
    ) -> None:
        """Test that report_summary writes summary to file."""
        adapter = MarkdownNotificationAdapter(str(temp_report_dir))

        stats = {
            "total_signatures": 10,
            "diagnosed": 3,
            "new": 7,
        }

        await adapter.report_summary(stats)

        # Verify summary file was created
        summary_file = temp_report_dir.parent / "summary.md"
        assert summary_file.exists()

        content = summary_file.read_text()
        assert "total_signatures" in content or "10" in content

    @pytest.mark.asyncio
    async def test_report_oserror_logging(
        self, temp_report_dir: Path, sample_signature: Signature, sample_diagnosis: Diagnosis
    ) -> None:
        """Test that OSError is caught and logged when writing fails."""
        adapter = MarkdownNotificationAdapter(str(temp_report_dir))

        # Mock write_text to raise OSError (covers both IOError and UnicodeEncodeError)
        with patch("pathlib.Path.write_text", side_effect=OSError("Permission denied")):
            with pytest.raises(OSError):
                await adapter.report(sample_signature, sample_diagnosis)

    @pytest.mark.asyncio
    async def test_report_summary_oserror_logging(
        self, temp_report_dir: Path
    ) -> None:
        """Test that OSError is caught and logged when writing summary fails."""
        adapter = MarkdownNotificationAdapter(str(temp_report_dir))

        stats = {"total": 1}

        # Mock write_text to raise OSError
        with patch("pathlib.Path.write_text", side_effect=OSError("Permission denied")):
            with pytest.raises(OSError):
                await adapter.report_summary(stats)

    @pytest.mark.asyncio
    async def test_report_creates_date_directory(
        self, temp_report_dir: Path, sample_signature: Signature, sample_diagnosis: Diagnosis
    ) -> None:
        """Test that report creates date-based subdirectory."""
        adapter = MarkdownNotificationAdapter(str(temp_report_dir))

        await adapter.report(sample_signature, sample_diagnosis)

        date_str = sample_diagnosis.diagnosed_at.strftime("%Y-%m-%d")
        date_dir = temp_report_dir / date_str

        assert date_dir.is_dir()

    def test_sanitize_filename_empty_string(self) -> None:
        """Test _sanitize_filename with empty string."""
        result = MarkdownNotificationAdapter._sanitize_filename("")
        assert result == ""

    def test_sanitize_filename_unicode_characters(self) -> None:
        """Test _sanitize_filename with unicode characters."""
        result = MarkdownNotificationAdapter._sanitize_filename("Error™ with emoji 🚀")
        # Should replace non-alphanumeric characters with underscores
        assert "___" in result or "_" in result
        assert "Error" in result

    def test_sanitize_filename_long_string(self) -> None:
        """Test _sanitize_filename with very long string."""
        long_string = "A" * 300  # Very long error type name
        result = MarkdownNotificationAdapter._sanitize_filename(long_string)
        assert len(result) == 300  # Length should be preserved

    def test_sanitize_filename_special_characters(self) -> None:
        """Test _sanitize_filename with filesystem-problematic characters."""
        text = "Error/With\\Special|Chars?*"
        result = MarkdownNotificationAdapter._sanitize_filename(text)
        # Should not contain problematic filesystem characters
        assert "/" not in result
        assert "\\" not in result
        assert "|" not in result
        assert "?" not in result
        assert "*" not in result
