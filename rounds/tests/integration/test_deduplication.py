"""Integration tests for fingerprint deduplication across poll cycles.

Exercises the full path: poll_service → fingerprint → SQLite store.
Uses FakeTelemetryPort for controlled error injection and a real SQLiteSignatureStore
backed by a temp file to validate on-disk persistence and deduplication semantics.
"""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rounds.adapters.store.sqlite import SQLiteSignatureStore
from rounds.core.fingerprint import Fingerprinter
from rounds.core.models import (
    ErrorEvent,
    Severity,
    SignatureStatus,
    StackFrame,
)
from rounds.core.poll_service import PollService
from rounds.core.triage import TriageEngine
from rounds.tests.fakes import (
    FakeDiagnosisPort,
    FakeNotificationPort,
    FakeTelemetryPort,
)
from rounds.core.investigator import Investigator


def _make_error(
    service: str = "api-service",
    error_type: str = "RuntimeError",
    message: str = "Connection refused",
    timestamp: datetime | None = None,
    trace_id: str = "trace-001",
) -> ErrorEvent:
    return ErrorEvent(
        trace_id=trace_id,
        span_id="span-001",
        service=service,
        error_type=error_type,
        error_message=message,
        stack_frames=(
            StackFrame(module="app.handler", function="handle", filename="handler.py", lineno=42),
        ),
        timestamp=timestamp or datetime.now(UTC),
        attributes={},
        severity=Severity.ERROR,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "signatures.db"


@pytest.fixture
def fingerprinter() -> Fingerprinter:
    return Fingerprinter()


@pytest.fixture
def triage() -> TriageEngine:
    # Set min_occurrence low so investigations don't interfere with basic tests.
    return TriageEngine(min_occurrence_for_investigation=100)


def _make_poll_service(
    store: SQLiteSignatureStore,
    telemetry: FakeTelemetryPort,
    fingerprinter: Fingerprinter,
    triage: TriageEngine,
) -> PollService:
    diagnosis = FakeDiagnosisPort()
    notification = FakeNotificationPort()
    investigator = Investigator(telemetry, store, diagnosis, notification, triage, "/app")
    return PollService(telemetry, store, fingerprinter, triage, investigator, lookback_minutes=15)


@pytest.mark.asyncio
async def test_new_error_creates_exactly_one_signature(
    db_path: Path,
    fingerprinter: Fingerprinter,
    triage: TriageEngine,
) -> None:
    """A new fingerprint detected for the first time must create exactly one Signature."""
    store = SQLiteSignatureStore(str(db_path))
    telemetry = FakeTelemetryPort()
    telemetry.add_error(_make_error())
    poll = _make_poll_service(store, telemetry, fingerprinter, triage)

    result = await poll.execute_poll_cycle()

    assert result.new_signatures == 1
    assert result.updated_signatures == 0
    signatures = await store.get_all()
    assert len(signatures) == 1
    assert signatures[0].service == "api-service"
    assert signatures[0].status == SignatureStatus.NEW
    await store.close_pool()


@pytest.mark.asyncio
async def test_same_fingerprint_across_cycles_no_duplicate(
    db_path: Path,
    fingerprinter: Fingerprinter,
    triage: TriageEngine,
) -> None:
    """The same error fingerprint across multiple poll cycles must yield exactly one record."""
    store = SQLiteSignatureStore(str(db_path))
    error = _make_error(timestamp=datetime.now(UTC))
    telemetry = FakeTelemetryPort()
    telemetry.add_error(error)
    poll = _make_poll_service(store, telemetry, fingerprinter, triage)

    await poll.execute_poll_cycle()
    await poll.execute_poll_cycle()
    await poll.execute_poll_cycle()

    signatures = await store.get_all()
    assert len(signatures) == 1, f"Expected 1 signature, got {len(signatures)}"
    await store.close_pool()


@pytest.mark.asyncio
async def test_occurrence_count_increments_each_cycle(
    db_path: Path,
    fingerprinter: Fingerprinter,
    triage: TriageEngine,
) -> None:
    """occurrence_count must grow by 1 for each cycle that detects the same error."""
    store = SQLiteSignatureStore(str(db_path))
    error = _make_error(timestamp=datetime.now(UTC))
    telemetry = FakeTelemetryPort()
    telemetry.add_error(error)
    poll = _make_poll_service(store, telemetry, fingerprinter, triage)

    for _ in range(3):
        await poll.execute_poll_cycle()

    sig = (await store.get_all())[0]
    assert sig.occurrence_count == 3
    await store.close_pool()


@pytest.mark.asyncio
async def test_first_seen_preserved_across_updates(
    db_path: Path,
    fingerprinter: Fingerprinter,
    triage: TriageEngine,
) -> None:
    """first_seen must not change when an existing signature is updated."""
    t0 = datetime.now(UTC)
    store = SQLiteSignatureStore(str(db_path))
    error = _make_error(timestamp=t0)
    telemetry = FakeTelemetryPort()
    telemetry.add_error(error)
    poll = _make_poll_service(store, telemetry, fingerprinter, triage)

    await poll.execute_poll_cycle()
    first_seen_after_first_cycle = (await store.get_all())[0].first_seen

    await poll.execute_poll_cycle()
    sig = (await store.get_all())[0]

    assert sig.first_seen == first_seen_after_first_cycle
    await store.close_pool()


@pytest.mark.asyncio
async def test_last_seen_updates_to_latest_timestamp(
    db_path: Path,
    fingerprinter: Fingerprinter,
    triage: TriageEngine,
) -> None:
    """last_seen must reflect the most recent occurrence timestamp."""
    t0 = datetime.now(UTC)
    t1 = t0 + timedelta(seconds=30)

    store = SQLiteSignatureStore(str(db_path))
    telemetry = FakeTelemetryPort()
    telemetry.add_error(_make_error(timestamp=t0, trace_id="trace-1"))
    poll = _make_poll_service(store, telemetry, fingerprinter, triage)

    await poll.execute_poll_cycle()

    # Second cycle with a newer occurrence timestamp
    telemetry2 = FakeTelemetryPort()
    telemetry2.add_error(_make_error(timestamp=t1, trace_id="trace-1"))
    poll2 = _make_poll_service(store, telemetry2, fingerprinter, triage)
    await poll2.execute_poll_cycle()

    sig = (await store.get_all())[0]
    assert sig.last_seen >= t1
    await store.close_pool()


@pytest.mark.asyncio
async def test_persistence_across_store_restart(
    db_path: Path,
    fingerprinter: Fingerprinter,
    triage: TriageEngine,
) -> None:
    """After creating a new store instance (simulating daemon restart), the existing
    Signature must be found and updated — not duplicated."""
    t0 = datetime.now(UTC)
    error = _make_error(timestamp=t0)

    # First store instance — first detection
    store1 = SQLiteSignatureStore(str(db_path))
    telemetry = FakeTelemetryPort()
    telemetry.add_error(error)
    poll1 = _make_poll_service(store1, telemetry, fingerprinter, triage)
    await poll1.execute_poll_cycle()
    await store1.close_pool()

    # Simulate restart: new store instance, same on-disk file
    store2 = SQLiteSignatureStore(str(db_path))
    telemetry2 = FakeTelemetryPort()
    telemetry2.add_error(error)
    poll2 = _make_poll_service(store2, telemetry2, fingerprinter, triage)
    result = await poll2.execute_poll_cycle()

    assert result.new_signatures == 0, "Should have updated, not created a new record"
    assert result.updated_signatures == 1

    signatures = await store2.get_all()
    assert len(signatures) == 1, "Must not duplicate after restart"
    assert signatures[0].occurrence_count == 2
    await store2.close_pool()


@pytest.mark.asyncio
async def test_multiple_errors_same_fingerprint_in_one_cycle(
    db_path: Path,
    fingerprinter: Fingerprinter,
    triage: TriageEngine,
) -> None:
    """Multiple error events sharing the same fingerprint within one poll cycle
    must collapse into a single Signature record."""
    t0 = datetime.now(UTC)
    # Three occurrences of the same error pattern, different trace_ids
    e1 = _make_error(timestamp=t0, trace_id="trace-a")
    e2 = _make_error(timestamp=t0 + timedelta(seconds=5), trace_id="trace-b")
    e3 = _make_error(timestamp=t0 + timedelta(seconds=10), trace_id="trace-c")

    store = SQLiteSignatureStore(str(db_path))
    telemetry = FakeTelemetryPort()
    telemetry.add_errors([e1, e2, e3])
    poll = _make_poll_service(store, telemetry, fingerprinter, triage)

    result = await poll.execute_poll_cycle()

    assert result.new_signatures == 1
    assert result.updated_signatures == 2  # e2 and e3 each update the existing record
    signatures = await store.get_all()
    assert len(signatures) == 1
    assert signatures[0].occurrence_count == 3
    await store.close_pool()


@pytest.mark.asyncio
async def test_out_of_order_events_do_not_corrupt_time_bounds(
    db_path: Path,
    fingerprinter: Fingerprinter,
    triage: TriageEngine,
) -> None:
    """Events with the same fingerprint arriving newest-first must not corrupt
    first_seen or last_seen."""
    t0 = datetime.now(UTC)
    t1 = t0 + timedelta(seconds=10)
    t2 = t0 + timedelta(seconds=20)

    # Arrive in reverse chronological order (newest first, as SigNoz may return)
    e_newest = _make_error(timestamp=t2, trace_id="trace-c")
    e_middle = _make_error(timestamp=t1, trace_id="trace-b")
    e_oldest = _make_error(timestamp=t0, trace_id="trace-a")

    store = SQLiteSignatureStore(str(db_path))
    telemetry = FakeTelemetryPort()
    telemetry.add_errors([e_newest, e_middle, e_oldest])
    poll = _make_poll_service(store, telemetry, fingerprinter, triage)

    result = await poll.execute_poll_cycle()

    assert result.errors_failed_to_process == 0, "No events should fail due to ordering"
    sig = (await store.get_all())[0]
    assert sig.occurrence_count == 3
    assert sig.first_seen == t0
    # last_seen must be the latest timestamp (t2)
    assert sig.last_seen == t2
    await store.close_pool()


@pytest.mark.asyncio
async def test_different_services_different_signatures(
    db_path: Path,
    fingerprinter: Fingerprinter,
    triage: TriageEngine,
) -> None:
    """Errors from different services must not be deduplicated together."""
    store = SQLiteSignatureStore(str(db_path))
    telemetry = FakeTelemetryPort()
    telemetry.add_errors([
        _make_error(service="service-a", trace_id="trace-1"),
        _make_error(service="service-b", trace_id="trace-2"),
    ])
    poll = _make_poll_service(store, telemetry, fingerprinter, triage)

    result = await poll.execute_poll_cycle()

    assert result.new_signatures == 2
    signatures = await store.get_all()
    assert len(signatures) == 2
    services = {s.service for s in signatures}
    assert services == {"service-a", "service-b"}
    await store.close_pool()
