"""Stdout notification adapter.

Implements NotificationPort by printing findings to terminal with
human-readable formatting.
"""

import asyncio
import logging
from typing import Any

from rounds.core.models import Diagnosis, Signature
from rounds.core.ports import NotificationPort

logger = logging.getLogger(__name__)


class StdoutNotificationAdapter(NotificationPort):
    """Prints findings to stdout with human-readable formatting."""

    def __init__(self, verbose: bool = False):
        """Initialize stdout notification adapter.

        Args:
            verbose: If True, include additional details in output.
        """
        self.verbose = verbose

    async def report(
        self, signature: Signature, diagnosis: Diagnosis
    ) -> None:
        """Report a diagnosed signature to stdout."""
        # Format header
        header = self._format_header(signature)
        await asyncio.to_thread(print, header)

        # Format signature details
        sig_details = self._format_signature_details(signature)
        await asyncio.to_thread(print, sig_details)

        # Format diagnosis
        diagnosis_details = self._format_diagnosis(diagnosis)
        await asyncio.to_thread(print, diagnosis_details)

        # Footer
        await asyncio.to_thread(print, self._format_footer())

    async def report_summary(self, stats: dict[str, Any]) -> None:
        """Periodic summary report."""
        summary = self._format_summary(stats)
        await asyncio.to_thread(print, summary)

    @staticmethod
    def _format_header(signature: Signature) -> str:
        """Format the report header."""
        lines = [
            "=" * 80,
            "DIAGNOSIS REPORT",
            "=" * 80,
            f"Error Type: {signature.error_type}",
            f"Service: {signature.service}",
            f"Status: {signature.status.value.upper()}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _format_signature_details(signature: Signature) -> str:
        """Format signature details section."""
        lines = [
            "",
            "-" * 80,
            "FAILURE PATTERN",
            "-" * 80,
            f"Fingerprint: {signature.fingerprint}",
            f"Message Template: {signature.message_template}",
            f"Stack Hash: {signature.stack_hash}",
            f"",
            f"Occurrences: {signature.occurrence_count}",
            f"First Seen: {signature.first_seen}",
            f"Last Seen: {signature.last_seen}",
        ]

        if signature.tags:
            tags_str = ", ".join(sorted(signature.tags))
            lines.append(f"Tags: {tags_str}")

        return "\n".join(lines)

    @staticmethod
    def _format_diagnosis(diagnosis: Diagnosis) -> str:
        """Format diagnosis section."""
        lines = [
            "",
            "-" * 80,
            "ANALYSIS",
            "-" * 80,
            f"Model: {diagnosis.model}",
            f"Confidence: {diagnosis.confidence.upper()}",
            f"Cost: ${diagnosis.cost_usd:.2f}",
            "",
            "ROOT CAUSE:",
            diagnosis.root_cause,
            "",
            "EVIDENCE:",
        ]

        for i, evidence in enumerate(diagnosis.evidence, 1):
            lines.append(f"  {i}. {evidence}")

        lines.extend(
            [
                "",
                "SUGGESTED FIX:",
                diagnosis.suggested_fix,
            ]
        )

        return "\n".join(lines)

    @staticmethod
    def _format_footer() -> str:
        """Format the report footer."""
        return "=" * 80

    @staticmethod
    def _format_summary(stats: dict[str, Any]) -> str:
        """Format a summary statistics report."""
        lines = [
            "=" * 80,
            "SUMMARY REPORT",
            "=" * 80,
            "",
            f"Total Signatures: {stats.get('total_signatures', 0)}",
            f"Total Errors Seen: {stats.get('total_errors_seen', 0)}",
        ]

        by_status = stats.get("by_status", {})
        if by_status:
            lines.append("")
            lines.append("By Status:")
            for status, count in sorted(by_status.items()):
                lines.append(f"  {status.upper()}: {count}")

        by_service = stats.get("by_service", {})
        if by_service:
            lines.append("")
            lines.append("By Service:")
            for service, count in sorted(by_service.items(), key=lambda x: -x[1])[:10]:
                lines.append(f"  {service}: {count}")

        lines.append("")
        lines.append("=" * 80)

        return "\n".join(lines)
