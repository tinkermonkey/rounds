"""Markdown file notification adapter.

Implements NotificationPort by appending findings to markdown report files
organized in date-based directories (YYYY-MM-DD).
Useful for creating audit trails and persistent diagnostic records.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rounds.core.models import Diagnosis, Signature
from rounds.core.ports import NotificationPort

logger = logging.getLogger(__name__)


class MarkdownNotificationAdapter(NotificationPort):
    """Appends findings to markdown report files organized by date."""

    def __init__(self, report_dir: str):
        """Initialize markdown notification adapter.

        Args:
            report_dir: Base directory where date-based subdirectories will be created.
                       Reports will be organized as: report_dir/YYYY-MM-DD/HH-MM-SS_service_ErrorType.md
                       Summary will be written to: report_dir/../summary.md

        Raises:
            OSError: If base directory cannot be created (permission denied, invalid path, etc.)
        """
        self.base_dir = Path(report_dir)
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise OSError(f"Failed to create base directory {report_dir}: {e}") from e
        self._lock = asyncio.Lock()

    @staticmethod
    def _sanitize_filename(text: str) -> str:
        """Sanitize a string for use in filenames.

        Args:
            text: The text to sanitize.

        Returns:
            Sanitized string with spaces and problematic characters replaced.
        """
        # Replace any character that's not alphanumeric, underscore, or hyphen
        sanitized = re.sub(r'[^\w\-]', '_', text)
        return sanitized

    def _get_report_file(self, signature: Signature, diagnosis: Diagnosis) -> Path:
        """Get the report file path for a specific diagnosis.

        Args:
            signature: The signature being diagnosed.
            diagnosis: The diagnosis results.

        Returns:
            Path to the individual markdown report file.

        Raises:
            OSError: If date subdirectory cannot be created (permission denied, disk full, etc.)
        """
        # Get timestamp from diagnosis
        timestamp = diagnosis.diagnosed_at
        date_str = timestamp.strftime("%Y-%m-%d")
        time_str = timestamp.strftime("%H-%M-%S")

        # Extract and sanitize service name and error type
        service = self._sanitize_filename(signature.service or "unknown")
        error_type = self._sanitize_filename(signature.error_type or "UnknownError")

        # Create date directory
        date_dir = self.base_dir / date_str
        try:
            date_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise OSError(f"Failed to create date directory {date_dir}: {e}") from e

        # Generate filename: HH-MM-SS_service_ErrorType.md
        filename = f"{time_str}_{service}_{error_type}.md"
        return date_dir / filename

    async def report(
        self, signature: Signature, diagnosis: Diagnosis
    ) -> None:
        """Report a diagnosed signature to markdown file."""
        # Format the report entry
        entry = self._format_report_entry(signature, diagnosis)

        # Write to individual file
        async with self._lock:
            try:
                report_file = self._get_report_file(signature, diagnosis)
                await asyncio.to_thread(report_file.write_text, entry, encoding='utf-8')

                logger.info(
                    f"Wrote diagnosis report to {report_file}",
                    extra={
                        "signature_id": signature.id,
                        "fingerprint": signature.fingerprint,
                    },
                )

            except IOError as e:
                logger.error(
                    f"Failed to write markdown report: {e}",
                    extra={"path": str(self._get_report_file(signature, diagnosis))},
                    exc_info=True,
                )
                raise

    async def report_summary(self, stats: dict[str, Any]) -> None:
        """Write summary statistics to separate markdown file.

        Raises:
            OSError: If parent directory cannot be created or file cannot be written.
        """
        summary = self._format_summary(stats)

        async with self._lock:
            try:
                summary_file = self.base_dir.parent / "summary.md"
                # Ensure parent directory exists
                try:
                    summary_file.parent.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    raise OSError(f"Failed to create summary directory {summary_file.parent}: {e}") from e

                await asyncio.to_thread(summary_file.write_text, summary, encoding='utf-8')

                logger.info(
                    f"Wrote summary report to {summary_file}",
                    extra={"stats": stats},
                )

            except IOError as e:
                logger.error(
                    f"Failed to write markdown summary: {e}",
                    extra={"path": str(self.base_dir.parent / "summary.md")},
                    exc_info=True,
                )
                raise


    def _format_report_entry(
        self, signature: Signature, diagnosis: Diagnosis
    ) -> str:
        """Format a single diagnosis report as markdown.

        Args:
            signature: The signature that was diagnosed.
            diagnosis: The diagnosis results.

        Returns:
            Formatted markdown string ready to append to file.
        """
        lines = []

        # Timestamp
        timestamp = datetime.now(timezone.utc).isoformat()
        lines.append(f"## Diagnosis Report - {timestamp}")
        lines.append("")

        # Signature info
        lines.append("### Error Information")
        lines.append(f"- **Error Type**: {signature.error_type}")
        lines.append(f"- **Service**: {signature.service}")
        lines.append(f"- **Signature ID**: {signature.id}")
        lines.append(f"- **Fingerprint**: `{signature.fingerprint}`")
        lines.append(f"- **Status**: {signature.status.value}")
        lines.append("")

        # Failure pattern
        lines.append("### Failure Pattern")
        lines.append(f"- **Message Template**: {signature.message_template}")
        lines.append(f"- **Stack Hash**: `{signature.stack_hash}`")
        lines.append(f"- **Occurrences**: {signature.occurrence_count}")
        lines.append(f"- **First Seen**: {signature.first_seen.isoformat()}")
        lines.append(f"- **Last Seen**: {signature.last_seen.isoformat()}")

        if signature.tags:
            tags_str = ", ".join(f"`{tag}`" for tag in sorted(signature.tags))
            lines.append(f"- **Tags**: {tags_str}")

        lines.append("")

        # Diagnosis
        lines.append("### Root Cause Analysis")
        lines.append(f"- **Model**: {diagnosis.model}")
        lines.append(f"- **Confidence**: **{diagnosis.confidence.upper()}**")
        lines.append(f"- **Cost**: ${diagnosis.cost_usd:.2f}")
        lines.append(f"- **Diagnosed At**: {diagnosis.diagnosed_at.isoformat()}")
        lines.append("")

        lines.append("#### Root Cause")
        lines.append(f"{diagnosis.root_cause}")
        lines.append("")

        lines.append("#### Evidence")
        for i, evidence in enumerate(diagnosis.evidence, 1):
            lines.append(f"{i}. {evidence}")
        lines.append("")

        lines.append("#### Suggested Fix")
        lines.append(f"{diagnosis.suggested_fix}")
        lines.append("")

        # Separator
        lines.append("---")

        return "\n".join(lines)

    @staticmethod
    def _format_summary(stats: dict[str, Any]) -> str:
        """Format a summary statistics report as markdown.

        Args:
            stats: Dictionary with summary statistics.

        Returns:
            Formatted markdown string.
        """
        lines = []

        # Timestamp
        timestamp = datetime.now(timezone.utc).isoformat()
        lines.append(f"## Summary Report - {timestamp}")
        lines.append("")

        lines.append("### Overall Statistics")
        lines.append(f"- **Total Signatures**: {stats.get('total_signatures', 0)}")
        lines.append(f"- **Total Errors Seen**: {stats.get('total_errors_seen', 0)}")
        lines.append("")

        # By status
        by_status = stats.get("by_status", {})
        if by_status:
            lines.append("### By Status")
            for status, count in sorted(by_status.items()):
                lines.append(f"- **{status.upper()}**: {count}")
            lines.append("")

        # By service
        by_service = stats.get("by_service", {})
        if by_service:
            lines.append("### By Service (Top 10)")
            for service, count in sorted(by_service.items(), key=lambda x: -x[1])[:10]:
                lines.append(f"- **{service}**: {count}")
            lines.append("")

        # Separator
        lines.append("---")

        return "\n".join(lines)
