"""Tests for Signature state machine guard clauses.

Verifies that state transitions are properly validated and that
invalid transitions raise appropriate errors.
"""

from datetime import UTC, datetime

import pytest

from rounds.core.models import (
    Diagnosis,
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
    assert signature.status == SignatureStatus.INVESTIGATING


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
    assert signature.status == SignatureStatus.DIAGNOSED
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
    assert signature.status == SignatureStatus.RESOLVED


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
    assert signature.status == SignatureStatus.MUTED


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
    assert signature.status == SignatureStatus.INVESTIGATING

    signature.mark_diagnosed(diagnosis)
    assert signature.status == SignatureStatus.DIAGNOSED
    assert signature.diagnosis is diagnosis

    signature.mark_resolved()
    assert signature.status == SignatureStatus.RESOLVED


def test_workflow_new_investigating_muted(signature: Signature) -> None:
    """Test muting during investigation."""
    assert signature.status == SignatureStatus.NEW

    signature.mark_investigating()
    assert signature.status == SignatureStatus.INVESTIGATING

    signature.mark_muted()
    assert signature.status == SignatureStatus.MUTED


def test_workflow_new_investigating_revert_investigating(signature: Signature) -> None:
    """Test reverting investigation back to NEW."""
    assert signature.status == SignatureStatus.NEW

    signature.mark_investigating()
    assert signature.status == SignatureStatus.INVESTIGATING

    signature.revert_to_new()
    assert signature.status == SignatureStatus.NEW

    # Can investigate again after revert
    signature.mark_investigating()
    assert signature.status == SignatureStatus.INVESTIGATING


def test_record_occurrence_invariants(signature: Signature) -> None:
    """Test that record_occurrence maintains timestamp invariants."""
    original_count = signature.occurrence_count
    new_timestamp = signature.last_seen

    signature.record_occurrence(new_timestamp)
    assert signature.occurrence_count == original_count + 1
    assert signature.last_seen == new_timestamp


def test_record_occurrence_before_first_seen_fails(signature: Signature) -> None:
    """record_occurrence should fail if timestamp is before first_seen."""
    from datetime import timedelta
    early_timestamp = signature.first_seen - timedelta(seconds=1)
    with pytest.raises(ValueError, match="cannot be before first_seen"):
        signature.record_occurrence(early_timestamp)
