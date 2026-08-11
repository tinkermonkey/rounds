"""Tests for DigestNotificationAdapter (WARN digest batching)."""

from datetime import UTC, datetime, timedelta

import pytest

from rounds.adapters.notification.digest import DigestNotificationAdapter
from rounds.core.models import Diagnosis, Severity, Signature, SignatureStatus
from rounds.core.triage import TriageEngine
from rounds.tests.fakes.notification import FakeNotificationPort


def _make_signature(
    *,
    service: str = "payment-service",
    max_severity: Severity = Severity.WARN,
    tags: frozenset[str] = frozenset(),
) -> Signature:
    return Signature(
        id=f"sig-{service}",
        fingerprint=f"fp-{service}",
        error_type="ConnectionTimeoutError",
        service=service,
        message_template="Failed to connect: timeout",
        stack_hash="hash-001",
        first_seen=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        last_seen=datetime(2024, 1, 1, 12, 5, 0, tzinfo=UTC),
        occurrence_count=5,
        status=SignatureStatus.DIAGNOSED,
        tags=tags,
        max_severity=max_severity,
    )


def _make_diagnosis(confidence: str = "low") -> Diagnosis:
    return Diagnosis(
        root_cause="root",
        evidence=(),
        suggested_fix="fix",
        confidence=confidence,  # type: ignore[arg-type]
        diagnosed_at=datetime(2024, 1, 1, 12, 30, 0, tzinfo=UTC),
        model="model",
        cost_usd=0.0,
    )


@pytest.fixture
def inner() -> FakeNotificationPort:
    return FakeNotificationPort()


@pytest.fixture
def triage() -> TriageEngine:
    return TriageEngine()


@pytest.mark.asyncio
async def test_batch_qualifying_diagnosis_is_buffered_not_reported(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """A batch-qualifying diagnosis produces no individual immediate notification."""
    adapter = DigestNotificationAdapter(inner=inner, triage=triage)
    signature = _make_signature(max_severity=Severity.WARN)
    diagnosis = _make_diagnosis(confidence="low")

    result = await adapter.report(signature, diagnosis)

    assert result is None
    assert inner.report_call_count == 0
    assert adapter.pending_count == 1


@pytest.mark.asyncio
async def test_high_confidence_error_severity_bypasses_batching(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """High-confidence diagnoses above the WARN ceiling are forwarded immediately.

    should_batch() is an OR rule: WARN-or-below/non-critical signatures batch
    regardless of confidence, so confidence alone only bypasses batching once
    severity is above the WARN ceiling too.
    """
    adapter = DigestNotificationAdapter(inner=inner, triage=triage)
    signature = _make_signature(max_severity=Severity.ERROR)
    diagnosis = _make_diagnosis(confidence="high")

    await adapter.report(signature, diagnosis)

    assert inner.report_call_count == 1
    assert adapter.pending_count == 0


@pytest.mark.asyncio
async def test_high_confidence_warn_severity_still_batches(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """WARN-or-below, non-critical signatures batch even at high confidence (OR rule)."""
    adapter = DigestNotificationAdapter(inner=inner, triage=triage)
    signature = _make_signature(max_severity=Severity.WARN)
    diagnosis = _make_diagnosis(confidence="high")

    await adapter.report(signature, diagnosis)

    assert inner.report_call_count == 0
    assert adapter.pending_count == 1


@pytest.mark.asyncio
async def test_critical_tagged_diagnosis_bypasses_batching(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """Critical-tagged signatures bypass batching, provided confidence isn't low."""
    adapter = DigestNotificationAdapter(inner=inner, triage=triage)
    signature = _make_signature(max_severity=Severity.WARN, tags=frozenset(["critical"]))
    diagnosis = _make_diagnosis(confidence="medium")

    await adapter.report(signature, diagnosis)

    assert inner.report_call_count == 1
    assert adapter.pending_count == 0


@pytest.mark.asyncio
async def test_low_confidence_bypasses_critical_tag_and_still_batches(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """Low confidence always batches (OR rule), even for a critical-tagged signature."""
    adapter = DigestNotificationAdapter(inner=inner, triage=triage)
    signature = _make_signature(max_severity=Severity.WARN, tags=frozenset(["critical"]))
    diagnosis = _make_diagnosis(confidence="low")

    await adapter.report(signature, diagnosis)

    assert inner.report_call_count == 0
    assert adapter.pending_count == 1


@pytest.mark.asyncio
async def test_immediate_flag_bypasses_batching_regardless_of_classification(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """report(..., immediate=True) always delivers now - e.g. user-initiated reinvestigate()."""
    adapter = DigestNotificationAdapter(inner=inner, triage=triage)
    signature = _make_signature(max_severity=Severity.WARN)
    diagnosis = _make_diagnosis(confidence="low")

    await adapter.report(signature, diagnosis, immediate=True)

    assert inner.report_call_count == 1
    assert inner.report_immediate_flags == [True]
    assert adapter.pending_count == 0


@pytest.mark.asyncio
async def test_multiple_batched_diagnoses_accumulate(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """Batched diagnoses accumulate across multiple report() calls within the window."""
    adapter = DigestNotificationAdapter(inner=inner, triage=triage)

    await adapter.report(_make_signature(service="svc-a"), _make_diagnosis())
    await adapter.report(_make_signature(service="svc-b"), _make_diagnosis())
    await adapter.report(_make_signature(service="svc-a"), _make_diagnosis())

    assert adapter.pending_count == 3
    assert inner.report_call_count == 0


@pytest.mark.asyncio
async def test_flush_before_window_elapsed_does_nothing(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """flush_if_due() is a no-op while the window hasn't elapsed yet."""
    window_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    adapter = DigestNotificationAdapter(inner=inner, triage=triage, window_start=window_start)
    await adapter.report(_make_signature(), _make_diagnosis())

    flushed = await adapter.flush_if_due(
        window_start + timedelta(hours=1), timedelta(days=1)
    )

    assert flushed is False
    assert adapter.pending_count == 1
    assert inner.report_summary_call_count == 0


@pytest.mark.asyncio
async def test_flush_after_window_elapsed_emits_digest_with_count_services_and_signatures(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """Emits one digest containing count, affected services, and signature identification."""
    window_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    adapter = DigestNotificationAdapter(inner=inner, triage=triage, window_start=window_start)
    sig_a = _make_signature(service="svc-a")
    sig_b = _make_signature(service="svc-b")
    await adapter.report(sig_a, _make_diagnosis())
    await adapter.report(sig_b, _make_diagnosis())

    now = window_start + timedelta(days=1)
    flushed = await adapter.flush_if_due(now, timedelta(days=1))

    assert flushed is True
    assert inner.report_summary_call_count == 1
    stats = inner.get_last_summary_report()
    assert stats is not None
    assert stats["count"] == 2
    assert stats["services"] == ["svc-a", "svc-b"]
    signature_ids = {entry["signature_id"] for entry in stats["signatures"]}
    assert signature_ids == {sig_a.id, sig_b.id}
    # total_signatures/total_errors_seen/by_status/by_service are what the
    # report_summary() formatters (stdout, markdown, github_issues) actually
    # render - without these keys a flushed digest is an invisible, all-zero
    # summary.
    assert stats["total_signatures"] == 2
    assert stats["total_errors_seen"] == 2
    assert stats["by_status"] == {"diagnosed": 2}
    assert stats["by_confidence"] == {"low": 2}
    assert stats["by_service"] == {"svc-a": 1, "svc-b": 1}
    # Buffer and window reset after flush.
    assert adapter.pending_count == 0


@pytest.mark.asyncio
async def test_flush_digest_stats_deduplicate_signatures_and_track_status(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """total_signatures counts unique signatures; total_errors_seen counts every diagnosis.

    Also verifies by_status carries real SignatureStatus values (not confidence)
    and by_confidence carries the confidence breakdown as its own dimension.
    """
    window_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    adapter = DigestNotificationAdapter(inner=inner, triage=triage, window_start=window_start)
    # sig_a is re-diagnosed twice within the same window - same signature id
    # (derived from service), distinct diagnoses.
    sig_a = _make_signature(service="svc-a")
    sig_b = _make_signature(service="svc-b")
    await adapter.report(sig_a, _make_diagnosis(confidence="low"))
    await adapter.report(sig_a, _make_diagnosis(confidence="medium"))
    await adapter.report(sig_b, _make_diagnosis(confidence="low"))

    now = window_start + timedelta(days=1)
    await adapter.flush_if_due(now, timedelta(days=1))

    stats = inner.get_last_summary_report()
    assert stats is not None
    assert stats["count"] == 3
    # sig_a counted once despite being batched twice; sig_b counted once.
    assert stats["total_signatures"] == 2
    # Every batched diagnosis is counted, including the repeat for sig_a.
    assert stats["total_errors_seen"] == 3
    assert stats["by_status"] == {"diagnosed": 3}
    assert stats["by_confidence"] == {"low": 2, "medium": 1}
    assert stats["by_service"] == {"svc-a": 2, "svc-b": 1}


@pytest.mark.asyncio
async def test_flush_with_empty_buffer_advances_window_without_notifying(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """An elapsed window with nothing batched still closes/reopens, but sends no digest."""
    window_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    adapter = DigestNotificationAdapter(inner=inner, triage=triage, window_start=window_start)

    now = window_start + timedelta(days=1)
    flushed = await adapter.flush_if_due(now, timedelta(days=1))

    assert flushed is True
    assert inner.report_summary_call_count == 0


@pytest.mark.asyncio
async def test_windows_do_not_double_count_or_drop_across_boundary(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """Two consecutive windows each report only what was batched during them."""
    window_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    adapter = DigestNotificationAdapter(inner=inner, triage=triage, window_start=window_start)

    await adapter.report(_make_signature(service="svc-a"), _make_diagnosis())
    first_flush_at = window_start + timedelta(days=1)
    await adapter.flush_if_due(first_flush_at, timedelta(days=1))

    await adapter.report(_make_signature(service="svc-b"), _make_diagnosis())
    second_flush_at = first_flush_at + timedelta(days=1)
    await adapter.flush_if_due(second_flush_at, timedelta(days=1))

    assert inner.report_summary_call_count == 2
    first_stats, second_stats = inner.reported_summaries
    assert first_stats["services"] == ["svc-a"]
    assert second_stats["services"] == ["svc-b"]


@pytest.mark.asyncio
async def test_disabled_digest_passes_every_report_through_immediately(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """With enabled=False, current per-diagnosis notification behavior is unchanged."""
    adapter = DigestNotificationAdapter(inner=inner, triage=triage, enabled=False)
    signature = _make_signature(max_severity=Severity.WARN)
    diagnosis = _make_diagnosis(confidence="low")

    await adapter.report(signature, diagnosis)

    assert inner.report_call_count == 1
    assert adapter.pending_count == 0


@pytest.mark.asyncio
async def test_other_notification_methods_pass_through_unchanged(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """report_summary/report_alert/close_resolved_issue/close all delegate to inner untouched."""
    adapter = DigestNotificationAdapter(inner=inner, triage=triage)

    await adapter.report_summary({"total_signatures": 3})
    await adapter.report_alert({"alert": "poll_cycle_pipeline_suspended"})
    await adapter.close_resolved_issue(_make_signature())
    await adapter.close()

    assert inner.report_summary_call_count == 1
    assert inner.report_alert_call_count == 1
    assert inner.close_resolved_issue_call_count == 1


@pytest.mark.asyncio
async def test_flush_failure_restores_buffer_and_window_for_retry(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """If report_summary() raises, the flushed entries and window aren't lost."""
    window_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    adapter = DigestNotificationAdapter(inner=inner, triage=triage, window_start=window_start)
    sig_a = _make_signature(service="svc-a")
    await adapter.report(sig_a, _make_diagnosis())

    inner.set_should_fail(True, "digest channel unavailable")
    now = window_start + timedelta(days=1)

    with pytest.raises(RuntimeError, match="digest channel unavailable"):
        await adapter.flush_if_due(now, timedelta(days=1))

    # Entry survives the failed flush instead of being dropped.
    assert adapter.pending_count == 1

    # Retrying immediately succeeds because the window was restored, not advanced.
    inner.set_should_fail(False)
    flushed = await adapter.flush_if_due(now, timedelta(days=1))

    assert flushed is True
    assert adapter.pending_count == 0
    assert inner.report_summary_call_count == 2  # first attempt failed, retry succeeded
    stats = inner.get_last_summary_report()
    assert stats is not None
    assert stats["signatures"][0]["signature_id"] == sig_a.id


@pytest.mark.asyncio
async def test_close_flushes_buffered_entries_before_delegating(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """close() flushes any buffered digest entries instead of discarding them."""
    adapter = DigestNotificationAdapter(inner=inner, triage=triage)
    sig_a = _make_signature(service="svc-a")
    await adapter.report(sig_a, _make_diagnosis())

    await adapter.close()

    assert inner.report_summary_call_count == 1
    stats = inner.get_last_summary_report()
    assert stats is not None
    assert stats["signatures"][0]["signature_id"] == sig_a.id
    assert adapter.pending_count == 0
    assert inner.close_call_count == 1


@pytest.mark.asyncio
async def test_close_still_closes_inner_when_flush_fails(
    inner: FakeNotificationPort, triage: TriageEngine
) -> None:
    """A broken digest channel on shutdown must not prevent inner.close() from running."""
    adapter = DigestNotificationAdapter(inner=inner, triage=triage)
    await adapter.report(_make_signature(), _make_diagnosis())

    inner.set_should_fail(True, "digest channel unavailable")

    await adapter.close()

    assert inner.close_call_count == 1
    # Failed flush restores the buffer rather than silently dropping it.
    assert adapter.pending_count == 1
