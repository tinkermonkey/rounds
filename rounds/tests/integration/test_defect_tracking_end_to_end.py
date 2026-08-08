"""End-to-end integration test for the full defect-tracking lifecycle.

Gap 3 (architecture amendment) / FR-E2E: exercises detection, diagnosis,
GitHub issue creation, dedup-with-recurrence-comment, phone-home alert
delivery with cooldown, auto-resolution, and re-emergence as one continuous
flow through the real PollService/Investigator/ManagementService, a real
SQLite-backed SignatureStore, and fake telemetry/diagnosis/GitHub/phone-home
adapters — never touching the real GitHub API.
"""

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from rounds.adapters.notification.composite import CompositeNotificationAdapter
from rounds.adapters.notification.github_issues import GitHubIssueNotificationAdapter
from rounds.adapters.notification.phone_home import PhoneHomeNotificationAdapter
from rounds.adapters.store.sqlite import SQLiteSignatureStore
from rounds.core.fingerprint import Fingerprinter
from rounds.core.investigator import Investigator
from rounds.core.management_service import ManagementService
from rounds.core.models import Diagnosis, ErrorEvent, Severity, SignatureStatus, StackFrame
from rounds.core.poll_service import PollService
from rounds.core.triage import TriageEngine
from rounds.tests.fakes import FakeDiagnosisPort, FakeTelemetryPort
from rounds.tests.fakes.github_client import FakeGitHubClient


def _make_error(timestamp: datetime) -> ErrorEvent:
    """A recurring ConnectionTimeoutError, identical in every field the
    fingerprinter cares about (error_type, service, message, stack) so
    repeated calls dedup onto the same signature."""
    return ErrorEvent(
        trace_id=f"trace-{timestamp.isoformat()}",
        span_id="span-001",
        service="payment-service",
        error_type="ConnectionTimeoutError",
        error_message="Connection to database timed out",
        stack_frames=(
            StackFrame(
                module="payment_service.db",
                function="connect",
                filename="db.py",
                lineno=42,
            ),
        ),
        timestamp=timestamp,
        attributes={},
        severity=Severity.ERROR,
    )


@pytest.mark.asyncio
async def test_defect_lifecycle_end_to_end(tmp_path: Path) -> None:
    """Detect -> diagnose -> create issue -> dedup recurrence -> alert with
    cooldown -> auto-resolve -> re-emerge with a fresh issue."""
    fingerprinter = Fingerprinter()
    triage = TriageEngine(min_occurrence_for_investigation=1)
    store = SQLiteSignatureStore(str(tmp_path / "signatures.db"))
    telemetry = FakeTelemetryPort()

    diagnosis_port = FakeDiagnosisPort()
    diagnosis_port.set_default_diagnosis(
        Diagnosis(
            root_cause="Connection pool exhaustion under load",
            evidence=("Pool size exceeded during peak traffic",),
            suggested_fix="Increase the connection pool size",
            confidence="high",
            diagnosed_at=datetime.now(UTC),
            model="fake-model",
            cost_usd=0.02,
            suggested_resolution_hours=1,
        )
    )

    github_client = FakeGitHubClient()
    github_adapter = GitHubIssueNotificationAdapter(
        repo_owner="acme", repo_name="widgets", github_token="test-token"
    )
    github_adapter._client = github_client  # type: ignore[assignment]

    phone_home_requests: list[httpx.Request] = []

    def phone_home_handler(request: httpx.Request) -> httpx.Response:
        phone_home_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    phone_home_adapter = PhoneHomeNotificationAdapter(
        endpoint_url="https://phone-home.example.com/alerts",
        auth_token="test-token",
    )
    phone_home_adapter._client = httpx.AsyncClient(
        transport=httpx.MockTransport(phone_home_handler)
    )

    notification = CompositeNotificationAdapter([github_adapter, phone_home_adapter])

    investigator = Investigator(
        telemetry, store, diagnosis_port, notification, triage, codebase_path="/unused"
    )
    poll = PollService(
        telemetry,
        store,
        fingerprinter,
        triage,
        investigator,
        lookback_minutes=60,
        notification=notification,
        resolution_threshold_hours_default=24,
    )
    management = ManagementService(
        store, telemetry, diagnosis_port, notification, triage, codebase_path="/unused"
    )

    try:
        # --- Detection: a poll cycle turns a fresh error into a NEW signature ---
        first_seen = datetime.now(UTC)
        error = _make_error(first_seen)
        fingerprint = fingerprinter.fingerprint(error)
        telemetry.add_error(error)
        telemetry.add_signature_events(fingerprint, [error])

        poll_result = await poll.execute_poll_cycle()
        assert poll_result.new_signatures == 1
        assert poll_result.investigations_queued == 1

        signature = await store.get_by_fingerprint(fingerprint)
        assert signature is not None
        assert signature.status == SignatureStatus.NEW

        # --- Diagnosis: the investigation cycle diagnoses it and adopts the
        # suggested resolution window ---
        investigation_result = await poll.execute_investigation_cycle()
        assert investigation_result.investigations_failed == 0
        assert len(investigation_result.diagnoses_produced) == 1

        signature = await store.get_by_fingerprint(fingerprint)
        assert signature is not None
        assert signature.status == SignatureStatus.DIAGNOSED
        assert signature.resolution_threshold_hours == 1

        # --- Issue creation: exactly one issue, with the diagnosis and
        # correct label scheme ---
        create_calls = [
            r for r in github_client.requests if r.method == "POST" and r.url.endswith("/issues")
        ]
        assert len(create_calls) == 1
        assert len(github_client.issues) == 1
        first_issue = github_client.issues[1]
        assert "rounds" in first_issue.labels
        assert "auto-detected" in first_issue.labels
        assert "severity-error" in first_issue.labels
        assert "service-payment-service" in first_issue.labels
        assert f"fingerprint:{fingerprint[:16]}" in first_issue.labels
        assert "Connection pool exhaustion under load" in first_issue.body

        # --- Alert delivery: exactly one phone-home callback, cooldown recorded ---
        assert len(phone_home_requests) == 1
        signature = await store.get_by_fingerprint(fingerprint)
        assert signature is not None
        assert signature.last_alerted_at is not None

        # --- Dedup on recurrence + cooldown: re-investigating the same
        # fingerprint posts a recurrence comment instead of a new issue, and
        # phone-home stays silent within its cooldown window ---
        await management.reinvestigate(signature.id)

        create_calls = [
            r for r in github_client.requests if r.method == "POST" and r.url.endswith("/issues")
        ]
        assert len(create_calls) == 1  # still just the one issue
        assert len(github_client.issues) == 1
        assert len(first_issue.comments) == 1
        assert "Recurrence Detected" in first_issue.comments[0]
        assert len(phone_home_requests) == 1  # cooldown suppressed the second alert

        # --- Resolution: quiet past the threshold auto-closes the issue ---
        signature = await store.get_by_fingerprint(fingerprint)
        assert signature is not None
        signature.first_seen = datetime.now(UTC) - timedelta(hours=3)
        signature.last_seen = datetime.now(UTC) - timedelta(hours=2)
        await store.update(signature)

        resolution_result = await poll.execute_resolution_cycle()
        assert resolution_result.signatures_resolved == 1

        signature = await store.get_by_fingerprint(fingerprint)
        assert signature is not None
        assert signature.status == SignatureStatus.RESOLVED
        assert first_issue.state == "closed"
        assert len(first_issue.comments) == 2
        assert "Auto-Resolved" in first_issue.comments[-1]

        # --- Re-emergence: a fresh occurrence after resolution creates a new
        # issue; the old one stays closed. Backdate the diagnosis so the
        # investigation cooldown doesn't block re-diagnosis of the recurrence. ---
        assert signature.diagnosis is not None
        signature.diagnosis = dataclasses.replace(
            signature.diagnosis, diagnosed_at=datetime.now(UTC) - timedelta(hours=25)
        )
        await store.update(signature)

        recurrence_error = _make_error(datetime.now(UTC))
        telemetry.errors.clear()  # the original occurrence has aged out of the lookback window
        telemetry.add_error(recurrence_error)
        telemetry.add_signature_events(fingerprint, [error, recurrence_error])

        poll_result_2 = await poll.execute_poll_cycle()
        assert poll_result_2.updated_signatures == 1
        assert poll_result_2.investigations_queued == 1

        signature = await store.get_by_fingerprint(fingerprint)
        assert signature is not None
        assert signature.status == SignatureStatus.NEW

        investigation_result_2 = await poll.execute_investigation_cycle()
        assert investigation_result_2.investigations_failed == 0
        assert len(investigation_result_2.diagnoses_produced) == 1

        assert len(github_client.issues) == 2
        second_issue = github_client.issues[2]
        assert second_issue.state == "open"
        assert first_issue.state == "closed"
    finally:
        await store.close_pool()
        await notification.close()
