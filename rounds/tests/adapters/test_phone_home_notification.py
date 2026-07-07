"""Tests for PhoneHomeNotificationAdapter's severity gate, cooldown, and mute suppression."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from rounds.adapters.notification import phone_home
from rounds.adapters.notification.phone_home import PhoneHomeNotificationAdapter
from rounds.core.models import Diagnosis, Severity, Signature, SignatureStatus
from rounds.tests.fakes.store import FakeSignatureStorePort


def _make_adapter(
    transport: httpx.MockTransport,
    store: FakeSignatureStorePort,
    severity_gate: frozenset[Severity] = frozenset({Severity.ERROR, Severity.FATAL}),
    cooldown_hours: int = 24,
) -> PhoneHomeNotificationAdapter:
    """Create a PhoneHomeNotificationAdapter with a mock transport."""
    adapter = PhoneHomeNotificationAdapter(
        endpoint_url="https://phone-home.example.com/alerts",
        auth_token="test-token",
        store=store,
        severity_gate=severity_gate,
        cooldown_hours=cooldown_hours,
    )
    adapter._client = httpx.AsyncClient(
        headers={"Authorization": "Bearer test-token"},
        transport=transport,
    )
    return adapter


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


class TestSeverityGate:
    """FR18-19: only ERROR/FATAL (or whatever gate is configured) trigger an alert."""

    @pytest.mark.asyncio
    async def test_error_severity_posts_one_alert(self) -> None:
        """A new ERROR signature posts exactly one alert."""
        signature = _make_signature(max_severity=Severity.ERROR)
        diagnosis = _make_diagnosis()
        posts = []

        def handler(request: httpx.Request) -> httpx.Response:
            posts.append(request)
            return httpx.Response(200, json={"status": "ok"})

        store = FakeSignatureStorePort()
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        await adapter.report(signature, diagnosis)
        await adapter.close()

        assert len(posts) == 1

    @pytest.mark.asyncio
    async def test_fatal_severity_posts_alert(self) -> None:
        """A new FATAL signature posts an alert."""
        signature = _make_signature(max_severity=Severity.FATAL)
        diagnosis = _make_diagnosis()
        posts = []

        def handler(request: httpx.Request) -> httpx.Response:
            posts.append(request)
            return httpx.Response(200, json={"status": "ok"})

        store = FakeSignatureStorePort()
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        await adapter.report(signature, diagnosis)
        await adapter.close()

        assert len(posts) == 1

    @pytest.mark.asyncio
    async def test_warn_severity_sends_no_alert(self) -> None:
        """A new WARN signature never triggers a phone-home alert."""
        signature = _make_signature(max_severity=Severity.WARN)
        diagnosis = _make_diagnosis()

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("No request should be made for a WARN signature")

        store = FakeSignatureStorePort()
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        await adapter.report(signature, diagnosis)
        await adapter.close()

        assert store.updated_signatures == []

    @pytest.mark.asyncio
    async def test_info_severity_sends_no_alert(self) -> None:
        """Severities below the gate (e.g. INFO) never trigger an alert."""
        signature = _make_signature(max_severity=Severity.INFO)
        diagnosis = _make_diagnosis()

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("No request should be made for an INFO signature")

        store = FakeSignatureStorePort()
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        await adapter.report(signature, diagnosis)
        await adapter.close()


class TestCooldown:
    """FR21: 24h (configurable) cooldown per signature via Signature.last_alerted_at."""

    @pytest.mark.asyncio
    async def test_no_alert_within_cooldown_window(self) -> None:
        """A signature alerted 1 hour ago does not alert again within a 24h cooldown."""
        signature = _make_signature(
            last_alerted_at=datetime.now(UTC) - timedelta(hours=1)
        )
        diagnosis = _make_diagnosis()

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("No request should be made within the cooldown window")

        store = FakeSignatureStorePort()
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        await adapter.report(signature, diagnosis)
        await adapter.close()

    @pytest.mark.asyncio
    async def test_alert_sent_after_cooldown_expires(self) -> None:
        """A signature last alerted more than the cooldown ago alerts again."""
        signature = _make_signature(
            last_alerted_at=datetime.now(UTC) - timedelta(hours=25)
        )
        diagnosis = _make_diagnosis()
        posts = []

        def handler(request: httpx.Request) -> httpx.Response:
            posts.append(request)
            return httpx.Response(200, json={"status": "ok"})

        store = FakeSignatureStorePort()
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        await adapter.report(signature, diagnosis)
        await adapter.close()

        assert len(posts) == 1

    @pytest.mark.asyncio
    async def test_successful_alert_persists_last_alerted_at(self) -> None:
        """After a successful POST, last_alerted_at is updated and persisted via the store."""
        signature = _make_signature(last_alerted_at=None)
        diagnosis = _make_diagnosis()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok"})

        store = FakeSignatureStorePort()
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        await adapter.report(signature, diagnosis)
        await adapter.close()

        assert signature.last_alerted_at is not None
        assert store.updated_signatures == [signature]

    @pytest.mark.asyncio
    async def test_no_prior_alert_is_not_suppressed_by_cooldown(self) -> None:
        """A signature with no last_alerted_at is not blocked by the cooldown check."""
        signature = _make_signature(last_alerted_at=None)
        diagnosis = _make_diagnosis()
        posts = []

        def handler(request: httpx.Request) -> httpx.Response:
            posts.append(request)
            return httpx.Response(200, json={"status": "ok"})

        store = FakeSignatureStorePort()
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        await adapter.report(signature, diagnosis)
        await adapter.close()

        assert len(posts) == 1


class TestMuteSuppression:
    """FR22: muted signatures never alert, even if severity/cooldown would qualify."""

    @pytest.mark.asyncio
    async def test_muted_signature_sends_no_alert(self) -> None:
        """A muted ERROR signature with no cooldown in effect still sends no alert."""
        signature = _make_signature(
            status=SignatureStatus.MUTED, max_severity=Severity.FATAL, last_alerted_at=None
        )
        diagnosis = _make_diagnosis()

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("No request should be made for a muted signature")

        store = FakeSignatureStorePort()
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        await adapter.report(signature, diagnosis)
        await adapter.close()

        assert store.updated_signatures == []


class TestSuppressionDoesNotRaise:
    """FR23: failing either gate suppresses the alert without raising an error."""

    @pytest.mark.asyncio
    async def test_gate_failure_does_not_raise(self) -> None:
        """report() returns normally when the signature is suppressed by any gate."""
        signature = _make_signature(max_severity=Severity.WARN)
        diagnosis = _make_diagnosis()

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("No request should be made")

        store = FakeSignatureStorePort()
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        # Should not raise.
        await adapter.report(signature, diagnosis)
        await adapter.close()


class TestSelfContainedMessage:
    """FR20: alerts must be comprehensible without reference to prior messages."""

    @pytest.mark.asyncio
    async def test_message_includes_full_context(self) -> None:
        """The alert payload includes error, service, root cause, and fix in one message."""
        signature = _make_signature()
        diagnosis = _make_diagnosis()
        posted_bodies = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            posted_bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "ok"})

        store = FakeSignatureStorePort()
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        await adapter.report(signature, diagnosis)
        await adapter.close()

        message = posted_bodies[0]["message"]
        assert signature.error_type in message
        assert signature.service in message
        assert diagnosis.root_cause in message
        assert diagnosis.suggested_fix in message
        assert signature.max_severity.value in message


class TestCooldownPersistenceRetry:
    """The alert is already sent when last_alerted_at is persisted, so a transient
    store failure must be retried rather than silently losing the cooldown."""

    @pytest.mark.asyncio
    async def test_transient_store_failure_recovers_on_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A store update that fails once still ends up persisted after retry."""
        monkeypatch.setattr(phone_home, "_COOLDOWN_PERSIST_RETRY_DELAY_SECONDS", 0)
        signature = _make_signature(last_alerted_at=None)
        diagnosis = _make_diagnosis()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok"})

        store = FakeSignatureStorePort()
        store.update_fail_count = 1
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        await adapter.report(signature, diagnosis)
        await adapter.close()

        assert signature.last_alerted_at is not None
        assert store.updated_signatures == [signature]

    @pytest.mark.asyncio
    async def test_persistent_store_failure_raises_after_alert_sent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If every retry fails, the error propagates so the gap is observable,
        even though the alert itself was already sent exactly once."""
        monkeypatch.setattr(phone_home, "_COOLDOWN_PERSIST_RETRY_DELAY_SECONDS", 0)
        signature = _make_signature(last_alerted_at=None)
        diagnosis = _make_diagnosis()
        posts = []

        def handler(request: httpx.Request) -> httpx.Response:
            posts.append(request)
            return httpx.Response(200, json={"status": "ok"})

        store = FakeSignatureStorePort()
        store.update_fail_count = 100
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        with pytest.raises(RuntimeError):
            await adapter.report(signature, diagnosis)
        await adapter.close()

        assert len(posts) == 1
        assert signature.last_alerted_at is not None
        assert store.updated_signatures == []


class TestHttpFailurePropagates:
    """A network/HTTP failure should propagate so the caller can log it."""

    @pytest.mark.asyncio
    async def test_http_error_raises_and_does_not_update_cooldown(self) -> None:
        """A 500 response raises and last_alerted_at is left untouched."""
        signature = _make_signature(last_alerted_at=None)
        diagnosis = _make_diagnosis()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        store = FakeSignatureStorePort()
        adapter = _make_adapter(httpx.MockTransport(handler), store)
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.report(signature, diagnosis)
        await adapter.close()

        assert signature.last_alerted_at is None
        assert store.updated_signatures == []
