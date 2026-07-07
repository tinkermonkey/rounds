"""Fake BudgetTracker for testing."""

from rounds.core.models import RoundStep


class FakeBudgetTracker:
    """In-memory BudgetTracker that records every call for test assertions."""

    def __init__(self) -> None:
        self.recorded_costs: list[tuple[RoundStep, float]] = []

    async def record_cost(self, step: RoundStep, cost_usd: float) -> None:
        """Record a cost incurred by a rounds step towards the daily budget."""
        self.recorded_costs.append((step, cost_usd))
