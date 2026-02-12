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
        """Report a diagnosed signature by creating a GitHub issue."""
        issue_title = self._format_issue_title(signature)
        issue_body = self._format_issue_body(signature, diagnosis)

        try:
            client = await self._get_client()

            # Create the issue
            response = await client.post(
                f"/repos/{self.repo_owner}/{self.repo_name}/issues",
                json={
                    "title": issue_title,
                    "body": issue_body,
                    "labels": self.labels,
                    "assignees": self.assignees,
                },
            )

            if response.status_code == 201:
                issue_data = response.json()
                logger.info(
                    f"Created GitHub issue #{issue_data['number']}",
                    extra={
                        "signature_id": signature.id,
                        "issue_number": issue_data["number"],
                        "issue_url": issue_data["html_url"],
                    },
                )
            else:
                logger.error(
                    f"Failed to create GitHub issue: {response.status_code}",
                    extra={
                        "signature_id": signature.id,
                        "response": response.text,
                    },
                )

        except httpx.RequestError as e:
            logger.error(
                f"Failed to create GitHub issue: {e}",
                extra={"signature_id": signature.id},
            )
            raise

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
                    "labels": self.labels + ["type:report"],
                },
            )

            if response.status_code == 201:
                issue_data = response.json()
                logger.info(
                    f"Created summary GitHub issue #{issue_data['number']}",
                    extra={
                        "issue_number": issue_data["number"],
                        "issue_url": issue_data["html_url"],
                    },
                )
            else:
                logger.error(
                    f"Failed to create summary GitHub issue: {response.status_code}",
                    extra={"response": response.text},
                )

        except httpx.RequestError as e:
            logger.error(f"Failed to create summary GitHub issue: {e}")
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
        lines.append(f"```")
        lines.append(f"{signature.message_template}")
        lines.append(f"```")
        lines.append("")

        lines.append("## Root Cause Analysis")
        lines.append(f"**Confidence**: {diagnosis.confidence.value.upper()}")
        lines.append("")
        lines.append(f"### Root Cause")
        lines.append(f"{diagnosis.root_cause}")
        lines.append("")

        lines.append(f"### Evidence")
        for i, evidence in enumerate(diagnosis.evidence, 1):
            lines.append(f"{i}. {evidence}")
        lines.append("")

        lines.append(f"### Suggested Fix")
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
