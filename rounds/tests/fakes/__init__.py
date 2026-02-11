"""Fake/mock implementations of core ports for testing.

These in-memory implementations allow core domain logic to be tested
without external dependencies:

- FakeTelemetryPort: In-memory error and trace storage
- FakeSignatureStorePort: In-memory signature persistence
- FakeDiagnosisPort: Canned diagnosis responses
- FakeNotificationPort: Captured notifications for assertion
"""
