"""Fake HealthCheckPort for testing."""

from rounds.core.models import HealthSnapshot


class FakeHealthCheckPort:
    """In-memory HealthCheckPort returning a preconfigured snapshot."""

    def __init__(self, snapshot: HealthSnapshot) -> None:
        self.snapshot = snapshot

    def get_health_snapshot(self) -> HealthSnapshot:
        """Return the preconfigured snapshot."""
        return self.snapshot
