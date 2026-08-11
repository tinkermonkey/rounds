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
from datetime import datetime
from typing import Any

from rounds.core.models import Diagnosis, Signature
from rounds.core.ports import NotificationPort, PartialNotificationError

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

    async def _gather(
        self, label: str, awaitables: Sequence[Awaitable[Any]]
    ) -> tuple[list[Any], BaseException | None]:
        """Run awaitables concurrently, logging every failure.

        Returns the return values of the channels that succeeded (in channel
        order) alongside the first exception encountered, if any. Unlike
        `_dispatch`, this never raises - callers that need to inspect the
        successes before deciding whether/how to propagate the failure
        (e.g. `report()`) should use this directly.
        """
        results = await asyncio.gather(*awaitables, return_exceptions=True)
        first_error: BaseException | None = None
        successes: list[Any] = []
        for channel, result in zip(self.channels, results):
            if isinstance(result, BaseException):
                logger.error(
                    f"Notification channel {type(channel).__name__} failed during {label}: {result}",
                    exc_info=result,
                )
                if first_error is None:
                    first_error = result
            else:
                successes.append(result)
        return successes, first_error

    async def _dispatch(self, label: str, awaitables: Sequence[Awaitable[Any]]) -> list[Any]:
        """Run awaitables concurrently, logging and re-raising the first failure.

        Returns the return values of the channels that succeeded, in channel order.
        """
        successes, first_error = await self._gather(label, awaitables)
        if first_error is not None:
            raise first_error
        return successes

    async def report(
        self, signature: Signature, diagnosis: Diagnosis, *, immediate: bool = False
    ) -> datetime | None:
        """Report a diagnosed signature to every configured channel concurrently.

        Returns the first non-None alert timestamp among the channels' results
        (in practice, at most one configured channel implements cooldown-gated
        alerting), or None if no channel returned one.

        Args:
            signature: The signature that was diagnosed.
            diagnosis: The diagnosis results to report.
            immediate: Forwarded to every channel unchanged - see
                NotificationPort.report().

        Raises:
            PartialNotificationError: If a sibling channel failed but
                another channel already returned an alert timestamp. Callers
                must extract `alerted_at` from the exception and record it
                before propagating, since that alert was already delivered.
        """
        successes, first_error = await self._gather(
            "report",
            [c.report(signature, diagnosis, immediate=immediate) for c in self.channels],
        )
        alerted_at = next((r for r in successes if r is not None), None)
        if first_error is not None:
            if alerted_at is not None:
                raise PartialNotificationError(alerted_at, first_error) from first_error
            raise first_error
        return alerted_at

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
