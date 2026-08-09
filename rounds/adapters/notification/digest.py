"""WARN-level digest notification adapter.

Wraps another NotificationPort and defers batch-qualifying diagnoses (see
TriageEngine.should_batch()) into a periodic digest instead of sending an
individual notification for each one. Everything else passes straight
through to the wrapped channel unchanged.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from rounds.core.models import Confidence, Diagnosis, Signature
from rounds.core.ports import NotificationPort
from rounds.core.triage import TriageEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DigestEntry:
    """Snapshot of a batched diagnosis, captured at buffering time.

    Signature is mutable, so the fields the digest needs are copied out
    immediately rather than holding a reference that could change (e.g. be
    resolved or re-tagged) before the digest is flushed.
    """

    signature_id: str
    fingerprint: str
    service: str
    error_type: str
    confidence: Confidence


class DigestNotificationAdapter(NotificationPort):
    """Buffers batch-qualifying diagnoses and reports them as a periodic digest.

    Delegates every NotificationPort call to `inner` unchanged, except
    `report()`: when `enabled` and `triage.should_batch()` classifies the
    diagnosis as batch-qualifying, it's buffered instead of forwarded, and
    `report()` returns None — consistent with the "suppressed by the
    channel's own gating logic" case already documented on
    NotificationPort.report().

    Buffered entries accumulate until `flush_if_due()` finds the window has
    elapsed — see DaemonScheduler, which owns the flush timer and calls it
    on a schedule independent of the poll interval.
    """

    def __init__(
        self,
        inner: NotificationPort,
        triage: TriageEngine,
        enabled: bool = True,
        window_start: datetime | None = None,
    ):
        """Initialize the digest adapter.

        Args:
            inner: The wrapped NotificationPort that immediate notifications
                and the flushed digest summary are delegated to.
            triage: TriageEngine used to classify diagnoses via should_batch().
            enabled: When False, report() always delegates to inner
                (current per-diagnosis behavior, unchanged).
            window_start: Timestamp the first digest window opens at.
                Defaults to now.
        """
        self.inner = inner
        self.triage = triage
        self.enabled = enabled
        self._buffer: list[_DigestEntry] = []
        self._window_start = window_start if window_start is not None else datetime.now(UTC)
        self._lock = asyncio.Lock()

    async def report(self, signature: Signature, diagnosis: Diagnosis) -> datetime | None:
        """Buffer batch-qualifying diagnoses; forward everything else immediately."""
        if self.enabled and self.triage.should_batch(signature, diagnosis):
            entry = _DigestEntry(
                signature_id=signature.id,
                fingerprint=signature.fingerprint,
                service=signature.service,
                error_type=signature.error_type,
                confidence=diagnosis.confidence,
            )
            async with self._lock:
                self._buffer.append(entry)
            logger.debug(
                f"Batched diagnosis for signature {signature.fingerprint} "
                f"(service={signature.service}) into WARN digest"
            )
            return None

        return await self.inner.report(signature, diagnosis)

    async def report_summary(self, stats: dict[str, Any]) -> None:
        """Pass through unchanged — the digest has its own summary path (flush_if_due)."""
        await self.inner.report_summary(stats)

    async def report_alert(self, alert: dict[str, Any]) -> None:
        """Pass through unchanged — operational alerts are never batched."""
        await self.inner.report_alert(alert)

    async def close_resolved_issue(self, signature: Signature) -> None:
        """Pass through unchanged."""
        await self.inner.close_resolved_issue(signature)

    async def close(self) -> None:
        """Pass through unchanged."""
        await self.inner.close()

    @property
    def pending_count(self) -> int:
        """Number of diagnoses currently buffered for the next digest."""
        return len(self._buffer)

    async def flush_if_due(self, now: datetime, window: timedelta) -> bool:
        """Flush the digest if at least `window` has elapsed since it opened.

        Always advances the window once it's due, even when the buffer is
        empty, so windows close and reopen on schedule regardless of whether
        any diagnoses were batched during that window — this is what keeps
        behavior deterministic (no double-counting, no dropped diagnoses)
        across a day boundary under continuous daemon operation.

        Args:
            now: Current timestamp.
            window: Digest window duration (e.g. configured digest interval).

        Returns:
            True if the window was due and flushed (the buffer may have been
            empty), False if the window hasn't elapsed yet.
        """
        async with self._lock:
            if now - self._window_start < window:
                return False
            entries = self._buffer
            self._buffer = []
            window_start = self._window_start
            self._window_start = now

        if entries:
            stats = self._build_digest_stats(entries, window_start, now)
            await self.inner.report_summary(stats)
            logger.info(
                f"Flushed WARN digest: {len(entries)} diagnoses across "
                f"{len(stats['services'])} services"
            )
        return True

    @staticmethod
    def _build_digest_stats(
        entries: list[_DigestEntry], window_start: datetime, window_end: datetime
    ) -> dict[str, Any]:
        """Build the report_summary() payload for a flushed digest window."""
        return {
            "type": "warn_digest",
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "count": len(entries),
            "services": sorted({entry.service for entry in entries}),
            "signatures": [
                {
                    "signature_id": entry.signature_id,
                    "fingerprint": entry.fingerprint,
                    "service": entry.service,
                    "error_type": entry.error_type,
                    "confidence": entry.confidence,
                }
                for entry in entries
            ],
        }
