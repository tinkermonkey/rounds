"""Tests for CompositeNotificationAdapter's concurrent multi-channel dispatch."""

from datetime import UTC, datetime
from typing import Any

import pytest

from rounds.adapters.notification.composite import CompositeNotificationAdapter
from rounds.core.models import Diagnosis, Severity, Signature, SignatureStatus
from rounds.tests.fakes.notification import FakeNotificationPort


def _make_signature(**overrides: Any) -> Signature:
    """Create a sample signature for testing."""
    defaults: dict[str, Any] = dict(
        id="sig-001",
        fingerprint="a" * 64,
        error_type="DatabaseError",
        service="api-service",
        message_template="Failed to connect to {database}",
        stack_hash="stack-abc",
        first_seen=datetime(2026, 7, 1, tzinfo=UTC),
        last_seen=datetime(2026, 7, 6, 12, 0, tzinfo=UTC),
        occurrence_count=7,
        status=SignatureStatus.DIAGNOSED,
        max_severity=Severity.ERROR,
    )
    defaults.update(overrides)
    return Signature(**defaults)


def _make_diagnosis(**overrides: Any) -> Diagnosis:
    """Create a sample diagnosis for testing."""
    defaults: dict[str, Any] = dict(
        root_cause="Connection pool exhaustion",
        evidence=("Pool size exceeded",),
        suggested_fix="Increase pool size",
        confidence="high",
        diagnosed_at=datetime(2026, 7, 6, tzinfo=UTC),
        model="claude-opus",
        cost_usd=0.01,
    )
    defaults.update(overrides)
    return Diagnosis(**defaults)


class TestConcurrentDispatch:
    """FR24: every configured channel receives the same diagnosis, not either/or."""

    @pytest.mark.asyncio
    async def test_report_reaches_every_channel(self) -> None:
        """report() dispatches to all channels for the same diagnosis."""
        signature = _make_signature()
        diagnosis = _make_diagnosis()
        github = FakeNotificationPort()
        phone_home = FakeNotificationPort()
        composite = CompositeNotificationAdapter([github, phone_home])

        await composite.report(signature, diagnosis)

        assert github.get_reported_diagnosis_count() == 1
        assert phone_home.get_reported_diagnosis_count() == 1
        assert github.get_last_diagnosis_report() == (signature, diagnosis)
        assert phone_home.get_last_diagnosis_report() == (signature, diagnosis)

    @pytest.mark.asyncio
    async def test_report_summary_reaches_every_channel(self) -> None:
        """report_summary() dispatches to all channels."""
        stats = {"total_signatures": 3}
        a, b = FakeNotificationPort(), FakeNotificationPort()
        composite = CompositeNotificationAdapter([a, b])

        await composite.report_summary(stats)

        assert a.get_last_summary_report() == stats
        assert b.get_last_summary_report() == stats

    @pytest.mark.asyncio
    async def test_report_alert_reaches_every_channel(self) -> None:
        """report_alert() dispatches to all channels."""
        alert = {"alert": "investigation_pipeline_suspended"}
        a, b = FakeNotificationPort(), FakeNotificationPort()
        composite = CompositeNotificationAdapter([a, b])

        await composite.report_alert(alert)

        assert a.reported_alerts == [alert]
        assert b.reported_alerts == [alert]

    @pytest.mark.asyncio
    async def test_close_resolved_issue_reaches_every_channel(self) -> None:
        """close_resolved_issue() dispatches to all channels."""
        signature = _make_signature(status=SignatureStatus.RESOLVED)
        a, b = FakeNotificationPort(), FakeNotificationPort()
        composite = CompositeNotificationAdapter([a, b])

        await composite.close_resolved_issue(signature)

        assert a.closed_resolved_issues == [signature]
        assert b.closed_resolved_issues == [signature]


class TestPartialFailureIsolation:
    """One channel failing must not prevent other channels from being dispatched to."""

    @pytest.mark.asyncio
    async def test_one_channel_failing_does_not_block_the_other(self) -> None:
        """A failing channel still lets the healthy channel receive the report."""
        signature = _make_signature()
        diagnosis = _make_diagnosis()
        healthy = FakeNotificationPort()
        failing = FakeNotificationPort()
        failing.set_should_fail(True, "phone-home unreachable")
        composite = CompositeNotificationAdapter([failing, healthy])

        with pytest.raises(RuntimeError, match="phone-home unreachable"):
            await composite.report(signature, diagnosis)

        assert healthy.get_reported_diagnosis_count() == 1
        assert failing.report_call_count == 1

    @pytest.mark.asyncio
    async def test_close_does_not_raise_on_channel_failure(self) -> None:
        """close() is best-effort cleanup: a failing channel does not stop others closing."""

        class RaisingClose(FakeNotificationPort):
            async def close(self) -> None:
                raise RuntimeError("close failed")

        healthy = FakeNotificationPort()
        raising = RaisingClose()
        composite = CompositeNotificationAdapter([raising, healthy])

        # Should not raise.
        await composite.close()


class TestConstruction:
    """Basic construction invariants."""

    def test_requires_at_least_one_channel(self) -> None:
        """An empty channel list is rejected at construction time."""
        with pytest.raises(ValueError):
            CompositeNotificationAdapter([])
