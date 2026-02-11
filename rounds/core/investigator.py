"""Investigation orchestration for the diagnostic system.

This module coordinates the analysis of failure signatures by
orchestrating interactions between the core domain logic and
external diagnosis services.
"""

import logging

from .models import Diagnosis, InvestigationContext, Signature, SignatureStatus
from .ports import DiagnosisPort, NotificationPort, SignatureStorePort, TelemetryPort
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
    ):
        self.telemetry = telemetry
        self.store = store
        self.diagnosis_engine = diagnosis_engine
        self.notification = notification
        self.triage = triage
        self.codebase_path = codebase_path

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
        traces = await self.telemetry.get_traces(
            [e.trace_id for e in events]
        )
        logs = await self.telemetry.get_correlated_logs(
            [e.trace_id for e in events], window_minutes=5
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

        # 3. Send to diagnosis engine
        signature.status = SignatureStatus.INVESTIGATING
        await self.store.update(signature)

        try:
            diagnosis = await self.diagnosis_engine.diagnose(context)

            # 4. Record result
            signature.diagnosis = diagnosis
            signature.status = SignatureStatus.DIAGNOSED
            await self.store.update(signature)

            # 5. Notify if warranted
            if self.triage.should_notify(signature, diagnosis):
                await self.notification.report(signature, diagnosis)

            return diagnosis

        except Exception as e:
            # Revert signature status to NEW on diagnosis failure
            signature.status = SignatureStatus.NEW
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
