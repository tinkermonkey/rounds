"""Daemon scheduler adapter.

Implements a long-running asyncio polling loop that continuously
triggers poll cycles at configurable intervals.
"""

import asyncio
import logging
import signal
from datetime import datetime, timezone

from rounds.core.ports import PollPort

logger = logging.getLogger(__name__)


class DaemonScheduler:
    """Asyncio-based daemon scheduler for periodic poll cycles."""

    def __init__(
        self,
        poll_port: PollPort,
        poll_interval_seconds: int = 60,
        budget_limit: float | None = None,
    ):
        """Initialize daemon scheduler.

        Args:
            poll_port: PollPort implementation to call for poll cycles.
            poll_interval_seconds: Interval between poll cycles in seconds.
            budget_limit: Daily budget limit in USD (None = unlimited).
        """
        self.poll_port = poll_port
        self.poll_interval_seconds = poll_interval_seconds
        self.budget_limit = budget_limit
        self.running = False
        self._task: asyncio.Task[None] | None = None
        self._daily_cost_usd = 0.0
        self._budget_date = datetime.now(timezone.utc).date()

    async def start(self) -> None:
        """Start the daemon scheduler loop."""
        if self.running:
            logger.warning("Daemon scheduler already running")
            return

        self.running = True
        logger.info(
            f"Starting daemon scheduler with {self.poll_interval_seconds}s interval"
        )

        # Set up signal handlers for graceful shutdown
        self._setup_signal_handlers()

        # Run the main loop
        try:
            await self._run_loop()
        except asyncio.CancelledError:
            logger.info("Daemon scheduler cancelled")
        except Exception as e:
            logger.error(f"Daemon scheduler error: {e}", exc_info=True)
        finally:
            self.running = False
            logger.info("Daemon scheduler stopped")

    async def stop(self) -> None:
        """Stop the daemon scheduler loop."""
        if not self.running:
            return

        logger.info("Stopping daemon scheduler...")
        self.running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        try:
            loop = asyncio.get_running_loop()

            def _handle_signal(sig: int) -> None:
                logger.info(f"Received signal {sig}, initiating graceful shutdown...")
                asyncio.create_task(self.stop())

            # Register handlers for SIGTERM and SIGINT
            loop.add_signal_handler(
                signal.SIGTERM, _handle_signal, signal.SIGTERM
            )
            loop.add_signal_handler(signal.SIGINT, _handle_signal, signal.SIGINT)
        except NotImplementedError:
            # Signal handlers not available on Windows
            logger.debug("Signal handlers not available on this platform")
        except Exception as e:
            logger.warning(f"Failed to set up signal handlers: {e}")

    async def _run_loop(self) -> None:
        """Main daemon loop."""
        cycle_number = 0
        loop = asyncio.get_running_loop()

        while self.running:
            cycle_number += 1

            try:
                logger.debug(f"Starting poll cycle #{cycle_number}")

                # Check if budget is exceeded
                if self._is_budget_exceeded():
                    logger.warning(
                        f"Daily budget limit exceeded (${self._daily_cost_usd:.2f}/"
                        f"${self.budget_limit:.2f}), skipping investigation cycles"
                    )
                    # Still poll for errors, but don't diagnose
                    result = await self.poll_port.execute_poll_cycle()
                else:
                    start_time = loop.time()

                    # Execute poll cycle
                    result = await self.poll_port.execute_poll_cycle()

                    elapsed = loop.time() - start_time

                    logger.info(
                        f"Poll cycle #{cycle_number} completed in {elapsed:.2f}s: "
                        f"{result.errors_found} errors, "
                        f"{result.new_signatures} new, "
                        f"{result.updated_signatures} updated, "
                        f"{result.investigations_queued} investigations queued"
                    )

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    f"Error in poll cycle #{cycle_number}: {e}", exc_info=True
                )

            # Wait before next cycle
            if self.running:
                try:
                    await asyncio.sleep(self.poll_interval_seconds)
                except asyncio.CancelledError:
                    raise

    def _is_budget_exceeded(self) -> bool:
        """Check if daily budget limit has been exceeded.

        Returns:
            True if budget_limit is set and daily cost exceeds it.
        """
        if self.budget_limit is None:
            return False

        # Reset daily cost if date has changed
        today = datetime.now(timezone.utc).date()
        if today != self._budget_date:
            self._daily_cost_usd = 0.0
            self._budget_date = today
            return False

        return self._daily_cost_usd >= self.budget_limit

    def record_diagnosis_cost(self, cost_usd: float) -> None:
        """Record a diagnosis cost towards the daily budget.

        Args:
            cost_usd: Cost of the diagnosis in USD.
        """
        # Reset daily cost if date has changed
        today = datetime.now(timezone.utc).date()
        if today != self._budget_date:
            self._daily_cost_usd = 0.0
            self._budget_date = today

        self._daily_cost_usd += cost_usd

        if self.budget_limit and self._daily_cost_usd >= self.budget_limit:
            logger.warning(
                f"Daily budget limit reached (${self._daily_cost_usd:.2f}/"
                f"${self.budget_limit:.2f})"
            )

    async def run_investigation_cycle(self) -> None:
        """Run a single investigation cycle (on-demand)."""
        try:
            logger.info("Starting on-demand investigation cycle")
            diagnoses = await self.poll_port.execute_investigation_cycle()
            logger.info(f"Investigation cycle completed: {len(diagnoses)} diagnoses")
        except Exception as e:
            logger.error(f"Error in investigation cycle: {e}", exc_info=True)
            raise


class DaemonFactory:
    """Factory for creating and running daemon instances."""

    @staticmethod
    def create(
        poll_port: PollPort,
        poll_interval_seconds: int = 60,
        budget_limit: float | None = None,
    ) -> DaemonScheduler:
        """Create a new daemon scheduler instance.

        Args:
            poll_port: PollPort implementation to call for poll cycles.
            poll_interval_seconds: Interval between poll cycles in seconds.
            budget_limit: Daily budget limit in USD (None = unlimited).

        Returns:
            DaemonScheduler instance.
        """
        return DaemonScheduler(
            poll_port=poll_port,
            poll_interval_seconds=poll_interval_seconds,
            budget_limit=budget_limit,
        )

    @staticmethod
    async def run_daemon(
        poll_port: PollPort,
        poll_interval_seconds: int = 60,
        budget_limit: float | None = None,
    ) -> None:
        """Create and run a daemon scheduler (blocking until stopped).

        Args:
            poll_port: PollPort implementation to call for poll cycles.
            poll_interval_seconds: Interval between poll cycles in seconds.
            budget_limit: Daily budget limit in USD (None = unlimited).
        """
        daemon = DaemonFactory.create(
            poll_port=poll_port,
            poll_interval_seconds=poll_interval_seconds,
            budget_limit=budget_limit,
        )
        await daemon.start()

    @staticmethod
    async def run_single_cycle(poll_port: PollPort) -> None:
        """Run a single poll cycle (non-daemon mode).

        Args:
            poll_port: PollPort implementation to call for poll cycle.
        """
        try:
            logger.info("Running single poll cycle")
            result = await poll_port.execute_poll_cycle()
            logger.info(
                f"Poll cycle completed: {result.errors_found} errors, "
                f"{result.new_signatures} new signatures"
            )
        except Exception as e:
            logger.error(f"Error in poll cycle: {e}", exc_info=True)
            raise
