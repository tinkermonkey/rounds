"""Composite notification adapter.

Fans a single NotificationPort call out to multiple notification channels
concurrently, so e.g. GitHub issue creation and phone-home alerting can run
side by side for the same diagnosis without either channel needing to know
about the other. Wired up in the main.py composition root when more than
one channel is configured.
"""

import asyncio
import logging
from collections.abc import Awaitable, Sequence
from typing import Any

from rounds.core.models import Diagnosis, Signature
from rounds.core.ports import NotificationPort

logger = logging.getLogger(__name__)


class CompositeNotificationAdapter(NotificationPort):
    """Dispatches every NotificationPort call to a fixed list of channels concurrently.

    Each channel is invoked independently: one channel's failure is logged
    but does not prevent the others from running. If any channel raises,
    the first exception encountered is re-raised after all channels have
    completed, so callers still observe that something failed.
    """

    def __init__(self, channels: list[NotificationPort]):
        """Initialize with the channels to dispatch to.

        Args:
            channels: Notification channels to fan out to. Must be non-empty.
        """
        if not channels:
            raise ValueError("CompositeNotificationAdapter requires at least one channel")
        self.channels = channels

    async def _dispatch(self, label: str, awaitables: Sequence[Awaitable[None]]) -> None:
        """Run awaitables concurrently, logging and re-raising the first failure."""
        results = await asyncio.gather(*awaitables, return_exceptions=True)
        first_error: BaseException | None = None
        for channel, result in zip(self.channels, results):
            if isinstance(result, BaseException):
                logger.error(
                    f"Notification channel {type(channel).__name__} failed during {label}: {result}",
                    exc_info=result,
                )
                if first_error is None:
                    first_error = result
        if first_error is not None:
            raise first_error

    async def report(self, signature: Signature, diagnosis: Diagnosis) -> None:
        """Report a diagnosed signature to every configured channel concurrently."""
        await self._dispatch("report", [c.report(signature, diagnosis) for c in self.channels])

    async def report_summary(self, stats: dict[str, Any]) -> None:
        """Report summary statistics to every configured channel concurrently."""
        await self._dispatch("report_summary", [c.report_summary(stats) for c in self.channels])

    async def report_alert(self, alert: dict[str, Any]) -> None:
        """Report an operational alert to every configured channel concurrently."""
        await self._dispatch("report_alert", [c.report_alert(alert) for c in self.channels])

    async def close_resolved_issue(self, signature: Signature) -> None:
        """Close whatever open issue/thread each channel created for this signature."""
        await self._dispatch(
            "close_resolved_issue", [c.close_resolved_issue(signature) for c in self.channels]
        )

    async def close(self) -> None:
        """Close every channel, logging (but not raising on) individual failures.

        Cleanup should be best-effort: a failure to close one channel should
        not prevent the others from releasing their resources.
        """
        results = await asyncio.gather(
            *(channel.close() for channel in self.channels), return_exceptions=True
        )
        for channel, result in zip(self.channels, results):
            if isinstance(result, BaseException):
                logger.error(
                    f"Notification channel {type(channel).__name__} failed to close: {result}",
                    exc_info=result,
                )
