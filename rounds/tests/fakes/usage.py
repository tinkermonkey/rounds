"""Fake UsageQueryPort implementation for testing."""

from rounds.core.ports import UsageQueryPort


class FakeUsageQueryPort(UsageQueryPort):
    """In-memory usage/cost query port for testing.

    Allows tests to configure a cost for a given trace ID and tracks all
    queries made for test assertions.
    """

    def __init__(self) -> None:
        self.costs: dict[str, float] = {}
        self.queries: list[str] = []
        self.should_fail: bool = False

    def set_cost(self, trace_id: str, cost_usd: float) -> None:
        """Configure the cost to return for a specific trace ID."""
        self.costs[trace_id] = cost_usd

    async def query_diagnosis_cost(self, trace_id: str) -> float:
        """Return the configured cost for trace_id, or 0.0 if unset."""
        self.queries.append(trace_id)
        if self.should_fail:
            raise RuntimeError("Usage query failed")
        return self.costs.get(trace_id, 0.0)
