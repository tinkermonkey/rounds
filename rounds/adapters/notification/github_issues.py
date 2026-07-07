"""GitHub Issues notification adapter.

Implements NotificationPort by creating or updating GitHub issues for diagnosed errors.
Enables integration with development workflows and issue tracking.
"""

import logging
from typing import Any

import httpx

from rounds.core.models import Diagnosis, Signature
from rounds.core.ports import NotificationPort

logger = logging.getLogger(__name__)


class GitHubIssueNotificationAdapter(NotificationPort):
    """Creates GitHub issues for diagnosed signatures."""

    def __init__(
        self,
        repo_owner: str,
        repo_name: str,
        github_token: str,
        api_base_url: str = "https://api.github.com",
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ):
        """Initialize GitHub Issues notification adapter.

        Args:
            repo_owner: GitHub repository owner (username or organization).
            repo_name: GitHub repository name.
            github_token: GitHub personal access token for authentication.
            api_base_url: Base URL for GitHub API (default: https://api.github.com).
            labels: Optional list of labels to apply to created issues.
            assignees: Optional list of GitHub usernames to assign to issues.
        """
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.github_token = github_token
        self.api_base_url = api_base_url
        self.labels = labels or ["type:bug", "source:rounds"]
        self.assignees = assignees or []
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "GitHubIssueNotificationAdapter":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client.

        Returns:
            httpx.AsyncClient configured with GitHub authentication.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.api_base_url,
                headers={
                    "Authorization": f"token {self.github_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def report(
        self, signature: Signature, diagnosis: Diagnosis
    ) -> None:
        """Report a diagnosed signature by creating or updating a GitHub issue.

        Deduplicates on the signature's fingerprint: if an open issue already
        carries the fingerprint label, a recurrence comment is posted to it
        instead of creating a second issue for the same failure pattern.
        """
        fingerprint_label = self._fingerprint_label(signature.fingerprint)
        existing_issue = await self._find_existing_issue(fingerprint_label)

        if existing_issue is not None:
            await self._post_recurrence_comment(existing_issue["number"], signature)
            return

        await self._create_issue(signature, diagnosis, fingerprint_label)

    async def _find_existing_issue(
        self, fingerprint_label: str
    ) -> dict[str, Any] | None:
        """Search for an open issue already labeled with this fingerprint.

        Returns:
            The first matching open issue, or None if no match exists.
        """
        try:
            client = await self._get_client()

            response = await client.get(
                f"/repos/{self.repo_owner}/{self.repo_name}/issues",
                params={"labels": fingerprint_label, "state": "open"},
            )
            response.raise_for_status()

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to search for existing GitHub issue: {e.response.status_code}",
                extra={"response": e.response.text},
                exc_info=True,
            )
            raise
        except httpx.RequestError as e:
            logger.error(f"Failed to search for existing GitHub issue: {e}", exc_info=True)
            raise

        # The issues list endpoint also returns pull requests; exclude them.
        issues = [item for item in response.json() if "pull_request" not in item]
        return issues[0] if issues else None

    async def _create_issue(
        self, signature: Signature, diagnosis: Diagnosis, fingerprint_label: str
    ) -> None:
        """Create a new GitHub issue for a signature with no existing open issue."""
        issue_title = self._format_issue_title(signature)
        issue_body = self._format_issue_body(signature, diagnosis)
        labels = self._build_labels(signature, fingerprint_label)

        try:
            client = await self._get_client()

            response = await client.post(
                f"/repos/{self.repo_owner}/{self.repo_name}/issues",
                json={
                    "title": issue_title,
                    "body": issue_body,
                    "labels": labels,
                    "assignees": self.assignees,
                },
            )

            response.raise_for_status()

            issue_data = response.json()
            logger.info(
                f"Created GitHub issue #{issue_data['number']}",
                extra={
                    "signature_id": signature.id,
                    "issue_number": issue_data["number"],
                    "issue_url": issue_data["html_url"],
                },
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to create GitHub issue: {e.response.status_code}",
                extra={
                    "signature_id": signature.id,
                    "response": e.response.text,
                },
                exc_info=True,
            )
            raise
        except httpx.RequestError as e:
            logger.error(
                f"Failed to create GitHub issue: {e}",
                extra={"signature_id": signature.id},
                exc_info=True,
            )
            raise

    async def _post_recurrence_comment(
        self, issue_number: int, signature: Signature
    ) -> None:
        """Post a recurrence comment to an existing open issue for this fingerprint."""
        comment_body = self._format_recurrence_comment(signature)

        try:
            client = await self._get_client()

            response = await client.post(
                f"/repos/{self.repo_owner}/{self.repo_name}/issues/{issue_number}/comments",
                json={"body": comment_body},
            )

            response.raise_for_status()

            logger.info(
                f"Posted recurrence comment on GitHub issue #{issue_number}",
                extra={
                    "signature_id": signature.id,
                    "issue_number": issue_number,
                },
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to post recurrence comment: {e.response.status_code}",
                extra={
                    "signature_id": signature.id,
                    "issue_number": issue_number,
                    "response": e.response.text,
                },
                exc_info=True,
            )
            raise
        except httpx.RequestError as e:
            logger.error(
                f"Failed to post recurrence comment: {e}",
                extra={"signature_id": signature.id, "issue_number": issue_number},
                exc_info=True,
            )
            raise

    @staticmethod
    def _fingerprint_label(fingerprint: str) -> str:
        """Derive a GitHub label encoding the signature's fingerprint.

        Truncated to stay within GitHub's 50-character label limit; 16 hex
        characters (64 bits) of the sha256 fingerprint is ample to avoid
        collisions between distinct failure patterns.
        """
        return f"fingerprint:{fingerprint[:16]}"

    def _build_labels(self, signature: Signature, fingerprint_label: str) -> list[str]:
        """Build the full label set for a newly created issue.

        Combines the fixed triage scheme (rounds, auto-detected, severity,
        service, fingerprint) with any configured extra labels, de-duplicated
        while preserving order.
        """
        labels = [
            "rounds",
            "auto-detected",
            f"severity-{signature.max_severity.value.lower()}",
            f"service-{signature.service}",
            fingerprint_label,
            *self.labels,
        ]
        return list(dict.fromkeys(labels))

    @staticmethod
    def _format_recurrence_comment(signature: Signature) -> str:
        """Format a recurrence comment for an existing open issue.

        Args:
            signature: The signature that recurred.

        Returns:
            Formatted markdown comment body.
        """
        lines = [
            "## Recurrence Detected",
            "",
            f"- **Occurrence Count**: {signature.occurrence_count}",
            f"- **Latest Occurrence**: {signature.last_seen.isoformat()}",
            f"- **Service**: {signature.service}",
            f"- **Severity**: {signature.max_severity.value}",
            "",
            "_Generated by Rounds diagnostic system_",
        ]
        return "\n".join(lines)

    async def report_summary(self, stats: dict[str, Any]) -> None:
        """Periodic summary report via GitHub issue.

        Creates an issue with diagnostic summary statistics.

        Args:
            stats: Dictionary with summary statistics.
        """
        issue_title = "Rounds Diagnostic Summary Report"
        issue_body = self._format_summary_body(stats)

        try:
            client = await self._get_client()

            # Create the summary issue
            response = await client.post(
                f"/repos/{self.repo_owner}/{self.repo_name}/issues",
                json={
                    "title": issue_title,
                    "body": issue_body,
                    "labels": [*self.labels, "type:report"],
                },
            )

            response.raise_for_status()

            issue_data = response.json()
            logger.info(
                f"Created summary GitHub issue #{issue_data['number']}",
                extra={
                    "issue_number": issue_data["number"],
                    "issue_url": issue_data["html_url"],
                },
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to create summary GitHub issue: {e.response.status_code}",
                extra={"response": e.response.text},
                exc_info=True,
            )
            raise
        except httpx.RequestError as e:
            logger.error(f"Failed to create summary GitHub issue: {e}", exc_info=True)
            raise

    @staticmethod
    def _format_issue_title(signature: Signature) -> str:
        """Format issue title from signature.

        Args:
            signature: The signature that was diagnosed.

        Returns:
            Formatted issue title.
        """
        return f"[{signature.service}] {signature.error_type}: {signature.message_template[:60]}"

    @staticmethod
    def _format_issue_body(signature: Signature, diagnosis: Diagnosis) -> str:
        """Format issue body with diagnosis details.

        Args:
            signature: The signature that was diagnosed.
            diagnosis: The diagnosis results.

        Returns:
            Formatted markdown issue body.
        """
        lines = []

        lines.append("## Error Information")
        lines.append(f"- **Error Type**: {signature.error_type}")
        lines.append(f"- **Service**: {signature.service}")
        lines.append(f"- **Status**: {signature.status.value}")
        lines.append(f"- **Occurrences**: {signature.occurrence_count}")
        lines.append("")

        lines.append("## Failure Pattern")
        lines.append("```")
        lines.append(f"{signature.message_template}")
        lines.append("```")
        lines.append("")

        lines.append("## Root Cause Analysis")
        lines.append(f"**Confidence**: {diagnosis.confidence.upper()}")
        lines.append("")
        lines.append("### Root Cause")
        lines.append(f"{diagnosis.root_cause}")
        lines.append("")

        lines.append("### Evidence")
        for i, evidence in enumerate(diagnosis.evidence, 1):
            lines.append(f"{i}. {evidence}")
        lines.append("")

        lines.append("### Suggested Fix")
        lines.append(f"{diagnosis.suggested_fix}")
        lines.append("")

        lines.append("## Metadata")
        lines.append(f"- **Signature ID**: {signature.id}")
        lines.append(f"- **Fingerprint**: `{signature.fingerprint}`")
        lines.append(f"- **First Seen**: {signature.first_seen.isoformat()}")
        lines.append(f"- **Last Seen**: {signature.last_seen.isoformat()}")
        lines.append(f"- **Model**: {diagnosis.model}")
        lines.append(f"- **Cost**: ${diagnosis.cost_usd:.2f}")
        lines.append("")

        lines.append("_Generated by Rounds diagnostic system_")

        return "\n".join(lines)

    async def report_alert(self, alert: dict[str, Any]) -> None:
        """Create a GitHub issue for an operational alert.

        Args:
            alert: Dictionary describing the alert event.
        """
        alert_type = alert.get("alert", "unknown")
        issue_title = f"[Rounds Alert] {alert_type}"
        issue_body = self._format_alert_body(alert)

        try:
            client = await self._get_client()

            response = await client.post(
                f"/repos/{self.repo_owner}/{self.repo_name}/issues",
                json={
                    "title": issue_title,
                    "body": issue_body,
                    "labels": [*self.labels, "type:alert"],
                },
            )

            response.raise_for_status()

            issue_data = response.json()
            logger.info(
                f"Created alert GitHub issue #{issue_data['number']}",
                extra={
                    "issue_number": issue_data["number"],
                    "issue_url": issue_data["html_url"],
                    "alert_type": alert_type,
                },
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to create alert GitHub issue: {e.response.status_code}",
                extra={"response": e.response.text},
                exc_info=True,
            )
            raise
        except httpx.RequestError as e:
            logger.error(f"Failed to create alert GitHub issue: {e}", exc_info=True)
            raise

    @staticmethod
    def _format_alert_body(alert: dict[str, Any]) -> str:
        """Format alert as markdown issue body."""
        lines = ["## Rounds Operational Alert", ""]
        if "message" in alert:
            lines.extend([alert["message"], ""])
        lines.append("### Details")
        for key, value in alert.items():
            if key != "message":
                lines.append(f"- **{key}**: {value}")
        lines.extend(["", "_Generated by Rounds diagnostic system_"])
        return "\n".join(lines)

    @staticmethod
    def _format_summary_body(stats: dict[str, Any]) -> str:
        """Format summary report as markdown issue body.

        Args:
            stats: Dictionary with summary statistics.

        Returns:
            Formatted markdown issue body.
        """
        lines = []

        lines.append("## Diagnostic Summary")
        lines.append("")

        lines.append("### Statistics")
        lines.append(f"- **Total Signatures**: {stats.get('total_signatures', 0)}")
        lines.append(f"- **Total Errors Seen**: {stats.get('total_errors_seen', 0)}")
        lines.append("")

        by_status = stats.get("by_status", {})
        if by_status:
            lines.append("### By Status")
            for status, count in sorted(by_status.items()):
                lines.append(f"- **{status.upper()}**: {count}")
            lines.append("")

        by_service = stats.get("by_service", {})
        if by_service:
            lines.append("### By Service (Top 10)")
            for service, count in sorted(by_service.items(), key=lambda x: -x[1])[:10]:
                lines.append(f"- **{service}**: {count}")
            lines.append("")

        lines.append("_Generated by Rounds diagnostic system_")

        return "\n".join(lines)
