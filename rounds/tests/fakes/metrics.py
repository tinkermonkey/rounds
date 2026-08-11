"""Fake DashboardMetricsPort for testing."""

from rounds.core.models import RoundStep


class FakeDashboardMetricsPort:
    """In-memory DashboardMetricsPort returning preconfigured dashboard state."""

    def __init__(
        self,
        signature_counts_by_status: dict[str, int] | None = None,
        daily_cost_usd: float = 0.0,
        cost_by_step: dict[RoundStep, float] | None = None,
        cost_by_service: dict[str, float] | None = None,
    ) -> None:
        self._signature_counts_by_status = signature_counts_by_status or {}
        self._daily_cost_usd = daily_cost_usd
        self._cost_by_step = cost_by_step or {}
        self._cost_by_service = cost_by_service or {}

    @property
    def signature_counts_by_status(self) -> dict[str, int]:
        """Return the preconfigured signature counts."""
        return self._signature_counts_by_status

    @property
    def daily_cost_usd(self) -> float:
        """Return the preconfigured daily cost."""
        return self._daily_cost_usd

    @property
    def cost_by_step(self) -> dict[RoundStep, float]:
        """Return the preconfigured cost breakdown by step."""
        return self._cost_by_step

    @property
    def cost_by_service(self) -> dict[str, float]:
        """Return the preconfigured cost breakdown by service."""
        return self._cost_by_service


class FakeFailingDashboardMetricsPort:
    """DashboardMetricsPort that raises, simulating a broken metrics query."""

    @property
    def signature_counts_by_status(self) -> dict[str, int]:
        """Raise unconditionally, as if computing the counts failed."""
        raise RuntimeError("simulated dashboard metrics failure")

    @property
    def daily_cost_usd(self) -> float:
        """Raise unconditionally, as if computing the cost failed."""
        raise RuntimeError("simulated dashboard metrics failure")

    @property
    def cost_by_step(self) -> dict[RoundStep, float]:
        """Raise unconditionally, as if computing the cost breakdown failed."""
        raise RuntimeError("simulated dashboard metrics failure")

    @property
    def cost_by_service(self) -> dict[str, float]:
        """Raise unconditionally, as if computing the cost breakdown failed."""
        raise RuntimeError("simulated dashboard metrics failure")
