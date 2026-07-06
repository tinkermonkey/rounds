"""Unit tests for AgentNodeDiagnosisAdapter."""

from datetime import UTC, datetime
from typing import Any

import pytest

from rounds.adapters.diagnosis.agent_node import AgentNodeDiagnosisAdapter, ServiceMapping
from rounds.core.models import (
    ErrorEvent,
    InvestigationContext,
    Severity,
    Signature,
    SignatureStatus,
    StackFrame,
)
from rounds.tests.fakes.diagnosis import FakeDiagnosisPort


class FakeAgentNodeClient:
    """Fake AgentNodeClient for testing, configurable to return or raise."""

    def __init__(self) -> None:
        self.response: dict[str, Any] | None = None
        self.error: Exception | None = None
        self.calls: list[tuple[str, str, str]] = []

    async def invoke(self, mcp_key: str, workspace: str, prompt: str) -> dict[str, Any]:
        self.calls.append((mcp_key, workspace, prompt))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _investigation_context(service: str = "mapped-service") -> InvestigationContext:
    error_event = ErrorEvent(
        trace_id="trace-001",
        span_id="span-001",
        service=service,
        error_type="TimeoutError",
        error_message="Request timed out after 30 seconds",
        stack_frames=(
            StackFrame(
                module="api.handler",
                function="process_request",
                filename="handler.py",
                lineno=42,
            ),
        ),
        timestamp=datetime.now(UTC),
        attributes={},
        severity=Severity.ERROR,
    )

    signature = Signature(
        id="sig-001",
        fingerprint="fp-001",
        error_type="TimeoutError",
        service=service,
        message_template="Request timed out",
        stack_hash="stack-001",
        first_seen=datetime.now(UTC),
        last_seen=datetime.now(UTC),
        occurrence_count=5,
        status=SignatureStatus.NEW,
    )

    return InvestigationContext(
        signature=signature,
        recent_events=(error_event,),
        trace_data=(),
        related_logs=(),
        codebase_path=".",
        historical_context=(),
    )


@pytest.fixture
def service_map() -> dict[str, ServiceMapping]:
    return {"mapped-service": ServiceMapping(mcp_key="node1", workspace="/workspace/target")}


@pytest.fixture
def client() -> FakeAgentNodeClient:
    return FakeAgentNodeClient()


@pytest.fixture
def fallback() -> FakeDiagnosisPort:
    return FakeDiagnosisPort()


@pytest.fixture
def adapter(
    service_map: dict[str, ServiceMapping],
    client: FakeAgentNodeClient,
    fallback: FakeDiagnosisPort,
) -> AgentNodeDiagnosisAdapter:
    return AgentNodeDiagnosisAdapter(
        service_map=service_map,
        client=client,
        fallback=fallback,
        usage_query=None,
        budget_usd=2.0,
    )


class TestDiagnoseMappedService:
    """Tests for diagnose() when the service is present in the service map."""

    @pytest.mark.asyncio
    async def test_diagnose_invokes_agent_node_client_and_returns_diagnosis(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
        fallback: FakeDiagnosisPort,
    ) -> None:
        client.response = {
            "summary": "Connection pool exhausted under load",
            "root_cause": "Pool size is hardcoded to 5 in db/pool.py:23, "
            "insufficient for concurrent request volume",
            "evidence": [
                "db/pool.py:23 — pool max_size=5",
                "Trace shows 12 concurrent requests waiting on pool.acquire()",
            ],
            "suggested_fix": "Increase pool max_size in db/pool.py:23 or add backpressure",
            "confidence": "HIGH",
        }

        context = _investigation_context()
        diagnosis = await adapter.diagnose(context)

        assert "db/pool.py:23" in diagnosis.root_cause
        assert diagnosis.confidence == "high"
        assert diagnosis.cost_usd == 0.0
        assert diagnosis.model == "agent-node:node1"
        assert len(client.calls) == 1
        assert len(fallback.diagnose_calls) == 0

        mcp_key, workspace, prompt = client.calls[0]
        assert mcp_key == "node1"
        assert workspace == "/workspace/target"
        assert "Workspace: /workspace/target" in prompt
        assert "--add-dir" not in prompt

    @pytest.mark.asyncio
    async def test_malformed_json_response_raises_without_crashing_pipeline(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
    ) -> None:
        client.error = ValueError("Agent node 'worker-host' returned no parseable JSON.")

        with pytest.raises(ValueError, match="no parseable JSON"):
            await adapter.diagnose(_investigation_context())

    @pytest.mark.asyncio
    async def test_response_missing_required_field_raises_value_error(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
    ) -> None:
        client.response = {"summary": "incomplete response"}

        with pytest.raises(ValueError, match="root_cause"):
            await adapter.diagnose(_investigation_context())


class TestDiagnoseFallback:
    """Tests for diagnose() when the service is absent from the service map."""

    @pytest.mark.asyncio
    async def test_unmapped_service_delegates_to_fallback(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
        fallback: FakeDiagnosisPort,
    ) -> None:
        context = _investigation_context(service="unmapped-service")

        diagnosis = await adapter.diagnose(context)

        assert len(client.calls) == 0
        assert len(fallback.diagnose_calls) == 1
        assert diagnosis.model == "fake-model"


class TestEstimateCost:
    """Tests for estimate_cost()."""

    @pytest.mark.asyncio
    async def test_estimate_cost_returns_heuristic_value(
        self, adapter: AgentNodeDiagnosisAdapter
    ) -> None:
        context = _investigation_context()

        cost = await adapter.estimate_cost(context)

        assert cost == pytest.approx(0.30 + (1 / 10) * 0.01)

    @pytest.mark.asyncio
    async def test_estimate_cost_scales_with_context_size(
        self, adapter: AgentNodeDiagnosisAdapter
    ) -> None:
        base_context = _investigation_context()
        larger_context = InvestigationContext(
            signature=base_context.signature,
            recent_events=base_context.recent_events * 20,
            trace_data=base_context.trace_data,
            related_logs=base_context.related_logs,
            codebase_path=base_context.codebase_path,
            historical_context=base_context.historical_context,
        )

        base_cost = await adapter.estimate_cost(base_context)
        larger_cost = await adapter.estimate_cost(larger_context)

        assert larger_cost > base_cost


class TestInvestigateTrace:
    """Tests for investigate_trace() delegation to the fallback adapter."""

    @pytest.mark.asyncio
    async def test_investigate_trace_delegates_to_fallback(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        fallback: FakeDiagnosisPort,
    ) -> None:
        from rounds.core.models import SpanNode, TraceTree

        trace = TraceTree(
            trace_id="trace-xyz",
            root_span=SpanNode(
                span_id="span-1",
                parent_id=None,
                service="mapped-service",
                operation="handle_request",
                duration_ms=10.0,
                status="error",
                attributes={},
                events=(),
            ),
            error_spans=(),
        )

        result = await adapter.investigate_trace(trace, codebase_path="/workspace/target")

        assert result.trace_id == "trace-xyz"
        assert result.model == "fake-model"
