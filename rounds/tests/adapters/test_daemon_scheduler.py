"""Tests for DaemonScheduler budget enforcement."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from rounds.adapters.scheduler.daemon import DaemonScheduler
from rounds.core.models import PollResult
from rounds.tests.fakes.notification import FakeNotificationPort
from rounds.tests.fakes.poll import FakePollPort


@pytest.fixture
def poll_port() -> FakePollPort:
    """Create a fake poll port."""
    return FakePollPort()


@pytest.mark.asyncio
async def test_budget_exceeded_blocks_diagnosis(
    poll_port: FakePollPort,
) -> None:
    """Test that exceeding budget is detected correctly."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        budget_limit=5.00,
    )

    # Record costs that exceed budget
    await scheduler.record_cost("diagnose", 3.00)
    await scheduler.record_cost("diagnose", 2.50)

    # Budget should be exceeded
    assert await scheduler._is_budget_exceeded() is True
    assert scheduler._daily_cost_usd == 5.50


@pytest.mark.asyncio
async def test_budget_resets_on_date_change(
    poll_port: FakePollPort,
) -> None:
    """Test that daily budget resets when date changes."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        budget_limit=10.00,
    )

    # Record cost on current date
    await scheduler.record_cost("diagnose", 8.00)
    assert scheduler._daily_cost_usd == 8.00

    original_date = scheduler._budget_date

    # Simulate date change by modifying the internal budget date to yesterday
    scheduler._budget_date = original_date - timedelta(days=1)

    # Record cost on today (new date) - should reset budget
    await scheduler.record_cost("diagnose", 2.00)
    assert scheduler._daily_cost_usd == 2.00
    # Budget date should have been updated to today
    assert scheduler._budget_date == original_date


@pytest.mark.asyncio
async def test_record_diagnosis_cost_accumulates(
    poll_port: FakePollPort,
) -> None:
    """Test that costs accumulate correctly."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        budget_limit=100.00,
    )

    await scheduler.record_cost("diagnose", 10.50)
    assert scheduler._daily_cost_usd == 10.50

    await scheduler.record_cost("diagnose", 15.25)
    assert scheduler._daily_cost_usd == 25.75

    await scheduler.record_cost("diagnose", 5.00)
    assert scheduler._daily_cost_usd == 30.75


@pytest.mark.asyncio
async def test_record_cost_tracks_per_step_breakdown(
    poll_port: FakePollPort,
) -> None:
    """Test that costs are attributed to their originating RoundStep."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        budget_limit=100.00,
    )

    await scheduler.record_cost("poll", 0.0)
    await scheduler.record_cost("fingerprint", 0.0)
    await scheduler.record_cost("diagnose", 1.25)
    await scheduler.record_cost("diagnose", 0.75)
    await scheduler.record_cost("confirm", 0.0)

    assert scheduler.cost_by_step == {
        "poll": 0.0,
        "fingerprint": 0.0,
        "diagnose": 2.0,
        "confirm": 0.0,
    }
    assert scheduler._daily_cost_usd == 2.0


@pytest.mark.asyncio
async def test_no_budget_limit_allows_unlimited_costs(
    poll_port: FakePollPort,
) -> None:
    """Test that None budget_limit allows unlimited costs."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        budget_limit=None,
    )

    await scheduler.record_cost("diagnose", 1000.00)
    assert await scheduler._is_budget_exceeded() is False


# --- Per-service budget cap tests ---


@pytest.mark.asyncio
async def test_service_budget_exceeded_blocks_investigation(
    poll_port: FakePollPort,
) -> None:
    """Test that a service's per-service cap is detected as exceeded once reached."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        service_budget_map={"noisy-service": 5.00},
    )

    await scheduler.record_cost("diagnose", 3.00, service="noisy-service")
    assert await scheduler.is_service_budget_exceeded("noisy-service") is False

    await scheduler.record_cost("diagnose", 2.00, service="noisy-service")
    assert await scheduler.is_service_budget_exceeded("noisy-service") is True
    assert scheduler.cost_by_service == {"noisy-service": 5.00}


@pytest.mark.asyncio
async def test_uncapped_service_never_reports_exceeded(
    poll_port: FakePollPort,
) -> None:
    """A service with no entry in service_budget_map is only governed by the global budget."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        service_budget_map={"noisy-service": 5.00},
    )

    await scheduler.record_cost("diagnose", 1000.00, service="quiet-service")
    assert await scheduler.is_service_budget_exceeded("quiet-service") is False


@pytest.mark.asyncio
async def test_service_budgets_are_independent(
    poll_port: FakePollPort,
) -> None:
    """Exhausting one service's cap must not affect another service's cap."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        service_budget_map={"service-a": 5.00, "service-b": 5.00},
    )

    await scheduler.record_cost("diagnose", 5.00, service="service-a")

    assert await scheduler.is_service_budget_exceeded("service-a") is True
    assert await scheduler.is_service_budget_exceeded("service-b") is False
    assert scheduler.cost_by_service == {"service-a": 5.00}


@pytest.mark.asyncio
async def test_service_budget_resets_on_date_change(
    poll_port: FakePollPort,
) -> None:
    """Per-service accumulated cost resets alongside the global daily budget reset."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        service_budget_map={"noisy-service": 5.00},
    )

    await scheduler.record_cost("diagnose", 5.00, service="noisy-service")
    assert await scheduler.is_service_budget_exceeded("noisy-service") is True

    original_date = scheduler._budget_date
    scheduler._budget_date = original_date - timedelta(days=1)

    # First check after the date change should reset and report not-exceeded
    assert await scheduler.is_service_budget_exceeded("noisy-service") is False
    assert scheduler.cost_by_service == {}
    assert scheduler._budget_date == original_date


@pytest.mark.asyncio
async def test_record_cost_without_service_does_not_populate_cost_by_service(
    poll_port: FakePollPort,
) -> None:
    """Costs recorded without a service (e.g. poll/fingerprint) aren't attributed to any service."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
    )

    await scheduler.record_cost("poll", 0.0)
    await scheduler.record_cost("fingerprint", 0.0)

    assert scheduler.cost_by_service == {}


# --- _run_loop Tests ---


@pytest.mark.asyncio
async def test_run_loop_executes_poll_cycles(
    poll_port: FakePollPort,
) -> None:
    """Test that _run_loop executes poll cycles until stopped."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,  # Use integer for type safety
        budget_limit=1000.0,
    )
    scheduler.running = True  # Start the scheduler

    # Run for a short time then stop
    async def run_then_stop() -> None:
        await asyncio.sleep(0.05)
        await scheduler.stop()

    # Run both concurrently so stop_task can interrupt _run_loop
    await asyncio.gather(
        scheduler._run_loop(),
        run_then_stop(),
    )

    # Should have executed at least one cycle
    assert poll_port.poll_cycle_count > 0


@pytest.mark.asyncio
async def test_run_loop_exits_on_stop_called(
    poll_port: FakePollPort,
) -> None:
    """Test that _run_loop exits when stop() is called."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=10,  # Use integer for type safety
        budget_limit=1000.0,
    )
    scheduler.running = True  # Start the scheduler

    async def stop_after_delay() -> None:
        await asyncio.sleep(0.01)
        await scheduler.stop()

    # Run both concurrently so stop_task can interrupt _run_loop
    await asyncio.gather(
        scheduler._run_loop(),
        stop_after_delay(),
    )

    # Should exit quickly despite long poll interval
    assert not scheduler.running


@pytest.mark.asyncio
async def test_run_loop_executes_resolution_cycle_each_iteration(
    poll_port: FakePollPort,
) -> None:
    """Test that _run_loop invokes execute_resolution_cycle() on the same
    cadence as the poll/investigation cycles."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        budget_limit=1000.0,
    )
    scheduler.running = True

    async def run_then_stop() -> None:
        await asyncio.sleep(0.05)
        await scheduler.stop()

    await asyncio.gather(
        scheduler._run_loop(),
        run_then_stop(),
    )

    assert poll_port.execute_resolution_cycle_call_count > 0
    assert (
        poll_port.execute_resolution_cycle_call_count == poll_port.poll_cycle_count
    )


@pytest.mark.asyncio
async def test_run_loop_executes_resolution_cycle_when_budget_exceeded(
    poll_port: FakePollPort,
) -> None:
    """Resolution detection incurs no LLM cost, so it must keep running on
    the normal cadence even once the daily budget is exhausted — only
    diagnosis (investigation) is gated by budget."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        budget_limit=5.00,
    )
    # Exhaust the budget before the loop starts.
    await scheduler.record_cost("diagnose", 10.00)
    assert await scheduler._is_budget_exceeded() is True

    scheduler.running = True

    async def run_then_stop() -> None:
        await asyncio.sleep(0.05)
        await scheduler.stop()

    await asyncio.gather(
        scheduler._run_loop(),
        run_then_stop(),
    )

    assert poll_port.poll_cycle_count > 0
    assert poll_port.execute_resolution_cycle_call_count > 0
    assert (
        poll_port.execute_resolution_cycle_call_count == poll_port.poll_cycle_count
    )


@pytest.mark.asyncio
async def test_run_loop_survives_resolution_cycle_failure(
    poll_port: FakePollPort,
) -> None:
    """A failing resolution cycle is logged but must not crash the daemon loop."""
    poll_port.should_fail_resolution = True
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        budget_limit=1000.0,
    )
    scheduler.running = True

    async def run_then_stop() -> None:
        await asyncio.sleep(0.05)
        await scheduler.stop()

    await asyncio.gather(
        scheduler._run_loop(),
        run_then_stop(),
    )

    # The poll loop should keep running (and keep polling) despite the failure.
    assert poll_port.poll_cycle_count > 0
    assert not scheduler.running


@pytest.mark.asyncio
async def test_run_loop_investigation_failure_tracking(
    poll_port: FakePollPort,
) -> None:
    """Test that _run_loop tracks investigation cycle failures."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,  # Use integer for type safety
        budget_limit=1000.0,
    )
    scheduler.running = True  # Start the scheduler

    # Configure poll port to return a result with investigations queued
    # so that investigation cycle will be triggered
    poll_port.set_default_poll_result(
        PollResult(
            errors_found=1,
            new_signatures=1,
            updated_signatures=0,
            investigations_queued=1,
            timestamp=datetime.now(UTC),
        )
    )

    # Make investigation cycle fail
    poll_port.should_fail_investigation = True

    # Run a few cycles
    async def stop_after_cycles() -> None:
        await asyncio.sleep(0.05)
        await scheduler.stop()

    # Run both concurrently so stop_task can interrupt _run_loop
    await asyncio.gather(
        scheduler._run_loop(),
        stop_after_cycles(),
    )

    # Failure counter should have incremented
    assert scheduler._investigation_failure_count > 0

@pytest.mark.asyncio
async def test_daemon_continues_after_resolution_threshold(
    poll_port: FakePollPort,
) -> None:
    """Daemon poll loop must survive 5 consecutive resolution failures and suspend them."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=0,
        budget_limit=1000.0,
    )
    scheduler.running = True
    poll_port.should_fail_resolution = True

    async def stop_after_suspension() -> None:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if (
                scheduler._resolution_failure_count >= 5
                and poll_port.poll_cycle_count >= 8
            ):
                await scheduler.stop()
                return
        await scheduler.stop()

    await asyncio.gather(
        scheduler._run_loop(),
        stop_after_suspension(),
    )

    # At least 5 consecutive failures — resolution suspended with backoff
    assert scheduler._resolution_failure_count >= 5
    assert scheduler._resolution_suspended_until is not None
    # Poll loop continued beyond the threshold
    assert poll_port.poll_cycle_count >= 8
    # Resolution cycle was called exactly 5 times (once per failure, then suspended for 300s backoff)
    assert poll_port.execute_resolution_cycle_call_count == 5


@pytest.mark.asyncio
async def test_resolution_resumes_after_backoff_expires(
    poll_port: FakePollPort,
) -> None:
    """Resolution retries after the suspension backoff expires, and resets on success."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=0,
        budget_limit=1000.0,
    )
    scheduler.running = True

    # Simulate being in the suspended state with an already-expired backoff
    scheduler._resolution_failure_count = 5
    scheduler._resolution_suspended_until = 0.0  # epoch — always in the past

    # Resolution succeeds this time (should_fail_resolution defaults to False)

    async def stop_after_reset() -> None:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if scheduler._resolution_failure_count == 0:
                await scheduler.stop()
                return
        await scheduler.stop()

    await asyncio.gather(
        scheduler._run_loop(),
        stop_after_reset(),
    )

    assert scheduler._resolution_failure_count == 0
    assert scheduler._resolution_suspended_until is None
    assert poll_port.execute_resolution_cycle_call_count >= 1


@pytest.mark.asyncio
async def test_notification_sent_on_resolution_threshold(
    poll_port: FakePollPort,
) -> None:
    """Notification is sent via NotificationPort when resolution failures hit the threshold."""
    notification_port = FakeNotificationPort()
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=0,
        budget_limit=1000.0,
        notification_port=notification_port,
    )
    scheduler.running = True
    poll_port.should_fail_resolution = True

    async def stop_after_threshold() -> None:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if scheduler._resolution_failure_count >= 5:
                await scheduler.stop()
                return
        await scheduler.stop()

    await asyncio.gather(
        scheduler._run_loop(),
        stop_after_threshold(),
    )

    assert notification_port.report_alert_call_count == 1
    alert = notification_port.reported_alerts[0]
    assert alert["alert"] == "resolution_pipeline_suspended"
    assert alert["consecutive_failures"] == 5


@pytest.mark.asyncio
async def test_resolution_failure_count_resets_on_success(
    poll_port: FakePollPort,
) -> None:
    """Failure counter resets to zero when a resolution cycle succeeds."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=0,
        budget_limit=1000.0,
    )
    scheduler.running = True

    # Pre-set failure counter; the next successful resolution cycle should reset it
    scheduler._resolution_failure_count = 3

    async def stop_after_success() -> None:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if scheduler._resolution_failure_count == 0 and \
               poll_port.execute_resolution_cycle_call_count >= 2:
                await scheduler.stop()
                return
        await scheduler.stop()

    await asyncio.gather(
        scheduler._run_loop(),
        stop_after_success(),
    )

    assert scheduler._resolution_failure_count == 0


@pytest.mark.asyncio
async def test_concurrent_cost_recording_is_thread_safe(
    poll_port: FakePollPort,
) -> None:
    """Test that concurrent cost recording is thread-safe with lock."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=1,
        budget_limit=100.00,
    )

    # Record costs concurrently to test lock protection
    tasks = [
        scheduler.record_cost("diagnose", 1.0) for _ in range(10)
    ]
    await asyncio.gather(*tasks)

    # Should have recorded all costs correctly
    assert scheduler._daily_cost_usd == 10.0


@pytest.mark.asyncio
async def test_start_without_poll_port_raises_value_error() -> None:
    """Test that start() raises ValueError when poll_port is None."""
    scheduler = DaemonScheduler(
        poll_port=None,
        poll_interval_seconds=1,
        budget_limit=100.00,
    )

    with pytest.raises(ValueError, match="poll_port must be set"):
        await scheduler.start()


@pytest.mark.asyncio
async def test_daemon_continues_after_investigation_threshold(
    poll_port: FakePollPort,
) -> None:
    """Daemon poll loop must survive 5 consecutive investigation failures and suspend them."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=0,
        budget_limit=1000.0,
    )
    scheduler.running = True

    # Each poll cycle returns one queued investigation so the investigation cycle runs
    poll_port.set_default_poll_result(
        PollResult(
            errors_found=1,
            new_signatures=1,
            updated_signatures=0,
            investigations_queued=1,
            timestamp=datetime.now(UTC),
        )
    )
    poll_port.should_fail_investigation = True

    async def stop_after_suspension() -> None:
        # Wait for at least 5 poll cycles beyond the failure threshold to confirm
        # investigations are suspended and poll loop continues
        for _ in range(200):
            await asyncio.sleep(0.01)
            if (
                scheduler._investigation_failure_count >= 5
                and poll_port.poll_cycle_count >= 8
            ):
                await scheduler.stop()
                return
        await scheduler.stop()

    # _run_loop and the stopper run concurrently; if the loop crashed, gather would raise
    await asyncio.gather(
        scheduler._run_loop(),
        stop_after_suspension(),
    )

    # At least 5 consecutive failures — investigations suspended with backoff
    assert scheduler._investigation_failure_count >= 5
    assert scheduler._investigation_suspended_until is not None
    # Poll loop continued beyond the threshold
    assert poll_port.poll_cycle_count >= 8
    # Investigation cycle was called exactly 5 times (once per failure, then suspended for 300s backoff)
    assert poll_port.execute_investigation_cycle_call_count == 5


@pytest.mark.asyncio
async def test_investigation_resumes_after_backoff_expires(
    poll_port: FakePollPort,
) -> None:
    """Investigations retry after the suspension backoff expires, and reset on success."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=0,
        budget_limit=1000.0,
    )
    scheduler.running = True

    # Simulate being in the suspended state with an already-expired backoff
    scheduler._investigation_failure_count = 5
    scheduler._investigation_suspended_until = 0.0  # epoch — always in the past

    poll_port.set_default_poll_result(
        PollResult(
            errors_found=1,
            new_signatures=1,
            updated_signatures=0,
            investigations_queued=1,
            timestamp=datetime.now(UTC),
        )
    )
    # Investigation succeeds this time (should_fail_investigation defaults to False)

    async def stop_after_reset() -> None:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if scheduler._investigation_failure_count == 0:
                await scheduler.stop()
                return
        await scheduler.stop()

    await asyncio.gather(
        scheduler._run_loop(),
        stop_after_reset(),
    )

    assert scheduler._investigation_failure_count == 0
    assert scheduler._investigation_suspended_until is None
    assert poll_port.execute_investigation_cycle_call_count >= 1


@pytest.mark.asyncio
async def test_notification_sent_on_investigation_threshold(
    poll_port: FakePollPort,
) -> None:
    """Notification is sent via NotificationPort when failures hit the threshold."""
    notification_port = FakeNotificationPort()
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=0,
        budget_limit=1000.0,
        notification_port=notification_port,
    )
    scheduler.running = True

    poll_port.set_default_poll_result(
        PollResult(
            errors_found=1,
            new_signatures=1,
            updated_signatures=0,
            investigations_queued=1,
            timestamp=datetime.now(UTC),
        )
    )
    poll_port.should_fail_investigation = True

    async def stop_after_threshold() -> None:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if scheduler._investigation_failure_count >= 5:
                await scheduler.stop()
                return
        await scheduler.stop()

    await asyncio.gather(
        scheduler._run_loop(),
        stop_after_threshold(),
    )

    assert notification_port.report_alert_call_count == 1
    alert = notification_port.reported_alerts[0]
    assert alert["alert"] == "investigation_pipeline_suspended"
    assert alert["consecutive_failures"] == 5


@pytest.mark.asyncio
async def test_notification_sent_only_once_per_failure_run(
    poll_port: FakePollPort,
) -> None:
    """Notification fires exactly once when threshold is crossed, not on subsequent failures."""
    notification_port = FakeNotificationPort()
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=0,
        budget_limit=1000.0,
        notification_port=notification_port,
    )
    scheduler.running = True

    # Pre-set to 4 failures; one more puts us at 5 (threshold), then failures accumulate past 5
    # with an already-expired suspension so retries run immediately
    scheduler._investigation_failure_count = 4
    scheduler._investigation_suspended_until = 0.0  # always expired

    poll_port.set_default_poll_result(
        PollResult(
            errors_found=1,
            new_signatures=1,
            updated_signatures=0,
            investigations_queued=1,
            timestamp=datetime.now(UTC),
        )
    )
    poll_port.should_fail_investigation = True

    async def stop_after_extra_failures() -> None:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if scheduler._investigation_failure_count >= 8:
                await scheduler.stop()
                return
        await scheduler.stop()

    await asyncio.gather(
        scheduler._run_loop(),
        stop_after_extra_failures(),
    )

    # Count crossed 5 only once, so notification fires exactly once
    assert notification_port.report_alert_call_count == 1


@pytest.mark.asyncio
async def test_no_notification_without_notification_port(
    poll_port: FakePollPort,
) -> None:
    """Daemon works normally when no notification_port is configured."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=0,
        budget_limit=1000.0,
        notification_port=None,
    )
    scheduler.running = True

    poll_port.set_default_poll_result(
        PollResult(
            errors_found=1,
            new_signatures=1,
            updated_signatures=0,
            investigations_queued=1,
            timestamp=datetime.now(UTC),
        )
    )
    poll_port.should_fail_investigation = True

    async def stop_after_threshold() -> None:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if scheduler._investigation_failure_count >= 5:
                await scheduler.stop()
                return
        await scheduler.stop()

    # Should not raise even without a notification port
    await asyncio.gather(
        scheduler._run_loop(),
        stop_after_threshold(),
    )

    assert scheduler._investigation_failure_count >= 5


@pytest.mark.asyncio
async def test_daemon_continues_when_notification_port_fails(
    poll_port: FakePollPort,
) -> None:
    """Daemon continues running even when the notification channel raises on report_alert."""
    notification_port = FakeNotificationPort()
    notification_port.should_fail = True
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=0,
        budget_limit=1000.0,
        notification_port=notification_port,
    )
    scheduler.running = True

    poll_port.set_default_poll_result(
        PollResult(
            errors_found=1,
            new_signatures=1,
            updated_signatures=0,
            investigations_queued=1,
            timestamp=datetime.now(UTC),
        )
    )
    poll_port.should_fail_investigation = True

    async def stop_after_threshold() -> None:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if (
                scheduler._investigation_failure_count >= 5
                and poll_port.poll_cycle_count >= 8
            ):
                await scheduler.stop()
                return
        await scheduler.stop()

    # If the try/except guard were absent this gather would raise RuntimeError
    await asyncio.gather(
        scheduler._run_loop(),
        stop_after_threshold(),
    )

    # Daemon survived the failing notification channel
    assert scheduler._investigation_failure_count >= 5
    assert poll_port.poll_cycle_count >= 8
    # report_alert was attempted (call count incremented before the failure check in the fake)
    assert notification_port.report_alert_call_count >= 1


@pytest.mark.asyncio
async def test_investigation_failure_count_resets_on_success(
    poll_port: FakePollPort,
) -> None:
    """Failure counter resets to zero when an investigation cycle succeeds."""
    scheduler = DaemonScheduler(
        poll_port=poll_port,
        poll_interval_seconds=0,
        budget_limit=1000.0,
    )
    scheduler.running = True

    # Pre-set failure counter; the next successful investigation cycle should reset it
    scheduler._investigation_failure_count = 3

    poll_port.set_default_poll_result(
        PollResult(
            errors_found=1,
            new_signatures=1,
            updated_signatures=0,
            investigations_queued=1,
            timestamp=datetime.now(UTC),
        )
    )

    async def stop_after_success() -> None:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if scheduler._investigation_failure_count == 0 and \
               poll_port.execute_investigation_cycle_call_count >= 2:
                await scheduler.stop()
                return
        await scheduler.stop()

    await asyncio.gather(
        scheduler._run_loop(),
        stop_after_success(),
    )

    assert scheduler._investigation_failure_count == 0
