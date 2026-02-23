"""Fake PollPort implementation for testing."""

from datetime import UTC, datetime

from rounds.core.models import InvestigationResult, PollResult
from rounds.core.ports import PollPort


class FakePollPort(PollPort):
    """In-memory poll port for testing.

    Allows tests to configure and track poll cycle executions.
    """

    def __init__(self) -> None:
        """Initialize with default values."""
        self.poll_results: list[PollResult] = []
        self.investigation_results: list[InvestigationResult] = []
        self.execute_poll_cycle_call_count = 0
        self.execute_investigation_cycle_call_count = 0
        self.default_poll_result: PollResult | None = None
        self.default_investigation_result: InvestigationResult | None = None
        self.should_fail: bool = False
        self.fail_message: str = "Poll failed"
        self.should_fail_investigation: bool = False

    def set_default_poll_result(self, result: PollResult) -> None:
        """Set the default poll result to return."""
        self.default_poll_result = result

    def set_default_investigation_result(self, result: InvestigationResult) -> None:
        """Set the default investigation result to return."""
        self.default_investigation_result = result

    def add_poll_result(self, result: PollResult) -> None:
        """Queue a poll result to be returned on next call."""
        self.poll_results.append(result)

    def add_investigation_result(self, result: InvestigationResult) -> None:
        """Queue an investigation result to be returned on next call."""
        self.investigation_results.append(result)

    @property
    def poll_cycle_count(self) -> int:
        """Alias for execute_poll_cycle_call_count for test compatibility."""
        return self.execute_poll_cycle_call_count

    async def execute_poll_cycle(self) -> PollResult:
        """Execute a poll cycle.

        Returns queued results or the default result.
        """
        self.execute_poll_cycle_call_count += 1

        if self.should_fail:
            raise RuntimeError(self.fail_message)

        if self.poll_results:
            return self.poll_results.pop(0)

        if self.default_poll_result:
            return self.default_poll_result

        # Return a default empty result
        return PollResult(
            errors_found=0,
            new_signatures=0,
            updated_signatures=0,
            investigations_queued=0,
            timestamp=datetime.now(UTC),
        )

    async def execute_investigation_cycle(self) -> InvestigationResult:
        """Execute an investigation cycle.

        Returns queued results or the default result.
        """
        self.execute_investigation_cycle_call_count += 1

        if self.should_fail or self.should_fail_investigation:
            raise RuntimeError(self.fail_message)

        if self.investigation_results:
            return self.investigation_results.pop(0)

        if self.default_investigation_result is not None:
            return self.default_investigation_result

        # Return empty result
        return InvestigationResult(
            diagnoses_produced=(),
            investigations_attempted=0,
            investigations_failed=0,
        )

    def set_should_fail(self, should_fail: bool, message: str = "Poll failed") -> None:
        """Configure the adapter to fail on the next operation."""
        self.should_fail = should_fail
        self.fail_message = message

    def reset(self) -> None:
        """Reset all collected data and state."""
        self.poll_results.clear()
        self.investigation_results.clear()
        self.execute_poll_cycle_call_count = 0
        self.execute_investigation_cycle_call_count = 0
        self.default_poll_result = None
        self.default_investigation_result = None
        self.should_fail = False
        self.should_fail_investigation = False
        self.fail_message = "Poll failed"
