"""Markdown file notification adapter.

Implements NotificationPort by appending findings to a markdown report file.
Useful for creating audit trails and persistent diagnostic records.
"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rounds.core.models import Diagnosis, Signature
from rounds.core.ports import NotificationPort

logger = logging.getLogger(__name__)


class MarkdownNotificationAdapter(NotificationPort):
    """Appends findings to a markdown report file."""

    def __init__(self, report_path: str):
        """Initialize markdown notification adapter.

        Args:
            report_path: Path to the markdown file where reports will be appended.
        """
        self.report_path = Path(report_path)
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def report(
        self, signature: Signature, diagnosis: Diagnosis
    ) -> None:
        """Report a diagnosed signature to markdown file."""
        # Format the report entry
        entry = self._format_report_entry(signature, diagnosis)

        # Append to file
        async with self._lock:
            try:
                with open(self.report_path, "a") as f:
                    f.write(entry)
                    f.write("\n")

                logger.info(
                    f"Appended diagnosis report to {self.report_path}",
                    extra={
                        "signature_id": signature.id,
                        "fingerprint": signature.fingerprint,
                    },
                )

            except IOError as e:
                logger.error(
                    f"Failed to write markdown report: {e}",
                    extra={"path": str(self.report_path)},
                )
                raise

    async def report_summary(self, stats: dict[str, Any]) -> None:
        """Periodic summary report appended to markdown file."""
        summary = self._format_summary(stats)

        async with self._lock:
            try:
                with open(self.report_path, "a") as f:
                    f.write(summary)
                    f.write("\n")

                logger.info(
                    f"Appended summary report to {self.report_path}",
                    extra={"stats": stats},
                )

            except IOError as e:
                logger.error(
                    f"Failed to write markdown summary: {e}",
                    extra={"path": str(self.report_path)},
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
        lines.append(f"- **Confidence**: **{diagnosis.confidence.value.upper()}**")
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
