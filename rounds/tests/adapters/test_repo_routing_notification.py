"""Tests for RepoOwnershipNotificationAdapter's owned/unowned/unmapped dispatch."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from rounds.adapters.notification.repo_routing import RepoOwnershipNotificationAdapter
from rounds.core.models import Diagnosis, Severity, Signature, SignatureStatus
from rounds.core.ports import NotificationPort
from rounds.tests.fakes.notification import FakeNotificationPort


def _make_signature(**overrides: Any) -> Signature:
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


class _RecordingFactory:
    """Records (owner, repo) calls and hands back a fresh FakeNotificationPort per repo."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.created: dict[tuple[str, str], FakeNotificationPort] = {}

    def __call__(self, owner: str, repo: str) -> NotificationPort:
        self.calls.append((owner, repo))
        adapter = FakeNotificationPort()
        self.created[(owner, repo)] = adapter
        return adapter


class TestOwnedRepoPath:
    @pytest.mark.asyncio
    async def test_report_routes_to_github_channel_for_owned_repo(self) -> None:
        factory = _RecordingFactory()
        fallback = FakeNotificationPort()
        adapter = RepoOwnershipNotificationAdapter(
            service_repo_map={"api-service": "acme/api"},
            github_account="acme",
            fallback=fallback,
            github_adapter_factory=factory,
        )
        signature = _make_signature(service="api-service")
        diagnosis = _make_diagnosis()

        await adapter.report(signature, diagnosis)

        assert factory.calls == [("acme", "api")]
        github_channel = factory.created[("acme", "api")]
        assert github_channel.get_reported_diagnosis_count() == 1
        assert fallback.report_call_count == 0

    @pytest.mark.asyncio
    async def test_ownership_check_case_insensitive_on_owner(self) -> None:
        factory = _RecordingFactory()
        fallback = FakeNotificationPort()
        adapter = RepoOwnershipNotificationAdapter(
            service_repo_map={"api-service": "ACME/api"},
            github_account="acme",
            fallback=fallback,
            github_adapter_factory=factory,
        )

        await adapter.report(_make_signature(service="api-service"), _make_diagnosis())

        assert factory.calls == [("ACME", "api")]
        assert fallback.report_call_count == 0

    @pytest.mark.asyncio
    async def test_repeated_reports_for_same_repo_reuse_cached_channel(self) -> None:
        factory = _RecordingFactory()
        adapter = RepoOwnershipNotificationAdapter(
            service_repo_map={"api-service": "acme/api"},
            github_account="acme",
            fallback=FakeNotificationPort(),
            github_adapter_factory=factory,
        )

        await adapter.report(_make_signature(service="api-service"), _make_diagnosis())
        await adapter.report(_make_signature(service="api-service"), _make_diagnosis())

        assert factory.calls == [("acme", "api")]
        assert factory.created[("acme", "api")].get_reported_diagnosis_count() == 2

    @pytest.mark.asyncio
    async def test_close_resolved_issue_routes_to_same_owned_repo(self) -> None:
        factory = _RecordingFactory()
        adapter = RepoOwnershipNotificationAdapter(
            service_repo_map={"api-service": "acme/api"},
            github_account="acme",
            fallback=FakeNotificationPort(),
            github_adapter_factory=factory,
        )
        signature = _make_signature(service="api-service")

        await adapter.close_resolved_issue(signature)

        github_channel = factory.created[("acme", "api")]
        assert github_channel.close_resolved_issue_call_count == 1


class TestNotOwnedOrUnknownRepoPath:
    @pytest.mark.asyncio
    async def test_report_falls_back_to_markdown_when_owner_does_not_match(self) -> None:
        factory = _RecordingFactory()
        fallback = FakeNotificationPort()
        adapter = RepoOwnershipNotificationAdapter(
            service_repo_map={"other-service": "someoneelse/other"},
            github_account="acme",
            fallback=fallback,
            github_adapter_factory=factory,
        )
        signature = _make_signature(service="other-service")
        diagnosis = _make_diagnosis()

        await adapter.report(signature, diagnosis)

        assert factory.calls == []
        assert fallback.get_reported_diagnosis_count() == 1

    @pytest.mark.asyncio
    async def test_report_falls_back_to_markdown_when_service_has_no_map_entry(self) -> None:
        factory = _RecordingFactory()
        fallback = FakeNotificationPort()
        adapter = RepoOwnershipNotificationAdapter(
            service_repo_map={},
            github_account="acme",
            fallback=fallback,
            github_adapter_factory=factory,
        )
        signature = _make_signature(service="unmapped-service")
        diagnosis = _make_diagnosis()

        await adapter.report(signature, diagnosis)

        assert factory.calls == []
        assert fallback.get_reported_diagnosis_count() == 1

    @pytest.mark.asyncio
    async def test_report_falls_back_when_no_account_configured(self) -> None:
        """An empty configured account can never own anything -> always fall back, never error."""
        factory = _RecordingFactory()
        fallback = FakeNotificationPort()
        adapter = RepoOwnershipNotificationAdapter(
            service_repo_map={"api-service": "acme/api"},
            github_account="",
            fallback=fallback,
            github_adapter_factory=factory,
        )

        await adapter.report(_make_signature(service="api-service"), _make_diagnosis())

        assert factory.calls == []
        assert fallback.get_reported_diagnosis_count() == 1

    @pytest.mark.asyncio
    async def test_malformed_map_entry_falls_back_to_markdown(self) -> None:
        factory = _RecordingFactory()
        fallback = FakeNotificationPort()
        adapter = RepoOwnershipNotificationAdapter(
            service_repo_map={"api-service": "not-a-valid-repo-string"},
            github_account="acme",
            fallback=fallback,
            github_adapter_factory=factory,
        )

        await adapter.report(_make_signature(service="api-service"), _make_diagnosis())

        assert factory.calls == []
        assert fallback.get_reported_diagnosis_count() == 1

    @pytest.mark.asyncio
    async def test_close_resolved_issue_falls_back_to_markdown_when_unowned(self) -> None:
        factory = _RecordingFactory()
        fallback = FakeNotificationPort()
        adapter = RepoOwnershipNotificationAdapter(
            service_repo_map={},
            github_account="acme",
            fallback=fallback,
            github_adapter_factory=factory,
        )
        signature = _make_signature(service="unmapped-service")

        await adapter.close_resolved_issue(signature)

        assert factory.calls == []
        assert fallback.close_resolved_issue_call_count == 1


class TestGlobalReports:
    @pytest.mark.asyncio
    async def test_report_summary_always_uses_fallback(self) -> None:
        factory = _RecordingFactory()
        fallback = FakeNotificationPort()
        adapter = RepoOwnershipNotificationAdapter(
            service_repo_map={"api-service": "acme/api"},
            github_account="acme",
            fallback=fallback,
            github_adapter_factory=factory,
        )

        await adapter.report_summary({"total_signatures": 3})

        assert factory.calls == []
        assert fallback.report_summary_call_count == 1

    @pytest.mark.asyncio
    async def test_report_alert_always_uses_fallback(self) -> None:
        factory = _RecordingFactory()
        fallback = FakeNotificationPort()
        adapter = RepoOwnershipNotificationAdapter(
            service_repo_map={"api-service": "acme/api"},
            github_account="acme",
            fallback=fallback,
            github_adapter_factory=factory,
        )

        await adapter.report_alert({"alert": "investigation_pipeline_suspended"})

        assert factory.calls == []
        assert fallback.report_alert_call_count == 1


class TestClose:
    @pytest.mark.asyncio
    async def test_close_closes_fallback_and_all_created_github_channels(self) -> None:
        factory = _RecordingFactory()
        fallback = FakeNotificationPort()
        adapter = RepoOwnershipNotificationAdapter(
            service_repo_map={
                "api-service": "acme/api",
                "worker-service": "acme/worker",
            },
            github_account="acme",
            fallback=fallback,
            github_adapter_factory=factory,
        )
        await adapter.report(_make_signature(service="api-service"), _make_diagnosis())
        await adapter.report(_make_signature(service="worker-service"), _make_diagnosis())

        channels = [fallback, *factory.created.values()]
        close_mocks = [AsyncMock(wraps=channel.close) for channel in channels]
        for channel, mock in zip(channels, close_mocks):
            channel.close = mock  # type: ignore[method-assign]

        await adapter.close()

        assert len(close_mocks) == 3
        for mock in close_mocks:
            mock.assert_awaited_once()
