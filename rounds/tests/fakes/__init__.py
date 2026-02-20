"""Fake/mock implementations of core ports for testing.

These in-memory implementations allow core domain logic to be tested
without external dependencies:

- FakeTelemetryPort: In-memory error and trace storage
- FakeSignatureStorePort: In-memory signature persistence
- FakeDiagnosisPort: Canned diagnosis responses
- FakeNotificationPort: Captured notifications for assertion
- FakePollPort: Captured poll cycle results
- FakeManagementPort: Captured management operations
"""

from .diagnosis import FakeDiagnosisPort
from .management import FakeManagementPort
from .notification import FakeNotificationPort
from .poll import FakePollPort
from .store import FakeSignatureStorePort
from .telemetry import FakeTelemetryPort

__all__ = [
    "FakeDiagnosisPort",
    "FakeManagementPort",
    "FakeNotificationPort",
    "FakePollPort",
    "FakeSignatureStorePort",
    "FakeTelemetryPort",
]
