"""Repo-ownership routing notification adapter.

Determines, per diagnosed signature, whether the affected service's source
repo is owned by the configured GitHub account and dispatches accordingly:
owned repos are routed to a GitHub issue in that repo, everything else
(services with no `service_repo_map` entry, or repos owned by another
account) falls back to a markdown-only notification. This is dispatch
logic sitting in front of GitHubIssueNotificationAdapter — it never talks
to the GitHub API itself, and the ownership check always completes before
any issue-creation call would be attempted.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from rounds.core.models import Diagnosis, Signature
from rounds.core.ports import NotificationPort

logger = logging.getLogger(__name__)

GitHubAdapterFactory = Callable[[str, str], NotificationPort]


class RepoOwnershipNotificationAdapter(NotificationPort):
    """Routes report() to the GitHub repo that owns a signature's service, or falls back to markdown.

    Ownership is resolved from `service_repo_map` before any GitHub API call
    is attempted: the signature's service is looked up for its "owner/repo",
    and that repo is only targeted if its owner matches the configured
    GitHub account. Unmapped services and repos owned by a different account
    always fall back to the markdown channel instead — this adapter never
    guesses a repo or risks creating an issue in an account it doesn't
    control.
    """

    def __init__(
        self,
        service_repo_map: dict[str, str],
        github_account: str,
        fallback: NotificationPort,
        github_adapter_factory: GitHubAdapterFactory,
    ) -> None:
        """Initialize the routing adapter.

        Args:
            service_repo_map: Maps telemetry service name to "owner/repo".
            github_account: The GitHub account rounds is authorized to create issues under.
            fallback: Notification channel used whenever no owned repo can be resolved.
            github_adapter_factory: Builds the notification channel (a
                GitHubIssueNotificationAdapter in production) that targets a given
                (owner, repo). Adapters are created lazily and cached per repo.
        """
        self._service_repo_map = service_repo_map
        self._github_account = github_account
        self._fallback = fallback
        self._github_adapter_factory = github_adapter_factory
        self._github_adapters: dict[tuple[str, str], NotificationPort] = {}

    def resolve_owned_repo(self, service: str) -> tuple[str, str] | None:
        """Return (owner, repo) for the service if it maps to a repo owned by the configured account.

        Returns None when the service has no entry in service_repo_map, the
        entry is malformed, or its repo's owner does not match the
        configured GitHub account.
        """
        mapped = self._service_repo_map.get(service)
        if not mapped or "/" not in mapped:
            return None
        owner, _, repo = mapped.partition("/")
        if not owner or not repo:
            return None
        if not self._github_account or owner.lower() != self._github_account.lower():
            logger.info(
                f"Service '{service}' maps to repo '{mapped}', which is not owned by "
                f"the configured GitHub account '{self._github_account}'; falling back to markdown",
                extra={"service": service, "mapped_repo": mapped},
            )
            return None
        return owner, repo

    def _channel_for(self, service: str) -> NotificationPort:
        """Resolve the notification channel that owns this service's repo, or the fallback."""
        owned = self.resolve_owned_repo(service)
        if owned is None:
            return self._fallback
        owner, repo = owned
        key = (owner, repo)
        if key not in self._github_adapters:
            self._github_adapters[key] = self._github_adapter_factory(owner, repo)
        return self._github_adapters[key]

    async def report(
        self, signature: Signature, diagnosis: Diagnosis, *, immediate: bool = False
    ) -> datetime | None:
        """Route to the owned repo's GitHub issue channel, or markdown if none is owned."""
        return await self._channel_for(signature.service).report(
            signature, diagnosis, immediate=immediate
        )

    async def report_summary(self, stats: dict[str, Any]) -> None:
        """Summaries have no per-service repo to target; always reported via the fallback channel."""
        await self._fallback.report_summary(stats)

    async def report_alert(self, alert: dict[str, Any]) -> None:
        """Operational alerts have no per-service repo to target; always reported via the fallback channel."""
        await self._fallback.report_alert(alert)

    async def close_resolved_issue(self, signature: Signature) -> None:
        """Close the issue in the same channel report() would have targeted for this signature."""
        await self._channel_for(signature.service).close_resolved_issue(signature)

    async def close(self) -> None:
        """Close the fallback channel and every GitHub adapter created for owned repos.

        Cleanup is best-effort: a failure to close one channel is logged but
        does not prevent the others from releasing their resources.
        """
        channels: list[NotificationPort] = [self._fallback, *self._github_adapters.values()]
        results = await asyncio.gather(*(c.close() for c in channels), return_exceptions=True)
        for channel, result in zip(channels, results):
            if isinstance(result, BaseException):
                logger.error(
                    f"Notification channel {type(channel).__name__} failed to close: {result}",
                    exc_info=result,
                )
