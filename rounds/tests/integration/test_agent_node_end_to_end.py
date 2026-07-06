"""End-to-end integration test for the agent-node diagnosis path.

BA Req 11: an integration test must trigger a known error from a target
codebase ("context-library") and verify that rounds diagnoses it
end-to-end via the local agent node, producing a source-level root
cause. tests/fixtures/context_library/pricing.py contains one
deliberate bug for this purpose.

The test exercises the full daemon cycle — telemetry -> fingerprint ->
signature store -> triage -> AgentNodeDiagnosisAdapter -> notification —
using a real SQLite-backed store and a fake AgentNodeClient standing in
for the SSH transport. The fake client reads the fixture's actual source
file at the workspace path it is given (exactly as the real `claude`
CLI running on the agent node would), so the resulting diagnosis is
tied to genuine source content rather than a canned string.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from rounds.adapters.diagnosis.agent_node import AgentNodeDiagnosisAdapter, ServiceMapping
from rounds.adapters.store.sqlite import SQLiteSignatureStore
from rounds.core.fingerprint import Fingerprinter
from rounds.core.investigator import Investigator
from rounds.core.models import ErrorEvent, Severity, StackFrame
from rounds.core.poll_service import PollService
from rounds.core.triage import TriageEngine
from rounds.tests.fakes import FakeDiagnosisPort, FakeNotificationPort, FakeTelemetryPort

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "context_library"
FIXTURE_FILE = FIXTURE_DIR / "pricing.py"


def _known_bug_lineno() -> int:
    """Locate the marked bug line in the fixture so the test stays correct
    if the fixture file is ever edited, instead of hardcoding a line number."""
    for lineno, line in enumerate(FIXTURE_FILE.read_text().splitlines(), start=1):
        if "BUG:" in line:
            return lineno
    raise AssertionError(f"No '# BUG:' marker found in {FIXTURE_FILE}")


class FakeLocalAgentClient:
    """Stands in for the SSH transport, simulating a local agent node.

    Rather than returning a canned response, it reads the actual source
    file at the given workspace path — the same thing a real `claude`
    CLI invocation would do when asked to diagnose an error — so the
    diagnosis it produces is grounded in genuine source content.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def invoke(self, mcp_key: str, workspace: str, prompt: str) -> dict[str, Any]:
        self.calls.append((mcp_key, workspace, prompt))

        # Stack frames are rendered as "module.function (filename:lineno)"
        # (see AgentNodeDiagnosisAdapter._build_diagnosis_prompt).
        match = re.search(r"\((\S+\.py):(\d+)\)", prompt)
        assert match, f"Prompt did not reference a source file:line: {prompt}"
        filename, lineno = match.group(1), int(match.group(2))

        source_path = Path(workspace) / filename
        buggy_line = source_path.read_text().splitlines()[lineno - 1].strip()

        return {
            "summary": "Division by zero when computing the discounted total.",
            "root_cause": (
                f"{filename}:{lineno} — `{buggy_line}` divides by `len(prices)` "
                "without checking for an empty list, raising ZeroDivisionError "
                "when calculate_discounted_total is called with no prices."
            ),
            "evidence": [
                f"Source at {filename}:{lineno}: {buggy_line}",
                "Stack trace shows the failure inside calculate_discounted_total",
            ],
            "suggested_fix": "Return 0.0 early when prices is empty.",
            "confidence": "HIGH",
        }


def _known_error() -> ErrorEvent:
    """A ZeroDivisionError matching the known bug in the context-library fixture."""
    return ErrorEvent(
        trace_id="trace-context-library-001",
        span_id="span-001",
        service="context-library",
        error_type="ZeroDivisionError",
        error_message="division by zero",
        stack_frames=(
            StackFrame(
                module="pricing",
                function="calculate_discounted_total",
                filename="pricing.py",
                lineno=_known_bug_lineno(),
            ),
        ),
        timestamp=datetime.now(UTC),
        attributes={},
        severity=Severity.ERROR,
    )


@pytest.mark.asyncio
async def test_known_error_diagnosed_end_to_end_via_local_agent(tmp_path: Path) -> None:
    """Trigger a known error from context-library and verify rounds diagnoses it
    end-to-end via the local agent node, with a source-level root cause."""
    error = _known_error()
    bug_lineno = error.stack_frames[0].lineno
    assert bug_lineno is not None

    fingerprinter = Fingerprinter()
    fingerprint = fingerprinter.fingerprint(error)

    telemetry = FakeTelemetryPort()
    telemetry.add_error(error)
    telemetry.add_signature_events(fingerprint, [error])

    store = SQLiteSignatureStore(str(tmp_path / "signatures.db"))
    triage = TriageEngine(min_occurrence_for_investigation=1)

    local_agent_client = FakeLocalAgentClient()
    diagnosis_adapter = AgentNodeDiagnosisAdapter(
        service_map={
            "context-library": ServiceMapping(mcp_key="local-agent", workspace=str(FIXTURE_DIR)),
        },
        client=local_agent_client,
        fallback=FakeDiagnosisPort(),
        budget_usd=5.0,
    )
    notification = FakeNotificationPort()
    investigator = Investigator(
        telemetry, store, diagnosis_adapter, notification, triage, codebase_path="/unused"
    )
    poll = PollService(telemetry, store, fingerprinter, triage, investigator, lookback_minutes=60)

    try:
        poll_result = await poll.execute_poll_cycle()
        assert poll_result.new_signatures == 1
        assert poll_result.investigations_queued == 1

        investigation_result = await poll.execute_investigation_cycle()
    finally:
        await store.close_pool()

    assert investigation_result.investigations_failed == 0
    assert len(investigation_result.diagnoses_produced) == 1
    diagnosis = investigation_result.diagnoses_produced[0]

    # The local agent was invoked for the mapped service, against the
    # context-library workspace — not the fallback diagnosis engine.
    assert len(local_agent_client.calls) == 1
    mcp_key, workspace, prompt = local_agent_client.calls[0]
    assert mcp_key == "local-agent"
    assert workspace == str(FIXTURE_DIR)
    assert "ZeroDivisionError" in prompt

    # Source-level root cause: it must cite the exact file:line of the
    # known bug, not just a generic description of the error type.
    assert f"pricing.py:{bug_lineno}" in diagnosis.root_cause
    assert "len(prices)" in diagnosis.root_cause
    assert diagnosis.confidence == "high"

    # High-confidence diagnoses are always reported.
    assert len(notification.reported_diagnoses) == 1
