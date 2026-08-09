"""Investigation orchestration for the diagnostic system.

This module coordinates the analysis of failure signatures by
orchestrating interactions between the core domain logic and
external diagnosis services.
"""

import logging

from .alert_cooldown import persist_alert_cooldown
from .models import Diagnosis, InvestigationContext, Signature
from .ports import (
    BudgetTracker,
    DiagnosisPort,
    NotificationPort,
    PartialNotificationError,
    SignatureStorePort,
    TelemetryPort,
)
from .triage import TriageEngine

logger = logging.getLogger(__name__)


class Investigator:
    """Orchestrates the investigation of a signature.

    Uses ports but contains no adapter-specific logic.
    """

    def __init__(
        self,
        telemetry: TelemetryPort,
        store: SignatureStorePort,
        diagnosis_engine: DiagnosisPort,
        notification: NotificationPort,
        triage: TriageEngine,
        codebase_path: str,
        budget_tracker: BudgetTracker | None = None,
    ):
        self.telemetry = telemetry
        self.store = store
        self.diagnosis_engine = diagnosis_engine
        self.notification = notification
        self.triage = triage
        self.codebase_path = codebase_path
        self.budget_tracker = budget_tracker

    async def investigate(self, signature: Signature) -> Diagnosis:
        """Assemble context, request diagnosis, store result, notify.

        Steps:
        1. Gather evidence via telemetry port
        2. Build investigation context
        3. Mark as investigating and send to diagnosis engine
        4. Record result
        5. Notify if warranted

        If diagnosis fails, the signature status is reverted to NEW
        and an error is logged.

        Raises:
            Any exception from diagnosis_engine.diagnose() is re-raised
            after reverting signature status and logging.
        """
        # 1. Gather evidence via telemetry port
        events = await self.telemetry.get_events_for_signature(
            signature.fingerprint, limit=5
        )
        trace_ids = [e.trace_id for e in events]
        traces, partial_info = await self.telemetry.get_traces(trace_ids)

        # Log if trace retrieval was incomplete
        if partial_info.is_partial:
            logger.warning(
                f"Incomplete trace data for signature {signature.fingerprint}: "
                f"retrieved {partial_info.total_returned} of {partial_info.total_requested} traces. "
                f"Reason: {partial_info.reason}. Proceeding with partial context."
            )

        logs = await self.telemetry.get_correlated_logs(
            trace_ids, window_minutes=5
        )

        # 2. Build investigation context
        context = InvestigationContext(
            signature=signature,
            recent_events=tuple(events),
            trace_data=tuple(traces),
            related_logs=tuple(logs),
            codebase_path=self.codebase_path,
            historical_context=tuple(await self.store.get_similar(signature)),
        )

        # Save original status before mutation for notification logic
        original_status = signature.status

        # 3. Send to diagnosis engine
        signature.mark_investigating()
        await self.store.update(signature)

        try:
            diagnosis = await self.diagnosis_engine.diagnose(context)
        except Exception as e:
            # Diagnosis failed - revert status and re-raise
            signature.revert_to_new()
            try:
                await self.store.update(signature)
            except Exception as store_error:
                logger.error(
                    f"Failed to revert signature status after diagnosis failure: "
                    f"{store_error}",
                    exc_info=True,
                )
            # Log the original diagnosis error and re-raise
            logger.error(
                f"Diagnosis failed for signature {signature.fingerprint}: {e}",
                exc_info=True,
            )
            raise

        # 4. Record cost if budget tracker available
        if self.budget_tracker:
            await self.budget_tracker.record_cost(
                "diagnose", diagnosis.cost_usd, service=signature.service
            )

        # 5. Persist diagnosis before notification
        # IMPORTANT: Check notification BEFORE changing status to DIAGNOSED
        # so that medium-confidence NEW signatures can still notify
        signature.mark_diagnosed(diagnosis)
        try:
            await self.store.update(signature)
        except Exception as e:
            logger.error(
                f"Failed to persist diagnosis for signature {signature.fingerprint}: {e}",
                exc_info=True,
            )
            raise

        # 6. Confirm: decide whether the diagnosis is worth notifying about
        # (failure here should NOT revert the persisted diagnosis)
        # Pass original status to should_notify for correct medium-confidence logic
        if self.budget_tracker:
            # Triage is pure decision logic today (no LLM call), so this is
            # always 0.0 - recorded so the confirm step is represented in
            # per-step cost accounting alongside poll/fingerprint/diagnose.
            await self.budget_tracker.record_cost(
                "confirm", 0.0, service=signature.service
            )

        notification_error: Exception | None = None
        try:
            if self.triage.should_notify(signature, diagnosis, original_status=original_status):
                alerted_at = await self.notification.report(signature, diagnosis)
                if alerted_at is not None:
                    signature.record_alert(alerted_at)
                    await persist_alert_cooldown(self.store, signature)
        except PartialNotificationError as e:
            # A sibling channel failed, but this channel already delivered its
            # alert - the cooldown must still be recorded or the next poll
            # cycle will re-send a duplicate, even though we're about to
            # surface the sibling's failure to the caller below.
            signature.record_alert(e.alerted_at)
            await persist_alert_cooldown(self.store, signature)
            logger.error(
                f"Notification partially failed for signature {signature.fingerprint}: {e}",
                exc_info=True,
            )
            notification_error = e
        except Exception as e:
            # Log notification failure but don't revert the successful diagnosis
            logger.error(
                f"Failed to notify about diagnosis for signature {signature.fingerprint}: {e}",
                exc_info=True,
            )
            notification_error = e

        # Re-raise notification error to expose it to caller
        if notification_error:
            raise notification_error

        return diagnosis
