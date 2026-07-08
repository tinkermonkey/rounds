"""Poll cycle logic for the diagnostic system.

This module implements the main polling loop that continuously
retrieves errors from telemetry, fingerprints them, and coordinates
investigations.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from .fingerprint import Fingerprinter
from .investigator import Investigator
from .models import (
    InvestigationResult,
    PollResult,
    ResolutionResult,
    Signature,
    SignatureStatus,
)
from .ports import BudgetTracker, NotificationPort, PollPort, SignatureStorePort, TelemetryPort
from .triage import TriageEngine

logger = logging.getLogger(__name__)


class PollService(PollPort):
    """Implements the poll cycle logic.

    This service orchestrates:
    - Polling telemetry for errors
    - Fingerprinting new errors
    - Deduplicating against known signatures
    - Queueing investigations
    """

    def __init__(
        self,
        telemetry: TelemetryPort,
        store: SignatureStorePort,
        fingerprinter: Fingerprinter,
        triage: TriageEngine,
        investigator: Investigator,
        lookback_minutes: int = 15,
        services: list[str] | None = None,
        batch_size: int | None = None,
        budget_tracker: BudgetTracker | None = None,
        notification: NotificationPort | None = None,
        resolution_threshold_hours_default: int = 24,
    ):
        self.telemetry = telemetry
        self.store = store
        self.fingerprinter = fingerprinter
        self.triage = triage
        self.investigator = investigator
        self.lookback_minutes = lookback_minutes
        self.services = services
        self.batch_size = batch_size
        self.budget_tracker = budget_tracker
        self.notification = notification
        self.resolution_threshold_hours_default = resolution_threshold_hours_default

    async def execute_poll_cycle(self) -> PollResult:
        """Check for new errors, fingerprint, dedup, and queue investigations.

        Returns a summary of what was found.

        Raises:
            Exception: If telemetry fetch fails. Errors are not silenced.
        """
        now = datetime.now(UTC)
        since = now - timedelta(minutes=self.lookback_minutes)

        try:
            errors = await self.telemetry.get_recent_errors(since, self.services)
        except Exception as e:
            logger.error(f"Failed to fetch recent errors from telemetry: {e}", exc_info=True)
            raise

        # Limit errors to batch_size if configured
        if self.batch_size is not None and len(errors) > self.batch_size:
            logger.info(
                f"Limiting poll to {self.batch_size} errors "
                f"(found {len(errors)}, taking first {self.batch_size})"
            )
            errors = errors[: self.batch_size]

        new_signatures = 0
        updated_signatures = 0
        investigations_queued = 0
        errors_failed_to_process = 0

        for error in errors:
            try:
                # Fingerprint the error
                fingerprint = self.fingerprinter.fingerprint(error)

                # Check if we've seen this signature before
                signature = await self.store.get_by_fingerprint(fingerprint)

                if signature is None:
                    # New signature - create it
                    normalized_stack = self.fingerprinter.normalize_stack(
                        error.stack_frames
                    )
                    signature = Signature(
                        id=str(uuid.uuid4()),
                        fingerprint=fingerprint,
                        error_type=error.error_type,
                        service=error.service,
                        message_template=self.fingerprinter.templatize_message(
                            error.error_message
                        ),
                        stack_hash=self.fingerprinter.hash_stack(normalized_stack),
                        first_seen=error.timestamp,
                        last_seen=error.timestamp,
                        occurrence_count=1,
                        status=SignatureStatus.NEW,
                        diagnosis=None,
                        tags=frozenset(),
                        max_severity=error.severity,
                    )
                    await self.store.save(signature)
                    new_signatures += 1
                else:
                    # Update existing signature
                    signature.record_occurrence(error.timestamp, error.severity)
                    await self.store.update(signature)
                    updated_signatures += 1

                # Check if we should investigate
                if self.triage.should_investigate(signature):
                    investigations_queued += 1

            except (MemoryError, SystemExit, KeyboardInterrupt):
                # Don't catch system errors - let them propagate
                raise
            except Exception as e:
                logger.error(
                    f"Failed to process error event {error.trace_id}: {e}",
                    exc_info=True,
                )
                errors_failed_to_process += 1

        if self.budget_tracker:
            # Fetching errors and fingerprinting them are pure telemetry
            # queries and hashing/regex logic today (no LLM calls), so cost
            # is always 0.0 - recorded so these steps appear in per-step
            # cost accounting alongside the diagnose and confirm steps.
            await self.budget_tracker.record_cost("poll", 0.0)
            await self.budget_tracker.record_cost("fingerprint", 0.0)

        return PollResult(
            errors_found=len(errors),
            new_signatures=new_signatures,
            updated_signatures=updated_signatures,
            investigations_queued=investigations_queued,
            timestamp=now,
            errors_failed_to_process=errors_failed_to_process,
        )

    async def execute_investigation_cycle(self) -> InvestigationResult:
        """Investigate pending signatures. Returns result with diagnoses and failure count.

        Raises:
            Exception: If signature store fetch fails. Errors are not silenced.
        """
        try:
            pending_seq = await self.store.get_pending_investigation()
        except Exception as e:
            logger.error(f"Failed to fetch pending signatures: {e}", exc_info=True)
            raise

        # Convert to list and sort by priority
        pending = list(pending_seq)
        pending.sort(
            key=lambda s: self.triage.calculate_priority(s), reverse=True
        )

        diagnoses = []
        investigations_attempted = 0
        investigations_failed = 0

        for signature in pending:
            if self.triage.should_investigate(signature):
                investigations_attempted += 1
                try:
                    diagnosis = await self.investigator.investigate(signature)
                    diagnoses.append(diagnosis)
                except Exception as e:
                    # Check if diagnosis was persisted despite the error
                    # (e.g., notification failure after successful diagnosis)
                    if signature.status == SignatureStatus.DIAGNOSED and signature.diagnosis is not None:
                        # Diagnosis succeeded, only notification failed
                        logger.warning(
                            f"Investigation succeeded for signature {signature.fingerprint} "
                            f"but post-diagnosis step failed: {e}",
                            exc_info=True,
                        )
                        diagnoses.append(signature.diagnosis)
                    else:
                        # Actual investigation failure
                        logger.error(
                            f"Failed to investigate signature {signature.fingerprint}: {e}",
                            exc_info=True,
                        )
                        investigations_failed += 1

        return InvestigationResult(
            diagnoses_produced=tuple(diagnoses),
            investigations_attempted=investigations_attempted,
            investigations_failed=investigations_failed,
        )

    # Tag applied to a RESOLVED signature when closing its notification-channel
    # item (e.g. GitHub issue) fails, so a future resolution cycle can find and
    # retry it instead of leaving it permanently dangling with no operator
    # visibility.
    _ISSUE_CLOSE_PENDING_TAG = "issue-close-pending"

    async def execute_resolution_cycle(self) -> ResolutionResult:
        """Check diagnosed signatures against their resolution threshold and auto-close stale ones.

        For each DIAGNOSED signature, compares its last_seen timestamp against
        its resolution threshold (signature.resolution_threshold_hours if set,
        otherwise resolution_threshold_hours_default). Signatures quiet for at
        least that long are marked RESOLVED and, if a notification port is
        configured, have their corresponding issue closed.

        Before processing newly-stale signatures, also retries closing the
        issue for any already-RESOLVED signature tagged from a previous
        cycle's close failure.

        A failure resolving or closing one signature is logged and does not
        prevent the rest of the cycle from proceeding.

        Raises:
            Exception: If fetching diagnosed signatures from the store fails.
        """
        now = datetime.now(UTC)

        issue_close_failures = 0
        try:
            previously_dangling = await self.store.get_all(status=SignatureStatus.RESOLVED)
        except Exception as e:
            logger.error(
                f"Failed to fetch resolved signatures for issue-close retry: {e}",
                exc_info=True,
            )
            previously_dangling = []

        for signature in previously_dangling:
            if self._ISSUE_CLOSE_PENDING_TAG not in signature.tags:
                continue
            if not await self._close_resolved_issue(signature):
                issue_close_failures += 1

        try:
            diagnosed = await self.store.get_all(status=SignatureStatus.DIAGNOSED)
        except Exception as e:
            logger.error(f"Failed to fetch diagnosed signatures: {e}", exc_info=True)
            raise

        signatures_resolved = 0

        for signature in diagnosed:
            threshold_hours = (
                signature.resolution_threshold_hours
                if signature.resolution_threshold_hours is not None
                else self.resolution_threshold_hours_default
            )
            if now - signature.last_seen < timedelta(hours=threshold_hours):
                continue

            original_status = signature.status
            try:
                signature.mark_resolved()
                await self.store.update(signature)
            except (MemoryError, SystemExit, KeyboardInterrupt):
                raise
            except Exception as e:
                logger.error(
                    f"Failed to auto-resolve signature {signature.fingerprint}: {e}",
                    exc_info=True,
                )
                if await self._was_persisted_as_resolved(signature):
                    # The store write actually committed despite the raised
                    # exception (e.g. a timeout after the write landed) -
                    # trust the persisted state instead of rolling back and
                    # clobbering a successful write with stale data.
                    pass
                else:
                    # The write genuinely never landed, so nothing needs to be
                    # undone in the store - just resync the in-memory object.
                    signature.restore_state(original_status, signature.diagnosis)
                    continue

            signatures_resolved += 1
            if not await self._close_resolved_issue(signature):
                issue_close_failures += 1

        if issue_close_failures:
            logger.error(
                f"{issue_close_failures} signature(s) resolved but failed to close "
                "their notification-channel issue; tagged for retry on the next "
                "resolution cycle"
            )

        return ResolutionResult(
            signatures_resolved=signatures_resolved,
            timestamp=now,
            issue_close_failures=issue_close_failures,
        )

    async def _was_persisted_as_resolved(self, signature: Signature) -> bool:
        """Check the store's ground truth after a failed auto-resolve write.

        A store.update() call can raise after its write already committed
        (e.g. the connection dropped while waiting for the response), so a
        failed lookup here is treated the same as "not persisted" - the
        safest assumption when we can't confirm what's actually stored.
        """
        try:
            persisted = await self.store.get_by_id(signature.id)
        except Exception as e:
            logger.error(
                f"Failed to verify persisted state for signature "
                f"{signature.fingerprint} after auto-resolve failure: {e}",
                exc_info=True,
            )
            return False
        return persisted is not None and persisted.status == SignatureStatus.RESOLVED

    async def _close_resolved_issue(self, signature: Signature) -> bool:
        """Close the notification-channel issue for a resolved signature.

        Returns True on success (or when no notification port is configured).
        On failure, tags the signature so it's retried on a future resolution
        cycle instead of leaving the issue dangling with no way to discover
        it. Tag mutations are persisted immediately so the retry state
        survives process restarts.
        """
        if self.notification is None:
            return True

        try:
            await self.notification.close_resolved_issue(signature)
        except Exception as e:
            logger.error(
                f"Failed to close issue for resolved signature "
                f"{signature.fingerprint}: {e}",
                exc_info=True,
            )
            if self._ISSUE_CLOSE_PENDING_TAG not in signature.tags:
                signature.tags = signature.tags | {self._ISSUE_CLOSE_PENDING_TAG}
                await self._persist_tag_update(signature)
            return False

        if self._ISSUE_CLOSE_PENDING_TAG in signature.tags:
            signature.tags = signature.tags - {self._ISSUE_CLOSE_PENDING_TAG}
            await self._persist_tag_update(signature)
        return True

    async def _persist_tag_update(self, signature: Signature) -> None:
        """Best-effort persistence of a tag change on an already-resolved signature.

        A failure here only means the retry bookkeeping is stale (e.g. a
        previously-failed close gets retried again next cycle, which is
        harmless) - it must not affect the signature's resolved status.
        """
        try:
            await self.store.update(signature)
        except Exception as e:
            logger.error(
                f"Failed to persist issue-close tag update for signature "
                f"{signature.fingerprint}: {e}",
                exc_info=True,
            )
