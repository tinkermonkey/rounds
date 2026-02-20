"""Fake Investigator for testing."""

from datetime import UTC, datetime

from rounds.core.investigator import Investigator
from rounds.core.models import Diagnosis, Signature
from rounds.core.triage import TriageEngine
from rounds.tests.fakes.diagnosis import FakeDiagnosisPort
from rounds.tests.fakes.notification import FakeNotificationPort
from rounds.tests.fakes.store import FakeSignatureStorePort
from rounds.tests.fakes.telemetry import FakeTelemetryPort


class FakeInvestigator(Investigator):
    """Fake investigator that returns pre-configured diagnoses without calling external services."""

    def __init__(self, diagnosis_to_return: Diagnosis | None = None, raise_error: Exception | None = None):
        """Initialize fake investigator.

        Args:
            diagnosis_to_return: Diagnosis to return when investigating. If None, returns a default diagnosis.
            raise_error: Exception to raise when investigating. If provided, raises instead of returning diagnosis.
        """
        # Create fake port implementations from existing test fakes
        fake_telemetry = FakeTelemetryPort()
        fake_store = FakeSignatureStorePort()
        fake_diagnosis = FakeDiagnosisPort()
        fake_notification = FakeNotificationPort()
        fake_triage = TriageEngine()

        # Initialize parent with fake ports
        super().__init__(
            telemetry=fake_telemetry,
            store=fake_store,
            diagnosis_engine=fake_diagnosis,
            notification=fake_notification,
            triage=fake_triage,
            codebase_path="/fake/codebase",
        )

        self.diagnosis_to_return = diagnosis_to_return
        self.raise_error = raise_error
        self.investigated_signatures: list[Signature] = []

    async def investigate(self, signature: Signature) -> Diagnosis:
        """Investigate a signature.

        Args:
            signature: The signature to investigate.

        Returns:
            Pre-configured diagnosis.

        Raises:
            Configured exception if raise_error is set.
        """
        self.investigated_signatures.append(signature)

        if self.raise_error:
            raise self.raise_error

        if self.diagnosis_to_return:
            return self.diagnosis_to_return

        # Return default diagnosis
        return Diagnosis(
            root_cause="Fake root cause",
            evidence=("Fake evidence 1", "Fake evidence 2"),
            suggested_fix="Fake suggested fix",
            confidence="medium",
            diagnosed_at=datetime.now(UTC),
            model="fake-model",
            cost_usd=0.01,
        )
