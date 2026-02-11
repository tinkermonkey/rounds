"""Fake DiagnosisPort implementation for testing."""

from datetime import datetime
from typing import Optional

from rounds.core.models import Confidence, Diagnosis, InvestigationContext
from rounds.core.ports import DiagnosisPort


class FakeDiagnosisPort(DiagnosisPort):
    """In-memory diagnosis engine for testing.

    Allows tests to configure pre-determined diagnoses or use default canned
    responses. Tracks all diagnosis requests for test assertions.
    """

    def __init__(self):
        """Initialize with default values."""
        self.diagnoses: dict[str, Diagnosis] = {}
        self.default_diagnosis: Optional[Diagnosis] = None
        self.default_cost: float = 0.1
        self.diagnose_calls: list[InvestigationContext] = []
        self.estimate_cost_calls: list[InvestigationContext] = []
        self.should_fail: bool = False
        self.fail_message: str = "Diagnosis failed"

    def set_default_diagnosis(self, diagnosis: Diagnosis) -> None:
        """Set the default diagnosis to return for all requests."""
        self.default_diagnosis = diagnosis

    def set_default_cost(self, cost: float) -> None:
        """Set the default cost estimate."""
        self.default_cost = cost

    def set_diagnosis_for_signature(self, fingerprint: str, diagnosis: Diagnosis) -> None:
        """Set a specific diagnosis for a signature fingerprint."""
        self.diagnoses[fingerprint] = diagnosis

    async def diagnose(self, context: InvestigationContext) -> Diagnosis:
        """Generate a diagnosis for an investigation context.

        Returns a pre-configured diagnosis if available, otherwise returns
        the default diagnosis or generates a canned response.
        """
        self.diagnose_calls.append(context)

        if self.should_fail:
            raise RuntimeError(self.fail_message)

        # Check for signature-specific diagnosis
        fingerprint = context.signature.fingerprint
        if fingerprint in self.diagnoses:
            return self.diagnoses[fingerprint]

        # Return default if set
        if self.default_diagnosis:
            return self.default_diagnosis

        # Generate a canned response
        return Diagnosis(
            root_cause=f"Root cause for {context.signature.error_type}",
            evidence=(
                f"Evidence from trace: {context.signature.fingerprint}",
                f"Service: {context.signature.service}",
            ),
            suggested_fix="Review and apply recommended fix",
            confidence=Confidence.MEDIUM,
            diagnosed_at=datetime.now(),
            model="fake-model",
            cost_usd=self.default_cost,
        )

    async def estimate_cost(self, context: InvestigationContext) -> float:
        """Estimate the cost of diagnosis for a context.

        Returns the default cost estimate.
        """
        self.estimate_cost_calls.append(context)

        if self.should_fail:
            raise RuntimeError(self.fail_message)

        return self.default_cost

    def set_should_fail(self, should_fail: bool, message: str = "Diagnosis failed") -> None:
        """Configure the adapter to fail on the next operation."""
        self.should_fail = should_fail
        self.fail_message = message

    def reset(self) -> None:
        """Reset all collected data and state."""
        self.diagnoses.clear()
        self.default_diagnosis = None
        self.default_cost = 0.1
        self.diagnose_calls.clear()
        self.estimate_cost_calls.clear()
        self.should_fail = False
        self.fail_message = "Diagnosis failed"
