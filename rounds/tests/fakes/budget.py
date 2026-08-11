"""Fake BudgetTracker for testing."""

from rounds.core.models import RoundStep


class FakeBudgetTracker:
    """In-memory BudgetTracker that records every call for test assertions."""

    def __init__(self) -> None:
        self.recorded_costs: list[tuple[RoundStep, float]] = []
        self.recorded_services: list[str | None] = []
        self.exceeded_services: set[str] = set()

    async def record_cost(
        self, step: RoundStep, cost_usd: float, *, service: str | None = None
    ) -> None:
        """Record a cost incurred by a rounds step towards the daily budget."""
        self.recorded_costs.append((step, cost_usd))
        self.recorded_services.append(service)

    async def is_service_budget_exceeded(self, service: str) -> bool:
        """Return whether the given service was pre-configured as budget-exhausted."""
        return service in self.exceeded_services
