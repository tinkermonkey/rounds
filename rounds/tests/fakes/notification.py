"""Fake NotificationPort implementation for testing."""

from typing import Any

from rounds.core.models import Diagnosis, Signature
from rounds.core.ports import NotificationPort


class FakeNotificationPort(NotificationPort):
    """In-memory notification adapter for testing.

    Captures all notifications sent through this port for test assertions.
    """

    def __init__(self):
        """Initialize with empty notification history."""
        self.reported_diagnoses: list[tuple[Signature, Diagnosis]] = []
        self.reported_summaries: list[dict[str, Any]] = []
        self.report_call_count = 0
        self.report_summary_call_count = 0
        self.should_fail: bool = False
        self.fail_message: str = "Notification failed"

    async def report(self, signature: Signature, diagnosis: Diagnosis) -> None:
        """Report a diagnosis for a signature.

        Captures the report for test assertions.
        """
        self.report_call_count += 1

        if self.should_fail:
            raise RuntimeError(self.fail_message)

        self.reported_diagnoses.append((signature, diagnosis))

    async def report_summary(self, stats: dict[str, Any]) -> None:
        """Report a summary of statistics.

        Captures the summary for test assertions.
        """
        self.report_summary_call_count += 1

        if self.should_fail:
            raise RuntimeError(self.fail_message)

        self.reported_summaries.append(stats)

    def get_last_diagnosis_report(self) -> tuple[Signature, Diagnosis] | None:
        """Get the most recent diagnosis report, if any."""
        if self.reported_diagnoses:
            return self.reported_diagnoses[-1]
        return None

    def get_last_summary_report(self) -> dict[str, Any] | None:
        """Get the most recent summary report, if any."""
        if self.reported_summaries:
            return self.reported_summaries[-1]
        return None

    def get_reported_diagnosis_count(self) -> int:
        """Get the count of reported diagnoses."""
        return len(self.reported_diagnoses)

    def get_reported_diagnoses_for_signature(
        self, signature_id: str
    ) -> list[tuple[Signature, Diagnosis]]:
        """Get all reported diagnoses for a specific signature."""
        return [
            (sig, diag)
            for sig, diag in self.reported_diagnoses
            if sig.id == signature_id
        ]

    def set_should_fail(self, should_fail: bool, message: str = "Notification failed") -> None:
        """Configure the adapter to fail on the next operation."""
        self.should_fail = should_fail
        self.fail_message = message

    def reset(self) -> None:
        """Reset all collected notifications and state."""
        self.reported_diagnoses.clear()
        self.reported_summaries.clear()
        self.report_call_count = 0
        self.report_summary_call_count = 0
        self.should_fail = False
        self.fail_message = "Notification failed"
