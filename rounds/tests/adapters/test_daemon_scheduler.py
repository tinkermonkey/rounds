"""Tests for DaemonScheduler budget enforcement."""

import asyncio
from datetime import datetime, timezone, timedelta
import pytest

from rounds.adapters.scheduler.daemon import DaemonScheduler
from rounds.core.models import PollResult
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
    await scheduler.record_diagnosis_cost(3.00)
    await scheduler.record_diagnosis_cost(2.50)

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
    await scheduler.record_diagnosis_cost(8.00)
    assert scheduler._daily_cost_usd == 8.00

    original_cost = scheduler._daily_cost_usd
    original_date = scheduler._budget_date

    # Simulate date change by modifying the internal budget date to yesterday
    scheduler._budget_date = original_date - timedelta(days=1)

    # Record cost on today (new date) - should reset budget
    await scheduler.record_diagnosis_cost(2.00)
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

    await scheduler.record_diagnosis_cost(10.50)
    assert scheduler._daily_cost_usd == 10.50

    await scheduler.record_diagnosis_cost(15.25)
    assert scheduler._daily_cost_usd == 25.75

    await scheduler.record_diagnosis_cost(5.00)
    assert scheduler._daily_cost_usd == 30.75


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

    await scheduler.record_diagnosis_cost(1000.00)
    assert await scheduler._is_budget_exceeded() is False


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
            timestamp=datetime.now(timezone.utc),
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
        scheduler.record_diagnosis_cost(1.0) for _ in range(10)
    ]
    await asyncio.gather(*tasks)

    # Should have recorded all costs correctly
    assert scheduler._daily_cost_usd == 10.0
