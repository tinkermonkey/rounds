"""Tests for Signature state machine guard clauses.

Verifies that state transitions are properly validated and that
invalid transitions raise appropriate errors.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from rounds.core.models import (
    Diagnosis,
    Severity,
    Signature,
    SignatureStatus,
)


@pytest.fixture
def signature() -> Signature:
    """Create a sample signature for testing."""
    return Signature(
        id="sig-001",
        fingerprint="abc123def456",
        error_type="ConnectionTimeoutError",
        service="payment-service",
        message_template="Failed to connect to database: timeout",
        stack_hash="hash-stack-001",
        first_seen=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        last_seen=datetime(2024, 1, 1, 12, 5, 0, tzinfo=UTC),
        occurrence_count=5,
        status=SignatureStatus.NEW,
    )


@pytest.fixture
def diagnosis() -> Diagnosis:
    """Create a sample diagnosis for testing."""
    return Diagnosis(
        root_cause="Database connection pool exhausted",
        evidence=("Stack trace shows pool limit reached",),
        suggested_fix="Increase connection pool size",
        confidence="high",
        diagnosed_at=datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC),
        model="claude-code",
        cost_usd=0.05,
    )


# ============================================================================
# mark_investigating tests
# ============================================================================


def test_mark_investigating_from_new(signature: Signature) -> None:
    """mark_investigating should succeed when status is NEW."""
    assert signature.status == SignatureStatus.NEW
    signature.mark_investigating()
    assert cast(SignatureStatus, signature.status) == SignatureStatus.INVESTIGATING


def test_mark_investigating_from_investigating(signature: Signature) -> None:
    """mark_investigating should succeed when already INVESTIGATING (idempotent)."""
    signature.status = SignatureStatus.INVESTIGATING
    signature.mark_investigating()
    assert signature.status == SignatureStatus.INVESTIGATING


def test_mark_investigating_from_diagnosed(signature: Signature) -> None:
    """mark_investigating should fail when status is DIAGNOSED."""
    signature.status = SignatureStatus.DIAGNOSED
    with pytest.raises(ValueError, match=r"Cannot investigate signature in .*DIAGNOSED"):
        signature.mark_investigating()


def test_mark_investigating_from_resolved(signature: Signature) -> None:
    """mark_investigating should fail when status is RESOLVED."""
    signature.status = SignatureStatus.RESOLVED
    with pytest.raises(ValueError, match=r"Cannot investigate signature in .*RESOLVED"):
        signature.mark_investigating()


def test_mark_investigating_from_muted(signature: Signature) -> None:
    """mark_investigating should fail when status is MUTED."""
    signature.status = SignatureStatus.MUTED
    with pytest.raises(ValueError, match=r"Cannot investigate signature in .*MUTED"):
        signature.mark_investigating()


# ============================================================================
# mark_diagnosed tests
# ============================================================================


def test_mark_diagnosed_from_investigating(signature: Signature, diagnosis: Diagnosis) -> None:
    """mark_diagnosed should succeed and set diagnosis when from INVESTIGATING."""
    signature.status = SignatureStatus.INVESTIGATING
    signature.mark_diagnosed(diagnosis)
    assert signature.status == SignatureStatus.DIAGNOSED
    assert signature.diagnosis is diagnosis


def test_mark_diagnosed_overwrites_previous_diagnosis(
    signature: Signature, diagnosis: Diagnosis
) -> None:
    """mark_diagnosed should overwrite previous diagnosis."""
    signature.status = SignatureStatus.DIAGNOSED
    signature.diagnosis = diagnosis

    new_diagnosis = Diagnosis(
        root_cause="Different root cause",
        evidence=("New evidence",),
        suggested_fix="New fix",
        confidence="medium",
        diagnosed_at=datetime(2024, 1, 1, 14, 0, 0, tzinfo=UTC),
        model="claude-code",
        cost_usd=0.03,
    )
    signature.mark_diagnosed(new_diagnosis)

    assert signature.status == SignatureStatus.DIAGNOSED
    assert signature.diagnosis is new_diagnosis
    assert signature.diagnosis.root_cause == "Different root cause"


def test_mark_diagnosed_from_new(signature: Signature, diagnosis: Diagnosis) -> None:
    """mark_diagnosed should succeed even from NEW status."""
    assert signature.status == SignatureStatus.NEW
    signature.mark_diagnosed(diagnosis)
    assert cast(SignatureStatus, signature.status) == SignatureStatus.DIAGNOSED
    assert signature.diagnosis is diagnosis


# ============================================================================
# mark_resolved tests
# ============================================================================


def test_mark_resolved_from_diagnosed(signature: Signature) -> None:
    """mark_resolved should succeed from DIAGNOSED."""
    signature.status = SignatureStatus.DIAGNOSED
    signature.mark_resolved()
    assert signature.status == SignatureStatus.RESOLVED


def test_mark_resolved_from_investigating(signature: Signature) -> None:
    """mark_resolved should succeed from INVESTIGATING."""
    signature.status = SignatureStatus.INVESTIGATING
    signature.mark_resolved()
    assert signature.status == SignatureStatus.RESOLVED


def test_mark_resolved_from_new(signature: Signature) -> None:
    """mark_resolved should succeed from NEW."""
    assert signature.status == SignatureStatus.NEW
    signature.mark_resolved()
    assert cast(SignatureStatus, signature.status) == SignatureStatus.RESOLVED


def test_mark_resolved_idempotent_fails(signature: Signature) -> None:
    """mark_resolved should fail when called twice (not idempotent)."""
    signature.status = SignatureStatus.RESOLVED
    with pytest.raises(ValueError, match="Signature is already resolved"):
        signature.mark_resolved()


def test_mark_resolved_from_muted(signature: Signature) -> None:
    """mark_resolved should succeed from MUTED."""
    signature.status = SignatureStatus.MUTED
    signature.mark_resolved()
    assert signature.status == SignatureStatus.RESOLVED


# ============================================================================
# mark_muted tests
# ============================================================================


def test_mark_muted_from_new(signature: Signature) -> None:
    """mark_muted should succeed from NEW."""
    assert signature.status == SignatureStatus.NEW
    signature.mark_muted()
    assert cast(SignatureStatus, signature.status) == SignatureStatus.MUTED


def test_mark_muted_from_diagnosed(signature: Signature) -> None:
    """mark_muted should succeed from DIAGNOSED."""
    signature.status = SignatureStatus.DIAGNOSED
    signature.mark_muted()
    assert signature.status == SignatureStatus.MUTED


def test_mark_muted_from_investigating(signature: Signature) -> None:
    """mark_muted should succeed from INVESTIGATING."""
    signature.status = SignatureStatus.INVESTIGATING
    signature.mark_muted()
    assert signature.status == SignatureStatus.MUTED


def test_mark_muted_idempotent_fails(signature: Signature) -> None:
    """mark_muted should fail when called twice (not idempotent)."""
    signature.status = SignatureStatus.MUTED
    with pytest.raises(ValueError, match="Signature is already muted"):
        signature.mark_muted()


def test_mark_muted_from_resolved(signature: Signature) -> None:
    """mark_muted should succeed from RESOLVED."""
    signature.status = SignatureStatus.RESOLVED
    signature.mark_muted()
    assert signature.status == SignatureStatus.MUTED


# ============================================================================
# revert_to_new tests
# ============================================================================


def test_revert_to_new_from_investigating(signature: Signature) -> None:
    """revert_to_new should succeed from INVESTIGATING."""
    signature.status = SignatureStatus.INVESTIGATING
    signature.revert_to_new()
    assert signature.status == SignatureStatus.NEW


def test_revert_to_new_from_new_fails(signature: Signature) -> None:
    """revert_to_new should fail when already NEW."""
    assert signature.status == SignatureStatus.NEW
    with pytest.raises(ValueError, match="Can only revert from INVESTIGATING status"):
        signature.revert_to_new()


def test_revert_to_new_from_diagnosed_fails(signature: Signature) -> None:
    """revert_to_new should fail from DIAGNOSED."""
    signature.status = SignatureStatus.DIAGNOSED
    with pytest.raises(ValueError, match="Can only revert from INVESTIGATING status"):
        signature.revert_to_new()


def test_revert_to_new_from_resolved_fails(signature: Signature) -> None:
    """revert_to_new should fail from RESOLVED."""
    signature.status = SignatureStatus.RESOLVED
    with pytest.raises(ValueError, match="Can only revert from INVESTIGATING status"):
        signature.revert_to_new()


def test_revert_to_new_from_muted_fails(signature: Signature) -> None:
    """revert_to_new should fail from MUTED."""
    signature.status = SignatureStatus.MUTED
    with pytest.raises(ValueError, match="Can only revert from INVESTIGATING status"):
        signature.revert_to_new()


# ============================================================================
# Comprehensive workflow tests
# ============================================================================


def test_workflow_new_investigating_diagnosed_resolved(signature: Signature, diagnosis: Diagnosis) -> None:
    """Test complete happy-path workflow: NEW -> INVESTIGATING -> DIAGNOSED -> RESOLVED."""
    assert signature.status == SignatureStatus.NEW

    signature.mark_investigating()
    assert cast(SignatureStatus, signature.status) == SignatureStatus.INVESTIGATING

    signature.mark_diagnosed(diagnosis)
    assert cast(SignatureStatus, signature.status) == SignatureStatus.DIAGNOSED
    assert signature.diagnosis is diagnosis

    signature.mark_resolved()
    assert cast(SignatureStatus, signature.status) == SignatureStatus.RESOLVED


def test_workflow_new_investigating_muted(signature: Signature) -> None:
    """Test muting during investigation."""
    assert signature.status == SignatureStatus.NEW

    signature.mark_investigating()
    assert cast(SignatureStatus, signature.status) == SignatureStatus.INVESTIGATING

    signature.mark_muted()
    assert cast(SignatureStatus, signature.status) == SignatureStatus.MUTED


def test_workflow_new_investigating_revert_investigating(signature: Signature) -> None:
    """Test reverting investigation back to NEW."""
    assert signature.status == SignatureStatus.NEW

    signature.mark_investigating()
    assert cast(SignatureStatus, signature.status) == SignatureStatus.INVESTIGATING

    signature.revert_to_new()
    assert cast(SignatureStatus, signature.status) == SignatureStatus.NEW

    # Can investigate again after revert
    signature.mark_investigating()
    assert cast(SignatureStatus, signature.status) == SignatureStatus.INVESTIGATING


def test_record_occurrence_invariants(signature: Signature) -> None:
    """Test that record_occurrence maintains timestamp invariants."""
    original_count = signature.occurrence_count
    new_timestamp = signature.last_seen

    signature.record_occurrence(new_timestamp)
    assert signature.occurrence_count == original_count + 1
    assert signature.last_seen == new_timestamp


def test_record_occurrence_earlier_timestamp_updates_first_seen(signature: Signature) -> None:
    """record_occurrence with an older timestamp should update first_seen, not raise."""
    early_timestamp = signature.first_seen - timedelta(seconds=1)
    original_last_seen = signature.last_seen
    original_count = signature.occurrence_count

    signature.record_occurrence(early_timestamp)

    assert signature.occurrence_count == original_count + 1
    assert signature.first_seen == early_timestamp
    assert signature.last_seen == original_last_seen  # last_seen should not move backward


def test_record_occurrence_does_not_move_last_seen_backwards(signature: Signature) -> None:
    """record_occurrence with an older-than-last_seen timestamp must not decrease last_seen."""
    original_last_seen = signature.last_seen
    # timestamp is after first_seen but before last_seen
    mid_timestamp = signature.first_seen + timedelta(seconds=30)
    assert mid_timestamp < original_last_seen

    signature.record_occurrence(mid_timestamp)

    assert signature.last_seen == original_last_seen


# ============================================================================
# resolution_threshold_hours, last_alerted_at, max_severity
# ============================================================================


def test_signature_defaults_for_new_fields(signature: Signature) -> None:
    """New signatures default resolution_threshold_hours/last_alerted_at to
    None and max_severity to ERROR, so no backfill is required for existing
    signatures deserialized without these fields."""
    assert signature.resolution_threshold_hours is None
    assert signature.last_alerted_at is None
    assert signature.max_severity == Severity.ERROR


def test_signature_accepts_explicit_resolution_and_alert_fields() -> None:
    """resolution_threshold_hours and last_alerted_at round-trip through construction."""
    alerted_at = datetime(2024, 1, 1, 14, 0, 0, tzinfo=UTC)
    sig = Signature(
        id="sig-002",
        fingerprint="fp-002",
        error_type="NullPointerError",
        service="billing-service",
        message_template="Null reference in handler",
        stack_hash="hash-stack-002",
        first_seen=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        last_seen=datetime(2024, 1, 1, 12, 5, 0, tzinfo=UTC),
        occurrence_count=1,
        status=SignatureStatus.NEW,
        resolution_threshold_hours=12,
        last_alerted_at=alerted_at,
        max_severity=Severity.FATAL,
    )
    assert sig.resolution_threshold_hours == 12
    assert sig.last_alerted_at == alerted_at
    assert sig.max_severity == Severity.FATAL


@pytest.mark.parametrize("invalid_hours", [0, -1, -24])
def test_signature_rejects_non_positive_resolution_threshold_hours(
    invalid_hours: int,
) -> None:
    """A zero or negative resolution_threshold_hours (e.g. from a corrupt row)
    must be rejected, since timedelta(hours=<non-positive>) would make every
    diagnosed signature appear immediately overdue for auto-close."""
    with pytest.raises(ValueError, match=r"resolution_threshold_hours must be positive"):
        Signature(
            id="sig-003",
            fingerprint="fp-003",
            error_type="NullPointerError",
            service="billing-service",
            message_template="Null reference in handler",
            stack_hash="hash-stack-003",
            first_seen=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            last_seen=datetime(2024, 1, 1, 12, 5, 0, tzinfo=UTC),
            occurrence_count=1,
            status=SignatureStatus.NEW,
            resolution_threshold_hours=invalid_hours,
        )


def test_record_occurrence_raises_max_severity_when_new_event_is_more_severe(
    signature: Signature,
) -> None:
    """record_occurrence should raise max_severity when a more severe event arrives."""
    signature.record_occurrence(signature.last_seen, Severity.FATAL)

    assert signature.max_severity == Severity.FATAL


def test_record_occurrence_reverts_resolved_signature_to_new(signature: Signature) -> None:
    """FR16: a recurrence against a RESOLVED signature (issue already auto-closed)
    must bring it back to NEW so it re-enters triage/investigation, rather than
    staying resolved under a closed issue."""
    signature.status = SignatureStatus.RESOLVED
    recurrence_timestamp = signature.last_seen + timedelta(days=2)

    signature.record_occurrence(recurrence_timestamp)

    assert signature.status == SignatureStatus.NEW
    assert signature.last_seen == recurrence_timestamp


def test_record_occurrence_does_not_alter_non_resolved_status(signature: Signature) -> None:
    """record_occurrence should not touch status for signatures that aren't RESOLVED."""
    signature.status = SignatureStatus.DIAGNOSED

    signature.record_occurrence(signature.last_seen + timedelta(hours=1))

    assert signature.status == SignatureStatus.DIAGNOSED


def test_record_occurrence_does_not_lower_max_severity(signature: Signature) -> None:
    """record_occurrence should not downgrade max_severity for a less severe event."""
    signature.record_occurrence(signature.last_seen, Severity.FATAL)
    signature.record_occurrence(signature.last_seen, Severity.WARN)

    assert signature.max_severity == Severity.FATAL


def test_record_occurrence_without_severity_leaves_max_severity_unchanged(
    signature: Signature,
) -> None:
    """record_occurrence called without a severity (backward compatible) is a no-op for max_severity."""
    original = signature.max_severity

    signature.record_occurrence(signature.last_seen)

    assert signature.max_severity == original


def test_severity_rank_orders_from_trace_to_fatal() -> None:
    """Severity.rank should increase from TRACE (least severe) to FATAL (most severe)."""
    ordered = [
        Severity.TRACE,
        Severity.DEBUG,
        Severity.INFO,
        Severity.WARN,
        Severity.ERROR,
        Severity.FATAL,
    ]
    ranks = [s.rank for s in ordered]
    assert ranks == sorted(ranks)


def test_diagnosis_suggested_resolution_hours_defaults_to_none(diagnosis: Diagnosis) -> None:
    """Diagnosis.suggested_resolution_hours defaults to None when not specified."""
    assert diagnosis.suggested_resolution_hours is None


def test_diagnosis_suggested_resolution_hours_round_trips() -> None:
    """Diagnosis.suggested_resolution_hours round-trips through construction."""
    d = Diagnosis(
        root_cause="Transient network timeout",
        evidence=("Connection reset observed once",),
        suggested_fix="Add retry with backoff",
        confidence="low",
        diagnosed_at=datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC),
        model="claude-code",
        cost_usd=0.02,
        suggested_resolution_hours=6,
    )
    assert d.suggested_resolution_hours == 6


@pytest.mark.parametrize("invalid_hours", [0, -1, -24])
def test_diagnosis_rejects_non_positive_suggested_resolution_hours(
    invalid_hours: int,
) -> None:
    """A zero or negative suggested_resolution_hours would produce an
    instantly-elapsed resolution threshold, so it must be rejected at
    construction time rather than flowing through to auto-resolution."""
    with pytest.raises(ValueError, match="suggested_resolution_hours must be positive"):
        Diagnosis(
            root_cause="Transient network timeout",
            evidence=("Connection reset observed once",),
            suggested_fix="Add retry with backoff",
            confidence="low",
            diagnosed_at=datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC),
            model="claude-code",
            cost_usd=0.02,
            suggested_resolution_hours=invalid_hours,
        )
