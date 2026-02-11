"""Poll cycle logic for the diagnostic system.

This module implements the main polling loop that continuously
retrieves errors from telemetry, fingerprints them, and coordinates
investigations.
"""

import uuid
from datetime import datetime, timedelta

from .fingerprint import Fingerprinter
from .investigator import Investigator
from .models import Diagnosis, ErrorEvent, PollResult, Signature, SignatureStatus
from .ports import PollPort, SignatureStorePort, TelemetryPort
from .triage import TriageEngine


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
    ):
        self.telemetry = telemetry
        self.store = store
        self.fingerprinter = fingerprinter
        self.triage = triage
        self.investigator = investigator
        self.lookback_minutes = lookback_minutes
        self.services = services

    async def execute_poll_cycle(self) -> PollResult:
        """Check for new errors, fingerprint, dedup, and queue investigations.

        Returns a summary of what was found.
        """
        now = datetime.now()
        since = now - timedelta(minutes=self.lookback_minutes)

        # Fetch recent errors
        errors = await self.telemetry.get_recent_errors(since, self.services)

        new_signatures = 0
        updated_signatures = 0
        investigations_queued = 0

        for error in errors:
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
                    stack_hash=self.fingerprinter._hash_stack(normalized_stack),
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

        return PollResult(
            errors_found=len(errors),
            new_signatures=new_signatures,
            updated_signatures=updated_signatures,
            investigations_queued=investigations_queued,
            timestamp=now,
        )

    async def execute_investigation_cycle(self) -> list[Diagnosis]:
        """Investigate pending signatures. Returns diagnoses produced."""
        pending = await self.store.get_pending_investigation()

        # Sort by priority
        pending.sort(
            key=lambda s: self.triage.calculate_priority(s), reverse=True
        )

        diagnoses = []
        for signature in pending:
            if self.triage.should_investigate(signature):
                diagnosis = await self.investigator.investigate(signature)
                diagnoses.append(diagnosis)

        return diagnoses
