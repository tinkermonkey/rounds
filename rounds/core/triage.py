"""Triage and prioritization rules for failure signatures.

This module implements the business rules that determine which
signatures should be investigated, in what order, and under what
conditions.
"""

from datetime import datetime, timedelta

from .models import Confidence, Diagnosis, Signature, SignatureStatus


class TriageEngine:
    """Decides what to do with each fingerprinted error.

    Pure decision logic — no side effects.
    """

    def __init__(
        self,
        min_occurrence_for_investigation: int = 3,
        investigation_cooldown_hours: int = 24,
        high_confidence_threshold: Confidence = Confidence.HIGH,
    ):
        self.min_occurrence_for_investigation = min_occurrence_for_investigation
        self.investigation_cooldown_hours = investigation_cooldown_hours
        self.high_confidence_threshold = high_confidence_threshold

    def should_investigate(self, signature: Signature) -> bool:
        """Is this signature worth sending to the diagnosis engine?

        Considers:
        - Status (don't re-investigate diagnosed/resolved/muted)
        - Occurrence count (need enough data)
        - Cooldown period (don't spam LLM for same signature)
        """
        # Don't investigate resolved or muted signatures
        if signature.status in {SignatureStatus.RESOLVED, SignatureStatus.MUTED}:
            return False

        # Don't investigate if already diagnosed recently
        if signature.diagnosis is not None:
            cooldown = timedelta(hours=self.investigation_cooldown_hours)
            if datetime.now() - signature.diagnosis.diagnosed_at < cooldown:
                return False

        # Need minimum occurrence count for meaningful investigation
        if signature.occurrence_count < self.min_occurrence_for_investigation:
            return False

        return True

    def should_notify(self, signature: Signature, diagnosis: Diagnosis) -> bool:
        """Should this diagnosis be reported?

        Considers:
        - Confidence level
        - Whether it's a new diagnosis (vs re-investigation)
        - Signature severity
        """
        # Always notify high-confidence diagnoses
        if diagnosis.confidence == self.high_confidence_threshold:
            return True

        # Notify medium confidence if it's a new signature
        if (
            signature.status == SignatureStatus.NEW
            and diagnosis.confidence == Confidence.MEDIUM
        ):
            return True

        # Notify if tagged as critical
        if "critical" in signature.tags:
            return True

        return False

    def calculate_priority(self, signature: Signature) -> int:
        """Order signatures for investigation when multiple are pending.

        Higher score = higher priority.

        Considers:
        - Frequency (occurrence count)
        - Recency (last seen timestamp)
        - Whether it's new
        - Tags (critical > flaky > normal)
        """
        priority = 0

        # Frequency component (0-100 points)
        priority += min(signature.occurrence_count, 100)

        # Recency component (0-50 points)
        hours_since_last = (
            datetime.now() - signature.last_seen
        ).total_seconds() / 3600
        if hours_since_last < 1:
            priority += 50
        elif hours_since_last < 24:
            priority += 25

        # New signature bonus (50 points)
        if signature.status == SignatureStatus.NEW:
            priority += 50

        # Tag bonuses
        if "critical" in signature.tags:
            priority += 100
        if "flaky-test" in signature.tags:
            priority -= 20  # Lower priority for known flaky tests

        return priority
