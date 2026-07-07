"""Unit tests for AgentNodeDiagnosisAdapter."""

from datetime import UTC, datetime
from typing import Any

import pytest

from rounds.adapters.diagnosis.agent_node import AgentNodeDiagnosisAdapter, ServiceMapping
from rounds.core.models import (
    ErrorEvent,
    InvestigationContext,
    LogEntry,
    Severity,
    Signature,
    SignatureStatus,
    SpanNode,
    StackFrame,
    TraceInvestigation,
    TraceTree,
)
from rounds.core.ports import DiagnosisPort
from rounds.tests.fakes.diagnosis import FakeDiagnosisPort
from rounds.tests.fakes.usage import FakeUsageQueryPort


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


class NotImplementedFallback(DiagnosisPort):
    """Fallback DiagnosisPort whose investigate_trace() always raises
    NotImplementedError, mirroring OpenAIDiagnosisAdapter's real behavior."""

    async def diagnose(self, context: InvestigationContext) -> Any:
        raise NotImplementedError

    async def estimate_cost(self, context: InvestigationContext) -> float:
        return 0.0

    async def investigate_trace(
        self,
        trace: TraceTree,
        codebase_path: str,
        correlated_logs: tuple[LogEntry, ...] = (),
    ) -> TraceInvestigation:
        raise NotImplementedError("OpenAI adapter does not support trace investigation.")


def _trace(trace_id: str = "trace-xyz", service: str = "mapped-service") -> TraceTree:
    return TraceTree(
        trace_id=trace_id,
        root_span=SpanNode(
            span_id="span-1",
            parent_id=None,
            service=service,
            operation="handle_request",
            duration_ms=10.0,
            status="error",
            attributes={},
            events=(),
        ),
        error_spans=(),
    )


def _investigation_context(
    service: str = "mapped-service", severity: Severity = Severity.ERROR
) -> InvestigationContext:
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
        severity=severity,
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
            "suggested_resolution_hours": 24,
        }

        context = _investigation_context()
        diagnosis = await adapter.diagnose(context)

        assert "db/pool.py:23" in diagnosis.root_cause
        assert diagnosis.confidence == "high"
        assert diagnosis.cost_usd == 0.0
        assert diagnosis.model == "agent-node:node1"
        assert diagnosis.suggested_resolution_hours == 24
        assert len(client.calls) == 1
        assert len(fallback.diagnose_calls) == 0

        mcp_key, workspace, prompt = client.calls[0]
        assert mcp_key == "node1"
        assert workspace == "/workspace/target"
        assert "Workspace: /workspace/target" in prompt
        assert "--add-dir" not in prompt

    @pytest.mark.asyncio
    async def test_client_error_propagates_with_logging(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
    ) -> None:
        """A ValueError raised by the client itself (e.g. no parseable JSON found)
        propagates through the adapter unchanged."""
        client.error = ValueError("Agent node 'worker-host' returned no parseable JSON.")

        with pytest.raises(ValueError, match="no parseable JSON"):
            await adapter.diagnose(_investigation_context())

    @pytest.mark.asyncio
    async def test_client_timeout_error_propagates(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
    ) -> None:
        client.error = TimeoutError("Agent node invocation timed out after 120 seconds")

        with pytest.raises(TimeoutError, match="timed out"):
            await adapter.diagnose(_investigation_context())

    @pytest.mark.asyncio
    async def test_client_runtime_error_propagates(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
    ) -> None:
        client.error = RuntimeError("Agent node process exited with non-zero status")

        with pytest.raises(RuntimeError, match="non-zero status"):
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

    @pytest.mark.asyncio
    async def test_response_with_wrong_evidence_type_raises_value_error(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
    ) -> None:
        """A response that is valid JSON but has the wrong shape (evidence as a
        string instead of a list) is rejected by parse_diagnosis_result rather
        than crashing the pipeline."""
        client.response = {
            "summary": "bad shape",
            "root_cause": "something failed",
            "evidence": "not-a-list",
            "suggested_fix": "fix it",
            "confidence": "LOW",
        }

        with pytest.raises(ValueError, match="'evidence' must be a list"):
            await adapter.diagnose(_investigation_context())

    @pytest.mark.asyncio
    async def test_estimated_cost_exceeding_budget_raises_before_invoking_client(
        self,
        service_map: dict[str, ServiceMapping],
        client: FakeAgentNodeClient,
        fallback: FakeDiagnosisPort,
    ) -> None:
        adapter = AgentNodeDiagnosisAdapter(
            service_map=service_map,
            client=client,
            fallback=fallback,
            usage_query=None,
            budget_usd=0.01,
        )

        with pytest.raises(ValueError, match="exceeds budget"):
            await adapter.diagnose(_investigation_context())

        assert len(client.calls) == 0

    @pytest.mark.asyncio
    async def test_unexpected_exception_is_logged_and_propagates(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
    ) -> None:
        """An exception type not in (ValueError, TimeoutError, RuntimeError) —
        e.g. a TypeError from malformed LLM data or a buggy client — should
        still be logged with service/mcp_key context before propagating."""
        client.error = TypeError("unsupported operand type(s)")

        with pytest.raises(TypeError, match="unsupported operand type"):
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


class TestBudgetUsd:
    """Tests that budget_usd is a read-only property, not a mutable attribute."""

    def test_budget_usd_reflects_constructor_value(
        self, adapter: AgentNodeDiagnosisAdapter
    ) -> None:
        assert adapter.budget_usd == 2.0

    def test_budget_usd_cannot_be_assigned(self, adapter: AgentNodeDiagnosisAdapter) -> None:
        with pytest.raises(AttributeError):
            adapter.budget_usd = 100.0  # type: ignore[misc]


class TestSeverityScoping:
    """Tests for BA Req 9's ERROR-only scoping (diagnose() rejects sub-ERROR signatures)."""

    @pytest.mark.asyncio
    async def test_warn_severity_events_raise_value_error(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
    ) -> None:
        context = _investigation_context(severity=Severity.WARN)

        with pytest.raises(ValueError, match="ERROR-level diagnosis"):
            await adapter.diagnose(context)

        assert len(client.calls) == 0

    @pytest.mark.asyncio
    async def test_fatal_severity_events_are_accepted(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
    ) -> None:
        client.response = {
            "summary": "Out of memory",
            "root_cause": "Unbounded cache growth",
            "evidence": ["evidence 1"],
            "suggested_fix": "Add cache eviction",
            "confidence": "HIGH",
        }
        context = _investigation_context(severity=Severity.FATAL)

        diagnosis = await adapter.diagnose(context)

        assert diagnosis.root_cause == "Unbounded cache growth"
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_no_recent_events_are_accepted(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
    ) -> None:
        """Severity cannot be determined without events, so diagnosis proceeds."""
        client.response = {
            "summary": "Connection pool exhausted",
            "root_cause": "Pool size hardcoded too low",
            "evidence": ["evidence 1"],
            "suggested_fix": "Increase pool size",
            "confidence": "HIGH",
        }
        context = _investigation_context()
        context = InvestigationContext(
            signature=context.signature,
            recent_events=(),
            trace_data=context.trace_data,
            related_logs=context.related_logs,
            codebase_path=context.codebase_path,
            historical_context=context.historical_context,
        )

        diagnosis = await adapter.diagnose(context)

        assert diagnosis.root_cause == "Pool size hardcoded too low"


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
    """Tests for investigate_trace() routing by the trace's root service."""

    @pytest.mark.asyncio
    async def test_mapped_service_invokes_agent_node_client(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
        fallback: FakeDiagnosisPort,
    ) -> None:
        client.response = {
            "summary": "Handles an incoming API request end-to-end",
            "code_flow": [
                "Step 1: mapped-service handler.py:42 — receives request",
            ],
            "services_involved": ["mapped-service"],
            "key_findings": ["No notable issues found"],
        }

        trace = _trace()
        result = await adapter.investigate_trace(trace, codebase_path="/workspace/target")

        assert result.trace_id == "trace-xyz"
        assert result.model == "agent-node:node1"
        assert result.summary == "Handles an incoming API request end-to-end"
        assert result.cost_usd == 0.0
        assert len(client.calls) == 1

        mcp_key, workspace, prompt = client.calls[0]
        assert mcp_key == "node1"
        assert workspace == "/workspace/target"
        assert "Workspace: /workspace/target" in prompt
        assert "Trace ID: trace-xyz" in prompt

    @pytest.mark.asyncio
    async def test_unmapped_service_delegates_to_fallback(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
    ) -> None:
        trace = _trace(service="unmapped-service")

        result = await adapter.investigate_trace(trace, codebase_path="/workspace/target")

        assert len(client.calls) == 0
        assert result.model == "fake-model"

    @pytest.mark.asyncio
    async def test_unmapped_service_fallback_not_implemented_raises(
        self,
        service_map: dict[str, ServiceMapping],
        client: FakeAgentNodeClient,
    ) -> None:
        adapter = AgentNodeDiagnosisAdapter(
            service_map=service_map,
            client=client,
            fallback=NotImplementedFallback(),
            usage_query=None,
            budget_usd=2.0,
        )
        trace = _trace(service="unmapped-service")

        with pytest.raises(ValueError, match="unmapped-service"):
            await adapter.investigate_trace(trace, codebase_path="/workspace/target")

    @pytest.mark.asyncio
    async def test_estimated_cost_exceeding_budget_raises_before_invoking_client(
        self,
        service_map: dict[str, ServiceMapping],
        client: FakeAgentNodeClient,
        fallback: FakeDiagnosisPort,
    ) -> None:
        adapter = AgentNodeDiagnosisAdapter(
            service_map=service_map,
            client=client,
            fallback=fallback,
            usage_query=None,
            budget_usd=0.01,
        )
        trace = _trace()

        with pytest.raises(ValueError, match="exceeds budget"):
            await adapter.investigate_trace(trace, codebase_path="/workspace/target")

        assert len(client.calls) == 0

    @pytest.mark.asyncio
    async def test_unexpected_exception_is_logged_and_propagates(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
    ) -> None:
        """Exception types outside (ValueError, TimeoutError, RuntimeError) —
        e.g. an AttributeError from a buggy UsageQueryPort — are still logged
        with trace_id/mcp_key context before propagating."""
        client.error = AttributeError("'NoneType' object has no attribute 'get'")
        trace = _trace()

        with pytest.raises(AttributeError, match="has no attribute"):
            await adapter.investigate_trace(trace, codebase_path="/workspace/target")


class TestCostResolution:
    """Tests for _resolve_cost() wiring into diagnose() and investigate_trace()."""

    @pytest.mark.asyncio
    async def test_diagnose_cost_resolved_via_usage_query(
        self,
        service_map: dict[str, ServiceMapping],
        client: FakeAgentNodeClient,
        fallback: FakeDiagnosisPort,
    ) -> None:
        usage_query = FakeUsageQueryPort()
        usage_query.set_cost("trace-001", 1.23)
        adapter = AgentNodeDiagnosisAdapter(
            service_map=service_map,
            client=client,
            fallback=fallback,
            usage_query=usage_query,
            budget_usd=2.0,
        )
        client.response = {
            "summary": "Connection pool exhausted",
            "root_cause": "Pool size hardcoded too low",
            "evidence": ["evidence 1"],
            "suggested_fix": "Increase pool size",
            "confidence": "HIGH",
        }

        diagnosis = await adapter.diagnose(_investigation_context())

        assert diagnosis.cost_usd == 1.23
        assert usage_query.queries == ["trace-001"]

    @pytest.mark.asyncio
    async def test_diagnose_cost_defaults_to_zero_when_no_usage_data_found(
        self,
        service_map: dict[str, ServiceMapping],
        client: FakeAgentNodeClient,
        fallback: FakeDiagnosisPort,
    ) -> None:
        usage_query = FakeUsageQueryPort()  # no cost configured for any trace
        adapter = AgentNodeDiagnosisAdapter(
            service_map=service_map,
            client=client,
            fallback=fallback,
            usage_query=usage_query,
            budget_usd=2.0,
        )
        client.response = {
            "summary": "Connection pool exhausted",
            "root_cause": "Pool size hardcoded too low",
            "evidence": ["evidence 1"],
            "suggested_fix": "Increase pool size",
            "confidence": "HIGH",
        }

        diagnosis = await adapter.diagnose(_investigation_context())

        assert diagnosis.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_diagnose_cost_query_failure_does_not_block_diagnosis(
        self,
        service_map: dict[str, ServiceMapping],
        client: FakeAgentNodeClient,
        fallback: FakeDiagnosisPort,
    ) -> None:
        usage_query = FakeUsageQueryPort()
        usage_query.should_fail = True
        adapter = AgentNodeDiagnosisAdapter(
            service_map=service_map,
            client=client,
            fallback=fallback,
            usage_query=usage_query,
            budget_usd=2.0,
        )
        client.response = {
            "summary": "Connection pool exhausted",
            "root_cause": "Pool size hardcoded too low",
            "evidence": ["evidence 1"],
            "suggested_fix": "Increase pool size",
            "confidence": "HIGH",
        }

        diagnosis = await adapter.diagnose(_investigation_context())

        assert diagnosis.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_investigate_trace_cost_resolved_via_usage_query(
        self,
        service_map: dict[str, ServiceMapping],
        client: FakeAgentNodeClient,
        fallback: FakeDiagnosisPort,
    ) -> None:
        usage_query = FakeUsageQueryPort()
        usage_query.set_cost("trace-xyz", 0.42)
        adapter = AgentNodeDiagnosisAdapter(
            service_map=service_map,
            client=client,
            fallback=fallback,
            usage_query=usage_query,
            budget_usd=2.0,
        )
        client.response = {
            "summary": "Handles an incoming API request end-to-end",
            "code_flow": ["Step 1: mapped-service — receives request"],
            "services_involved": ["mapped-service"],
            "key_findings": [],
        }

        result = await adapter.investigate_trace(_trace(), codebase_path="/workspace/target")

        assert result.cost_usd == 0.42
        assert usage_query.queries == ["trace-xyz"]

    @pytest.mark.asyncio
    async def test_investigate_trace_cost_defaults_to_zero_when_usage_query_none(
        self,
        adapter: AgentNodeDiagnosisAdapter,
        client: FakeAgentNodeClient,
    ) -> None:
        client.response = {
            "summary": "Handles an incoming API request end-to-end",
            "code_flow": ["Step 1: mapped-service — receives request"],
            "services_involved": ["mapped-service"],
            "key_findings": [],
        }

        result = await adapter.investigate_trace(_trace(), codebase_path="/workspace/target")

        assert result.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_diagnose_cost_query_bug_is_not_swallowed(
        self,
        service_map: dict[str, ServiceMapping],
        client: FakeAgentNodeClient,
        fallback: FakeDiagnosisPort,
    ) -> None:
        """A RuntimeError from usage_query (e.g. a programming bug in the
        UsageQueryPort implementation) must propagate rather than being
        silently converted to a $0.00 cost."""
        usage_query = FakeUsageQueryPort()
        usage_query.should_raise_bug = True
        adapter = AgentNodeDiagnosisAdapter(
            service_map=service_map,
            client=client,
            fallback=fallback,
            usage_query=usage_query,
            budget_usd=2.0,
        )
        client.response = {
            "summary": "Connection pool exhausted",
            "root_cause": "Pool size hardcoded too low",
            "evidence": ["evidence 1"],
            "suggested_fix": "Increase pool size",
            "confidence": "HIGH",
        }

        with pytest.raises(RuntimeError, match="Programming bug"):
            await adapter.diagnose(_investigation_context())
