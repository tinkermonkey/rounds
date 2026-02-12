"""Triage and prioritization rules for failure signatures.

This module implements the business rules that determine which
signatures should be investigated, in what order, and under what
conditions.
"""

from datetime import datetime, timedelta, timezone

from .models import Confidence, Diagnosis, Signature, SignatureStatus


class TriageEngine:
    """Decides what to do with each fingerprinted error.

    Pure decision logic — no side effects.
    """

    def __init__(
        self,
        min_occurrence_for_investigation: int = 3,
        investigation_cooldown_hours: int = 24,
        high_confidence_threshold: Confidence = "high",
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
            now = datetime.now(timezone.utc)
            if now - signature.diagnosis.diagnosed_at < cooldown:
                return False

        # Need minimum occurrence count for meaningful investigation
        if signature.occurrence_count < self.min_occurrence_for_investigation:
            return False

        return True

    def should_notify(
        self,
        signature: Signature,
        diagnosis: Diagnosis,
        original_status: SignatureStatus | None = None,
    ) -> bool:
        """Should this diagnosis be reported?

        Considers:
        - Confidence level
        - Whether it's a new diagnosis (vs re-investigation)
        - Signature severity

        Args:
            signature: The signature being diagnosed
            diagnosis: The diagnosis result
            original_status: The original status before diagnosis. If provided,
                            used to determine if this is a new signature diagnosis.
        """
        # Always notify high-confidence diagnoses
        if diagnosis.confidence == self.high_confidence_threshold:
            return True

        # Notify medium confidence if it's a new signature
        # Use original_status if provided, otherwise check current status
        status_for_check = original_status if original_status is not None else signature.status
        if (
            status_for_check == SignatureStatus.NEW
            and diagnosis.confidence == "medium"
        ):
            return True

        # Notify if tagged as critical
        if "critical" in signature.tags:
            return True

        return False

    def calculate_priority(self, signature: Signature) -> int:
        """Order signatures for investigation when multiple are pending.

        Higher score = higher priority.

        Weighting rationale:
        - Frequency capped at 100 to prevent high-volume errors from dominating
        - Recency weighted at 50% of frequency since immediate errors are important
        - New signatures get bonus (50 points, equal to 50% of max frequency) to surface novel issues
        - Critical tag adds 100 (highest possible boost for confirmed issues)
        - Flaky test penalty -20 to de-prioritize known unstable tests

        Considers:
        - Frequency (occurrence count)
        - Recency (last seen timestamp)
        - Whether it's new
        - Tags (critical > flaky > normal)
        """
        priority = 0

        # Frequency component (0-100 points)
        priority += min(signature.occurrence_count, 100)

        # Recency component (0-50 points max)
        # Recent errors (< 1 hour) are more actionable than older ones
        now = datetime.now(timezone.utc)
        hours_since_last = (
            now - signature.last_seen
        ).total_seconds() / 3600
        if hours_since_last < 1:
            priority += 50
        elif hours_since_last < 24:
            priority += 25

        # New signature bonus (50 points)
        # Equal to 50% of max frequency to surface novel issues early
        if signature.status == SignatureStatus.NEW:
            priority += 50

        # Tag bonuses
        if "critical" in signature.tags:
            priority += 100
        if "flaky-test" in signature.tags:
            priority -= 20  # Lower priority for known flaky tests

        return priority
