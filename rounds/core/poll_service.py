"""Poll cycle logic for the diagnostic system.

This module implements the main polling loop that continuously
retrieves errors from telemetry, fingerprints them, and coordinates
investigations.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from .fingerprint import Fingerprinter
from .investigator import Investigator
from .models import Diagnosis, ErrorEvent, PollResult, Signature, SignatureStatus
from .ports import PollPort, SignatureStorePort, TelemetryPort
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
    ):
        self.telemetry = telemetry
        self.store = store
        self.fingerprinter = fingerprinter
        self.triage = triage
        self.investigator = investigator
        self.lookback_minutes = lookback_minutes
        self.services = services
        self.batch_size = batch_size

    async def execute_poll_cycle(self) -> PollResult:
        """Check for new errors, fingerprint, dedup, and queue investigations.

        Returns a summary of what was found.
        """
        now = datetime.now(timezone.utc)
        since = now - timedelta(minutes=self.lookback_minutes)

        try:
            errors = await self.telemetry.get_recent_errors(since, self.services)
        except Exception as e:
            logger.error(f"Failed to fetch recent errors from telemetry: {e}", exc_info=True)
            return PollResult(
                errors_found=0,
                new_signatures=0,
                updated_signatures=0,
                investigations_queued=0,
                timestamp=now,
            )

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
                    )
                    await self.store.save(signature)
                    new_signatures += 1
                else:
                    # Update existing signature
                    signature.last_seen = error.timestamp
                    signature.occurrence_count += 1
                    await self.store.update(signature)
                    updated_signatures += 1

                # Check if we should investigate
                if self.triage.should_investigate(signature):
                    investigations_queued += 1

            except Exception as e:
                logger.error(
                    f"Failed to process error event {error.trace_id}: {e}",
                    exc_info=True,
                )
                # Continue processing remaining errors

        return PollResult(
            errors_found=len(errors),
            new_signatures=new_signatures,
            updated_signatures=updated_signatures,
            investigations_queued=investigations_queued,
            timestamp=now,
        )

    async def execute_investigation_cycle(self) -> list[Diagnosis]:
        """Investigate pending signatures. Returns diagnoses produced."""
        try:
            pending = await self.store.get_pending_investigation()
        except Exception as e:
            logger.error(f"Failed to fetch pending signatures: {e}", exc_info=True)
            return []

        # Sort by priority
        pending.sort(
            key=lambda s: self.triage.calculate_priority(s), reverse=True
        )

        diagnoses = []
        for signature in pending:
            try:
                if self.triage.should_investigate(signature):
                    diagnosis = await self.investigator.investigate(signature)
                    diagnoses.append(diagnosis)
            except Exception as e:
                logger.error(
                    f"Failed to investigate signature {signature.fingerprint}: {e}",
                    exc_info=True,
                )
                # Continue with next signature

        return diagnoses
