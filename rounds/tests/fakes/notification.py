"""Fake NotificationPort implementation for testing."""

from datetime import datetime
from typing import Any

from rounds.core.models import Diagnosis, Signature
from rounds.core.ports import NotificationPort


class FakeNotificationPort(NotificationPort):
    """In-memory notification adapter for testing.

    Captures all notifications sent through this port for test assertions.
    """

    def __init__(self) -> None:
        """Initialize with empty notification history."""
        self.reported_diagnoses: list[tuple[Signature, Diagnosis]] = []
        self.reported_summaries: list[dict[str, Any]] = []
        self.reported_alerts: list[dict[str, Any]] = []
        self.closed_resolved_issues: list[Signature] = []
        self.report_call_count = 0
        self.report_summary_call_count = 0
        self.report_alert_call_count = 0
        self.close_resolved_issue_call_count = 0
        self.close_call_count = 0
        self.should_fail: bool = False
        self.fail_message: str = "Notification failed"
        # Configurable return value for report(), simulating a channel that
        # performs cooldown-gated alerting (e.g. phone-home).
        self.report_alerted_at: datetime | None = None
        # Captures the `immediate` flag passed to each report() call, in order.
        self.report_immediate_flags: list[bool] = []

    async def report(
        self, signature: Signature, diagnosis: Diagnosis, *, immediate: bool = False
    ) -> datetime | None:
        """Report a diagnosis for a signature.

        Captures the report for test assertions. Returns report_alerted_at,
        configurable to simulate a cooldown-gated channel's alert timestamp.
        """
        self.report_call_count += 1
        self.report_immediate_flags.append(immediate)

        if self.should_fail:
            raise RuntimeError(self.fail_message)

        self.reported_diagnoses.append((signature, diagnosis))
        return self.report_alerted_at

    async def report_summary(self, stats: dict[str, Any]) -> None:
        """Report a summary of statistics.

        Captures the summary for test assertions.
        """
        self.report_summary_call_count += 1

        if self.should_fail:
            raise RuntimeError(self.fail_message)

        self.reported_summaries.append(stats)

    async def report_alert(self, alert: dict[str, Any]) -> None:
        """Report an operational alert.

        Captures the alert for test assertions.
        """
        self.report_alert_call_count += 1

        if self.should_fail:
            raise RuntimeError(self.fail_message)

        self.reported_alerts.append(alert)

    async def close_resolved_issue(self, signature: Signature) -> None:
        """Close the issue for an auto-resolved signature.

        Captures the call for test assertions.
        """
        self.close_resolved_issue_call_count += 1

        if self.should_fail:
            raise RuntimeError(self.fail_message)

        self.closed_resolved_issues.append(signature)

    async def close(self) -> None:
        """Close connections and clean up resources.

        Captures the call for test assertions. Unlike the other methods,
        this ignores `should_fail` — tests that need `close()` itself to
        raise should call it directly rather than via `should_fail`.
        """
        self.close_call_count += 1

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
        self.report_immediate_flags.clear()
        self.reported_summaries.clear()
        self.reported_alerts.clear()
        self.closed_resolved_issues.clear()
        self.report_call_count = 0
        self.report_summary_call_count = 0
        self.report_alert_call_count = 0
        self.close_resolved_issue_call_count = 0
        self.close_call_count = 0
        self.should_fail = False
        self.fail_message = "Notification failed"
