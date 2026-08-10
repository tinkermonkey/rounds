"""Daemon scheduler adapter.

Implements a long-running asyncio polling loop that continuously
triggers poll cycles at configurable intervals.
"""

import asyncio
import logging
import signal
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from opentelemetry import metrics, trace

from rounds.core.models import HealthSnapshot, RoundStep
from rounds.core.ports import DigestFlushPort, NotificationPort, PollPort, SignatureStorePort

logger = logging.getLogger(__name__)

# Consecutive-failure thresholds for the investigation and resolution circuit
# breakers. Unlike poll_failure_threshold (configurable via
# DaemonScheduler.__init__), these are fixed at 5 - kept as named constants
# rather than inline magic numbers so the suspension-gating logic below and
# get_health_snapshot()'s investigation_suspended/resolution_suspended flags
# can't drift out of sync with each other.
INVESTIGATION_FAILURE_THRESHOLD = 5
RESOLUTION_FAILURE_THRESHOLD = 5


class DaemonScheduler:
    """Asyncio-based daemon scheduler for periodic poll cycles."""

    def __init__(
        self,
        poll_port: PollPort | None = None,
        poll_interval_seconds: int = 60,
        budget_limit: float | None = None,
        notification_port: NotificationPort | None = None,
        service_budget_map: dict[str, float] | None = None,
        poll_failure_threshold: int = 5,
        digest_notifier: DigestFlushPort | None = None,
        digest_interval_seconds: int = 86400,
        signature_store: SignatureStorePort | None = None,
    ):
        """Initialize daemon scheduler.

        Args:
            poll_port: PollPort implementation to call for poll cycles (can be set later).
            poll_interval_seconds: Interval between poll cycles in seconds.
            budget_limit: Daily budget limit in USD (None = unlimited).
            notification_port: NotificationPort to alert operators when the investigation
                or resolution pipeline is suspended due to persistent failures (optional).
            service_budget_map: Per-service daily USD budget cap, keyed by service
                name. Services with no entry are governed only by budget_limit.
            poll_failure_threshold: Number of consecutive execute_poll_cycle()
                failures before polling is suspended, an alert is raised, and
                get_health_snapshot() reports unhealthy.
            digest_notifier: DigestFlushPort implementation to flush on a schedule
                independent of poll_interval_seconds (see digest_interval_seconds).
                None (the default) disables WARN-digest flushing entirely.
            digest_interval_seconds: How often to flush the WARN digest, in
                seconds. Only relevant when digest_notifier is set. Checked
                every poll cycle but only acts once the window has elapsed,
                so it stays decoupled from poll_interval_seconds.
            signature_store: SignatureStorePort used to refresh signature counts
                by status once per poll cycle, for the self-observability
                dashboard's `rounds.signatures.count_by_status` gauge. None
                (the default) disables this refresh; signature_counts_by_status
                then stays empty.
        """
        self.poll_port = poll_port
        self.poll_interval_seconds = poll_interval_seconds
        self.budget_limit = budget_limit
        self.notification_port = notification_port
        self.poll_failure_threshold = poll_failure_threshold
        self.digest_notifier = digest_notifier
        self.digest_interval_seconds = digest_interval_seconds
        self.running = False
        self._task: asyncio.Task[None] | None = None
        self._daily_cost_usd = 0.0
        self._cost_by_step: dict[RoundStep, float] = defaultdict(float)
        self._cost_by_service: dict[str, float] = defaultdict(float)
        self._service_budget_map: dict[str, float] = dict(service_budget_map or {})
        self._budget_date = datetime.now(UTC).date()
        self._budget_lock = asyncio.Lock()
        self._investigation_failure_count = 0
        self._investigation_suspended_until: float | None = None
        self._resolution_failure_count = 0
        self._resolution_suspended_until: float | None = None
        self._poll_failure_count = 0
        self._poll_suspended_until: float | None = None
        self._last_poll_completed_at: datetime | None = None

        # Self-observability dashboard instrumentation. self._tracer/_meter
        # resolve to OpenTelemetry API no-ops when self-telemetry is disabled
        # (no provider registered in telemetry.py), so this instrumentation
        # carries zero export overhead in that case.
        self._signature_store = signature_store
        self._signature_counts_by_status: dict[str, int] = {}
        self._tracer = trace.get_tracer(__name__)
        meter = metrics.get_meter(__name__)
        self._poll_latency_histogram = meter.create_histogram(
            "rounds.daemon.poll_cycle.duration",
            unit="s",
            description="Duration of each executed poll cycle, in seconds",
        )
        self._investigation_latency_histogram = meter.create_histogram(
            "rounds.daemon.investigation_cycle.duration",
            unit="s",
            description="Duration of each executed investigation cycle, in seconds",
        )
        self._resolution_latency_histogram = meter.create_histogram(
            "rounds.daemon.resolution_cycle.duration",
            unit="s",
            description="Duration of each executed resolution cycle, in seconds",
        )

    async def start(self) -> None:
        """Start the daemon scheduler loop.

        Raises:
            ValueError: If poll_port is not set.
        """
        if self.poll_port is None:
            raise ValueError("poll_port must be set before starting the scheduler")

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
                # Store task reference to prevent garbage collection
                task = asyncio.create_task(self.stop())
                # Log any exceptions from stop() to prevent silent failures
                def _log_task_exception(t: asyncio.Task[None]) -> None:
                    try:
                        t.result()  # Retrieve result to raise any exception
                    except Exception as e:
                        logger.error(f"Error during signal-triggered shutdown: {e}", exc_info=True)
                task.add_done_callback(_log_task_exception)

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
        """Main daemon loop.

        Raises:
            ValueError: If poll_port is not set (should be caught by start()).
        """
        # Design pattern: Optional field with runtime validation.
        # The poll_port field is Optional to allow deferred initialization (set in start()),
        # but guaranteed non-None here by construction (start() must be called first).
        if self.poll_port is None:
            raise ValueError("poll_port must be set before _run_loop is called")

        poll_port = self.poll_port

        cycle_number = 0
        loop = asyncio.get_running_loop()

        while self.running:
            cycle_number += 1

            try:
                logger.debug(f"Starting poll cycle #{cycle_number}")

                # Check if budget is exceeded (captured once so poll/investigation
                # gating stays consistent within this cycle)
                budget_exceeded = await self._is_budget_exceeded()

                # Poll-cycle circuit breaker: mirrors the resolution/investigation
                # pattern below. result stays None when polling is suspended or
                # fails, which downstream resolution/investigation gating checks.
                result = None
                poll_now = loop.time()
                if (
                    self._poll_failure_count >= self.poll_failure_threshold
                    and self._poll_suspended_until is not None
                    and poll_now < self._poll_suspended_until
                ):
                    logger.warning(
                        f"Skipping poll cycle #{cycle_number}: "
                        f"{self._poll_failure_count} consecutive failures, "
                        f"polling suspended. "
                        f"Review logs for root cause; daemon will retry after backoff."
                    )
                else:
                    if self._poll_failure_count >= self.poll_failure_threshold:
                        logger.info(
                            f"Retrying poll cycle #{cycle_number} after suspension "
                            f"(previous consecutive failures: {self._poll_failure_count})"
                        )
                    poll_start_time = loop.time()
                    try:
                        with self._tracer.start_as_current_span(
                            "rounds.daemon.poll_cycle"
                        ) as span:
                            span.set_attribute("rounds.cycle_number", cycle_number)
                            span.set_attribute("rounds.budget_exceeded", budget_exceeded)
                            if budget_exceeded:
                                logger.warning(
                                    f"Daily budget limit exceeded (${self._daily_cost_usd:.2f}/"
                                    f"${self.budget_limit:.2f}), skipping investigation cycles"
                                )
                                # Still poll for errors, but don't diagnose
                                result = await poll_port.execute_poll_cycle()
                            else:
                                # Execute poll cycle
                                result = await poll_port.execute_poll_cycle()

                                elapsed = loop.time() - poll_start_time

                                logger.info(
                                    f"Poll cycle #{cycle_number} completed in {elapsed:.2f}s: "
                                    f"{result.errors_found} errors, "
                                    f"{result.new_signatures} new, "
                                    f"{result.updated_signatures} updated, "
                                    f"{result.investigations_queued} investigations queued"
                                )
                                span.set_attribute(
                                    "rounds.poll_cycle.errors_found", result.errors_found
                                )
                                span.set_attribute(
                                    "rounds.poll_cycle.new_signatures", result.new_signatures
                                )

                        self._poll_latency_histogram.record(
                            loop.time() - poll_start_time, {"outcome": "success"}
                        )
                        self._poll_failure_count = 0
                        self._poll_suspended_until = None
                        self._last_poll_completed_at = datetime.now(UTC)
                    except Exception as e:
                        self._poll_latency_histogram.record(
                            loop.time() - poll_start_time, {"outcome": "error"}
                        )
                        self._poll_failure_count += 1
                        backoff_seconds = max(self.poll_interval_seconds * 5, 300)
                        self._poll_suspended_until = loop.time() + backoff_seconds
                        logger.error(
                            f"Error in poll cycle #{cycle_number}: {e} "
                            f"(consecutive failures: {self._poll_failure_count})",
                            exc_info=True,
                        )
                        if self._poll_failure_count >= self.poll_failure_threshold:
                            logger.critical(
                                f"Poll cycle has failed {self._poll_failure_count} "
                                f"consecutive times. Suspending polling for "
                                f"{backoff_seconds}s. "
                                f"Review logs for root cause; daemon will retry after backoff."
                            )
                            if (
                                self._poll_failure_count == self.poll_failure_threshold
                                and self.notification_port is not None
                            ):
                                try:
                                    await self.notification_port.report_alert({
                                        "alert": "poll_cycle_pipeline_suspended",
                                        "consecutive_failures": self._poll_failure_count,
                                        "suspended_for_seconds": backoff_seconds,
                                        "message": (
                                            f"Rounds poll cycle has failed "
                                            f"{self._poll_failure_count} consecutive times. "
                                            f"Polling is suspended for {backoff_seconds}s. "
                                            f"Review logs for root cause."
                                        ),
                                    })
                                except Exception as notify_err:
                                    logger.error(
                                        f"Failed to send poll failure alert: {notify_err}",
                                        exc_info=True,
                                    )

                # Execute resolution cycle: auto-close signatures gone quiet.
                # Runs every cycle regardless of budget, since it incurs no
                # LLM cost (just a store scan and, optionally, issue closes).
                resolution_now = loop.time()
                if (
                    self._resolution_failure_count >= RESOLUTION_FAILURE_THRESHOLD
                    and self._resolution_suspended_until is not None
                    and resolution_now < self._resolution_suspended_until
                ):
                    logger.warning(
                        f"Skipping resolution cycle #{cycle_number}: "
                        f"{self._resolution_failure_count} consecutive failures, "
                        f"resolution suspended. "
                        f"Review logs for root cause; daemon poll loop continues."
                    )
                else:
                    if self._resolution_failure_count >= RESOLUTION_FAILURE_THRESHOLD:
                        logger.info(
                            f"Retrying resolution cycle #{cycle_number} after suspension "
                            f"(previous consecutive failures: {self._resolution_failure_count})"
                        )
                    resolution_start_time = loop.time()
                    try:
                        with self._tracer.start_as_current_span(
                            "rounds.daemon.resolution_cycle"
                        ) as span:
                            span.set_attribute("rounds.cycle_number", cycle_number)
                            resolution_result = await poll_port.execute_resolution_cycle()
                            span.set_attribute(
                                "rounds.resolution_cycle.signatures_resolved",
                                resolution_result.signatures_resolved,
                            )
                        self._resolution_latency_histogram.record(
                            loop.time() - resolution_start_time, {"outcome": "success"}
                        )
                        self._resolution_failure_count = 0
                        self._resolution_suspended_until = None
                        if resolution_result.signatures_resolved > 0:
                            logger.info(
                                f"Resolution cycle #{cycle_number} completed: "
                                f"{resolution_result.signatures_resolved} signatures auto-resolved"
                            )
                    except Exception as e:
                        self._resolution_latency_histogram.record(
                            loop.time() - resolution_start_time, {"outcome": "error"}
                        )
                        self._resolution_failure_count += 1
                        backoff_seconds = max(self.poll_interval_seconds * 5, 300)
                        self._resolution_suspended_until = loop.time() + backoff_seconds
                        logger.error(
                            f"Error in resolution cycle #{cycle_number}: {e} "
                            f"(consecutive failures: {self._resolution_failure_count})",
                            exc_info=True,
                        )
                        if self._resolution_failure_count >= RESOLUTION_FAILURE_THRESHOLD:
                            logger.critical(
                                f"Resolution cycle has failed {self._resolution_failure_count} "
                                f"consecutive times. Suspending resolution for "
                                f"{backoff_seconds}s. "
                                f"Review logs for root cause; daemon poll loop continues."
                            )
                            if (
                                self._resolution_failure_count == RESOLUTION_FAILURE_THRESHOLD
                                and self.notification_port is not None
                            ):
                                try:
                                    await self.notification_port.report_alert({
                                        "alert": "resolution_pipeline_suspended",
                                        "consecutive_failures": self._resolution_failure_count,
                                        "suspended_for_seconds": backoff_seconds,
                                        "message": (
                                            f"Rounds resolution pipeline has failed "
                                            f"{self._resolution_failure_count} consecutive times. "
                                            f"Auto-resolution is suspended for {backoff_seconds}s. "
                                            f"Review logs for root cause."
                                        ),
                                    })
                                except Exception as notify_err:
                                    logger.error(
                                        f"Failed to send resolution failure alert: {notify_err}",
                                        exc_info=True,
                                    )

                # Flush the WARN digest if its window has elapsed. Runs every
                # cycle regardless of poll/budget/resolution outcome, on a
                # schedule governed by digest_interval_seconds rather than
                # poll_interval_seconds, so the digest cadence is independent
                # of how often the daemon polls.
                if self.digest_notifier is not None:
                    try:
                        await self.digest_notifier.flush_if_due(
                            datetime.now(UTC),
                            timedelta(seconds=self.digest_interval_seconds),
                        )
                    except Exception as e:
                        logger.error(f"Error flushing WARN digest: {e}", exc_info=True)

                # Execute investigation cycle for pending diagnoses.
                # Skipped when budget is exhausted, since diagnosis incurs LLM cost,
                # or when this cycle's poll was suspended/failed (result is None).
                if (
                    not budget_exceeded
                    and result is not None
                    and result.investigations_queued > 0
                ):
                    now = loop.time()
                    if (
                        self._investigation_failure_count >= INVESTIGATION_FAILURE_THRESHOLD
                        and self._investigation_suspended_until is not None
                        and now < self._investigation_suspended_until
                    ):
                        logger.warning(
                            f"Skipping investigation cycle #{cycle_number}: "
                            f"{self._investigation_failure_count} consecutive failures, "
                            f"investigations suspended. "
                            f"Review logs for root cause; daemon poll loop continues."
                        )
                    else:
                        if self._investigation_failure_count >= INVESTIGATION_FAILURE_THRESHOLD:
                            logger.info(
                                f"Retrying investigation cycle #{cycle_number} after suspension "
                                f"(previous consecutive failures: {self._investigation_failure_count})"
                            )
                        else:
                            logger.debug(f"Starting investigation cycle #{cycle_number}")
                        investigation_start_time = loop.time()
                        try:
                            with self._tracer.start_as_current_span(
                                "rounds.daemon.investigation_cycle"
                            ) as span:
                                span.set_attribute("rounds.cycle_number", cycle_number)
                                inv_result = await poll_port.execute_investigation_cycle()
                                span.set_attribute(
                                    "rounds.investigation_cycle.diagnoses_produced",
                                    len(inv_result.diagnoses_produced),
                                )
                                span.set_attribute(
                                    "rounds.investigation_cycle.investigations_failed",
                                    inv_result.investigations_failed,
                                )
                            self._investigation_latency_histogram.record(
                                loop.time() - investigation_start_time, {"outcome": "success"}
                            )
                            self._investigation_failure_count = 0
                            self._investigation_suspended_until = None
                            logger.info(
                                f"Investigation cycle #{cycle_number} completed: "
                                f"{len(inv_result.diagnoses_produced)} diagnoses produced, "
                                f"{inv_result.investigations_failed} failed "
                                f"(out of {inv_result.investigations_attempted} attempted)"
                            )
                        except Exception as e:
                            self._investigation_latency_histogram.record(
                                loop.time() - investigation_start_time, {"outcome": "error"}
                            )
                            self._investigation_failure_count += 1
                            backoff_seconds = max(self.poll_interval_seconds * 5, 300)
                            self._investigation_suspended_until = loop.time() + backoff_seconds
                            logger.error(
                                f"Error in investigation cycle #{cycle_number}: {e} "
                                f"(consecutive failures: {self._investigation_failure_count})",
                                exc_info=True,
                            )
                            if self._investigation_failure_count >= INVESTIGATION_FAILURE_THRESHOLD:
                                logger.critical(
                                    f"Investigation cycle has failed {self._investigation_failure_count} "
                                    f"consecutive times. Suspending investigations for "
                                    f"{backoff_seconds}s. "
                                    f"Review logs for root cause; daemon poll loop continues."
                                )
                                if (
                                    self._investigation_failure_count == INVESTIGATION_FAILURE_THRESHOLD
                                    and self.notification_port is not None
                                ):
                                    try:
                                        await self.notification_port.report_alert({
                                            "alert": "investigation_pipeline_suspended",
                                            "consecutive_failures": self._investigation_failure_count,
                                            "suspended_for_seconds": backoff_seconds,
                                            "message": (
                                                f"Rounds investigation pipeline has failed "
                                                f"{self._investigation_failure_count} consecutive times. "
                                                f"Diagnoses are suspended for {backoff_seconds}s. "
                                                f"Review logs for root cause."
                                            ),
                                        })
                                    except Exception as notify_err:
                                        logger.error(
                                            f"Failed to send investigation failure alert: {notify_err}",
                                            exc_info=True,
                                        )

                # Refresh cached signature counts by status for the
                # self-observability dashboard's gauge. Runs once per cycle,
                # independent of poll/budget/resolution outcome, so the count
                # stays reasonably current even if polling is suspended.
                await self._refresh_signature_counts()

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

    async def _refresh_signature_counts(self) -> None:
        """Refresh the cached signature-counts-by-status snapshot.

        No-op when no signature_store was configured. Failures are logged
        and swallowed so a store hiccup never interrupts the poll loop -
        the dashboard gauge simply reports the last-known counts until the
        next successful refresh.
        """
        if self._signature_store is None:
            return

        try:
            stats = await self._signature_store.get_stats()
            self._signature_counts_by_status = dict(stats.by_status)
        except Exception as e:
            logger.warning(
                f"Failed to refresh signature counts for telemetry: {e}", exc_info=True
            )

    async def _is_budget_exceeded(self) -> bool:
        """Check if daily budget limit has been exceeded.

        Thread-safe: Uses the same asyncio.Lock as record_cost to protect
        budget state mutations and prevent TOCTOU races.

        Returns:
            True if budget_limit is set and daily cost exceeds it.
        """
        if self.budget_limit is None:
            return False

        async with self._budget_lock:
            # Reset daily cost if date has changed
            today = datetime.now(UTC).date()
            if today != self._budget_date:
                self._daily_cost_usd = 0.0
                self._cost_by_step.clear()
                self._cost_by_service.clear()
                self._budget_date = today
                return False

            return self._daily_cost_usd >= self.budget_limit

    async def is_service_budget_exceeded(self, service: str) -> bool:
        """Check if a service's per-service daily budget cap has been reached.

        Thread-safe: Uses the same asyncio.Lock as record_cost to protect
        budget state mutations and prevent TOCTOU races.

        Returns:
            False when no cap is configured for the service (uncapped
            services are governed only by the global daily budget), or when
            the accumulated cost for the service is still under its cap.
        """
        cap = self._service_budget_map.get(service)
        if cap is None:
            return False

        async with self._budget_lock:
            # Reset daily cost if date has changed
            today = datetime.now(UTC).date()
            if today != self._budget_date:
                self._daily_cost_usd = 0.0
                self._cost_by_step.clear()
                self._cost_by_service.clear()
                self._budget_date = today
                return False

            return self._cost_by_service.get(service, 0.0) >= cap

    async def record_cost(
        self, step: RoundStep, cost_usd: float, *, service: str | None = None
    ) -> None:
        """Record a rounds step's cost towards the daily budget.

        Thread-safe: uses asyncio.Lock to protect budget state mutations.

        Args:
            step: Which rounds step (poll, fingerprint, diagnose, confirm)
                incurred the cost, tracked separately in cost_by_step for
                per-step spend visibility.
            cost_usd: Cost incurred by that step, in USD.
            service: The service to also attribute this cost to, tracked
                separately in cost_by_service for per-service spend
                visibility and budget cap enforcement. Optional - costs not
                attributable to a single signature's service (e.g. poll,
                fingerprint) omit it.
        """
        async with self._budget_lock:
            # Reset daily cost if date has changed
            today = datetime.now(UTC).date()
            if today != self._budget_date:
                self._daily_cost_usd = 0.0
                self._cost_by_step.clear()
                self._cost_by_service.clear()
                self._budget_date = today

            self._daily_cost_usd += cost_usd
            self._cost_by_step[step] += cost_usd

            if self.budget_limit and self._daily_cost_usd >= self.budget_limit:
                logger.warning(
                    f"Daily budget limit reached (${self._daily_cost_usd:.2f}/"
                    f"${self.budget_limit:.2f})"
                )

            if service is not None:
                self._cost_by_service[service] += cost_usd
                cap = self._service_budget_map.get(service)
                if cap is not None and self._cost_by_service[service] >= cap:
                    logger.warning(
                        f"Per-service budget cap reached for '{service}' "
                        f"(${self._cost_by_service[service]:.2f}/${cap:.2f}), "
                        "further investigations for this service will be skipped today"
                    )

    @property
    def cost_by_step(self) -> dict[RoundStep, float]:
        """Read-only snapshot of today's accumulated cost, broken down by RoundStep."""
        return dict(self._cost_by_step)

    @property
    def cost_by_service(self) -> dict[str, float]:
        """Read-only snapshot of today's accumulated cost, broken down by service."""
        return dict(self._cost_by_service)

    @property
    def daily_cost_usd(self) -> float:
        """Read-only snapshot of today's total accumulated diagnosis cost, in USD."""
        return self._daily_cost_usd

    @property
    def signature_counts_by_status(self) -> dict[str, int]:
        """Read-only snapshot of signature counts by status.

        Refreshed once per poll cycle when a signature_store was configured;
        empty otherwise.
        """
        return dict(self._signature_counts_by_status)

    def get_health_snapshot(self) -> HealthSnapshot:
        """Read-only snapshot of daemon health, for monitoring endpoints.

        May be called from a different thread than the one running the poll
        loop (e.g. the HTTP server thread answering /health), so each
        mutable counter is read into a local variable exactly once and that
        local is reused everywhere it's needed - never re-reading the
        attribute - so a concurrent increment from the poll loop can't
        produce an internally contradictory snapshot.

        Reports unhealthy once consecutive_poll_failures reaches
        poll_failure_threshold, or once the investigation or resolution
        circuit breaker has tripped, independent of any single telemetry
        backend.
        """
        poll_failure_count = self._poll_failure_count
        return HealthSnapshot(
            last_poll_completed_at=self._last_poll_completed_at,
            consecutive_poll_failures=poll_failure_count,
            poll_failure_threshold=self.poll_failure_threshold,
            investigation_suspended=(
                self._investigation_failure_count >= INVESTIGATION_FAILURE_THRESHOLD
            ),
            resolution_suspended=(
                self._resolution_failure_count >= RESOLUTION_FAILURE_THRESHOLD
            ),
        )

    async def run_investigation_cycle(self) -> None:
        """Run a single investigation cycle (on-demand)."""
        if self.poll_port is None:
            raise ValueError("poll_port must be set to run investigation cycle")

        try:
            logger.info("Starting on-demand investigation cycle")
            result = await self.poll_port.execute_investigation_cycle()
            logger.info(
                f"Investigation cycle completed: "
                f"{len(result.diagnoses_produced)} diagnoses, "
                f"{result.investigations_failed} failed"
            )
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
        notification_port: NotificationPort | None = None,
    ) -> DaemonScheduler:
        """Create a new daemon scheduler instance.

        Args:
            poll_port: PollPort implementation to call for poll cycles.
            poll_interval_seconds: Interval between poll cycles in seconds.
            budget_limit: Daily budget limit in USD (None = unlimited).
            notification_port: NotificationPort to alert on persistent failures.

        Returns:
            DaemonScheduler instance.
        """
        return DaemonScheduler(
            poll_port=poll_port,
            poll_interval_seconds=poll_interval_seconds,
            budget_limit=budget_limit,
            notification_port=notification_port,
        )

    @staticmethod
    async def run_daemon(
        poll_port: PollPort,
        poll_interval_seconds: int = 60,
        budget_limit: float | None = None,
        notification_port: NotificationPort | None = None,
    ) -> None:
        """Create and run a daemon scheduler (blocking until stopped).

        Args:
            poll_port: PollPort implementation to call for poll cycles.
            poll_interval_seconds: Interval between poll cycles in seconds.
            budget_limit: Daily budget limit in USD (None = unlimited).
            notification_port: NotificationPort to alert on persistent failures.
        """
        daemon = DaemonFactory.create(
            poll_port=poll_port,
            poll_interval_seconds=poll_interval_seconds,
            budget_limit=budget_limit,
            notification_port=notification_port,
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
