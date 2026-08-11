"""WARN-level digest notification adapter.

Wraps another NotificationPort and defers batch-qualifying diagnoses (see
TriageEngine.should_batch()) into a periodic digest instead of sending an
individual notification for each one. Everything else passes straight
through to the wrapped channel unchanged.
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from rounds.core.models import Confidence, Diagnosis, Signature, SignatureStatus
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
    status: SignatureStatus
    confidence: Confidence


class DigestNotificationAdapter(NotificationPort):
    """Buffers batch-qualifying diagnoses and reports them as a periodic digest.

    Delegates every NotificationPort call to `inner` unchanged, except
    `report()`: when `immediate` is not set, `enabled` is True, and
    `triage.should_batch()` classifies the diagnosis as batch-qualifying,
    it's buffered instead of forwarded, and `report()` returns None —
    consistent with the "suppressed by the channel's own gating logic" case
    already documented on NotificationPort.report(). `report(..., immediate=True)`
    always bypasses batching, for callers (e.g. reinvestigate()) that require
    immediate delivery regardless of confidence or severity. `close()` is
    also special: it flushes any still-buffered entries before delegating,
    so shutdown never silently discards a partially-filled window.

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

    async def report(
        self, signature: Signature, diagnosis: Diagnosis, *, immediate: bool = False
    ) -> datetime | None:
        """Buffer batch-qualifying diagnoses; forward everything else immediately.

        `immediate=True` bypasses batching entirely — used by user-initiated
        calls (e.g. reinvestigate()) that must always be delivered right
        away, never silently deferred into the next digest window.
        """
        if not immediate and self.enabled and self.triage.should_batch(signature, diagnosis):
            entry = _DigestEntry(
                signature_id=signature.id,
                fingerprint=signature.fingerprint,
                service=signature.service,
                error_type=signature.error_type,
                status=signature.status,
                confidence=diagnosis.confidence,
            )
            async with self._lock:
                self._buffer.append(entry)
            logger.debug(
                f"Batched diagnosis for signature {signature.fingerprint} "
                f"(service={signature.service}) into WARN digest"
            )
            return None

        return await self.inner.report(signature, diagnosis, immediate=immediate)

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
        """Flush any buffered digest entries before delegating to inner.close().

        Without this, diagnoses batched into an open window are silently
        discarded on daemon shutdown. Flush failure is logged, not raised,
        so a broken digest channel never prevents `inner.close()` from
        running and releasing its own resources.
        """
        async with self._lock:
            entries = self._buffer
            self._buffer = []
            window_start = self._window_start

        if entries:
            now = datetime.now(UTC)
            stats = self._build_digest_stats(entries, window_start, now)
            try:
                await self.inner.report_summary(stats)
            except Exception:
                async with self._lock:
                    self._buffer = entries + self._buffer
                logger.error(
                    f"Failed to flush WARN digest on shutdown "
                    f"({len(entries)} diagnoses may be lost)",
                    exc_info=True,
                )
            else:
                logger.info(
                    f"Flushed WARN digest on shutdown: {len(entries)} diagnoses across "
                    f"{len(stats['services'])} services"
                )

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

        Raises:
            Exception: Whatever `inner.report_summary()` raises. On failure,
                the flushed entries and window start are restored so nothing
                is lost — the next due check retries the same window rather
                than silently dropping the batched diagnoses.
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
            try:
                await self.inner.report_summary(stats)
            except Exception:
                async with self._lock:
                    self._buffer = entries + self._buffer
                    self._window_start = window_start
                raise
            logger.info(
                f"Flushed WARN digest: {len(entries)} diagnoses across "
                f"{len(stats['services'])} services"
            )
        return True

    @staticmethod
    def _build_digest_stats(
        entries: list[_DigestEntry], window_start: datetime, window_end: datetime
    ) -> dict[str, Any]:
        """Build the report_summary() payload for a flushed digest window.

        Includes both the digest-native fields (type, window_start/end, count,
        services, signatures) and the total_signatures/total_errors_seen/
        by_status/by_service fields that every report_summary() implementation
        (stdout, markdown, github_issues) actually renders via dict.get(). Without
        the latter, those adapters silently fall back to their zero/empty
        defaults and a flushed digest produces an invisible, all-zero summary.

        by_status carries the real Signature.status breakdown (new/investigating/
        diagnosed/...), matching every other producer of by_status in the codebase
        (StoreStats, the OTEL count_by_status gauge). Confidence is a distinct
        dimension and is reported separately under by_confidence, which the
        stdout/markdown/github_issues formatters render as its own section.

        total_signatures counts unique signatures (a signature re-diagnosed
        multiple times within one digest window still counts once), while
        total_errors_seen counts every batched diagnosis in the window.
        """
        by_service: dict[str, int] = defaultdict(int)
        by_status: dict[str, int] = defaultdict(int)
        by_confidence: dict[str, int] = defaultdict(int)
        unique_signature_ids: set[str] = set()
        for entry in entries:
            by_service[entry.service] += 1
            by_status[entry.status.value] += 1
            by_confidence[entry.confidence] += 1
            unique_signature_ids.add(entry.signature_id)

        return {
            "type": "warn_digest",
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "count": len(entries),
            "services": sorted(by_service),
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
            "total_signatures": len(unique_signature_ids),
            "total_errors_seen": len(entries),
            "by_status": dict(by_status),
            "by_confidence": dict(by_confidence),
            "by_service": dict(by_service),
        }
