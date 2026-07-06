"""Agent node diagnosis adapter.

Implements DiagnosisPort as a composite router: diagnosis requests for
services present in the service map are delegated to the agent node that
owns that service's source code (via AgentNodeClient); requests for
unmapped services fall back to another DiagnosisPort implementation
(typically OpenAIDiagnosisAdapter).
"""

import logging
from dataclasses import dataclass
from typing import Any

from rounds.adapters.diagnosis._client import AgentNodeClient
from rounds.adapters.diagnosis._parsing import parse_diagnosis_result
from rounds.core.models import (
    Diagnosis,
    InvestigationContext,
    LogEntry,
    TraceInvestigation,
    TraceTree,
)
from rounds.core.ports import DiagnosisPort, UsageQueryPort

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceMapping:
    """Identifies the agent node and workspace that owns a service's source code."""

    mcp_key: str
    workspace: str


class AgentNodeDiagnosisAdapter(DiagnosisPort):
    """Routes diagnosis to the agent node owning a service's source, or falls back.

    Diagnosis is scoped to ERROR-level signatures; WARN-level signatures are
    not expected to reach this adapter.
    """

    def __init__(
        self,
        service_map: dict[str, ServiceMapping],
        client: AgentNodeClient,
        fallback: DiagnosisPort,
        usage_query: UsageQueryPort | None = None,
        budget_usd: float = 2.0,
    ):
        """Initialize the agent node diagnosis adapter.

        Args:
            service_map: Maps telemetry service name to the agent node and
                workspace that owns its source code.
            client: Transport used to invoke diagnosis on a remote agent node.
            fallback: DiagnosisPort used for services absent from service_map.
            usage_query: Optional port for resolving actual OTLP-based cost.
                Unused until cost resolution is wired up (deferred).
            budget_usd: Budget per diagnosis in USD, used for pre-flight gating.
        """
        self._service_map = service_map
        self._client = client
        self._fallback = fallback
        self._usage_query = usage_query
        self.budget_usd = budget_usd

    async def diagnose(self, context: InvestigationContext) -> Diagnosis:
        """Diagnose via the owning agent node, or delegate to the fallback adapter.

        Raises:
            ValueError: If the estimated cost exceeds budget_usd, or if the
                agent node's response contains no parseable JSON or is missing
                required diagnosis fields.
            TimeoutError: If the agent node invocation times out.
            RuntimeError: If the agent node invocation fails.
        """
        mapping = self._service_map.get(context.signature.service)
        if mapping is None:
            return await self._fallback.diagnose(context)

        try:
            estimated_cost = await self.estimate_cost(context)
            if estimated_cost > self.budget_usd:
                raise ValueError(
                    f"Diagnosis cost ${estimated_cost:.2f} exceeds budget ${self.budget_usd:.2f}"
                )

            prompt = self._build_diagnosis_prompt(context, mapping.workspace)
            raw = await self._client.invoke(mapping.mcp_key, mapping.workspace, prompt)
            return parse_diagnosis_result(raw, model=f"agent-node:{mapping.mcp_key}")
        except (ValueError, TimeoutError, RuntimeError) as e:
            logger.error(
                f"Agent node diagnosis failed for service={context.signature.service!r} "
                f"mcp_key={mapping.mcp_key!r}: {e}",
                exc_info=True,
            )
            raise

    async def estimate_cost(self, context: InvestigationContext) -> float:
        """Heuristic pre-flight budget estimate (same formula pattern as claude_code.py).

        Note: Returns a heuristic estimate, not an OTLP-resolved cost. Actual
        cost resolution via usage_query is deferred to a later phase.
        """
        base_cost = 0.30
        context_size = (
            len(context.recent_events)
            + len(context.trace_data)
            + len(context.related_logs)
        )
        additional_cost = (context_size / 10) * 0.01
        return base_cost + additional_cost

    async def investigate_trace(
        self,
        trace: TraceTree,
        codebase_path: str,
        correlated_logs: tuple[LogEntry, ...] = (),
    ) -> TraceInvestigation:
        """Delegate trace investigation to the fallback adapter.

        Agent-node-delegated trace investigation is deferred to a later phase;
        for now this defers entirely to the fallback DiagnosisPort.
        """
        return await self._fallback.investigate_trace(trace, codebase_path, correlated_logs)

    def _build_diagnosis_prompt(self, context: InvestigationContext, workspace: str) -> str:
        """Build a diagnosis prompt for the agent node.

        Follows the structure of ClaudeCodeDiagnosisAdapter's investigation
        prompt, but omits any `--add-dir` instruction since the agent node's
        source is already mounted at `workspace`.
        """
        sig = context.signature
        prompt = f"""You are an expert software engineer diagnosing a recurring production failure.
Workspace: {workspace}

Use your file reading tools (Read, Glob, Grep) to examine relevant source files \
in the workspace based on the service names and operation names in the trace below. \
Read the actual code that is failing before drawing conclusions.

---

## Failure Signature
Error Type: {sig.error_type}
Service: {sig.service}
Message: {sig.message_template}
Occurrences: {sig.occurrence_count} (first: {sig.first_seen}, last: {sig.last_seen})

## Recent Error Events ({len(context.recent_events)} total)
"""
        for i, event in enumerate(context.recent_events[:5], 1):
            prompt += f"\n### Event {i} — {event.timestamp}\n"
            prompt += f"Service: {event.service}\n"
            prompt += f"Error: {event.error_type}: {event.error_message}\n"
            if event.stack_frames:
                prompt += "Stack:\n"
                for frame in event.stack_frames[:10]:
                    prompt += (
                        f"  {frame.module}.{frame.function}"
                        f" ({frame.filename}:{frame.lineno})\n"
                    )

        if context.trace_data:
            prompt += f"\n## Distributed Traces ({len(context.trace_data)} traces)\n"
            prompt += (
                "Each trace shows the complete call chain. "
                "Spans marked [ERROR] are where failures occurred.\n"
            )
            for trace in context.trace_data[:3]:
                prompt += f"\n### Trace {trace.trace_id}\n"
                if trace.root_span:
                    prompt += self._format_span_tree(trace.root_span)
                if trace.error_spans:
                    prompt += "\n**Error span details:**\n"
                    for span in trace.error_spans[:5]:
                        prompt += (
                            f"- {span.service}: {span.operation} "
                            f"({span.duration_ms:.1f}ms)\n"
                        )
                        relevant_attrs = {
                            k: v
                            for k, v in span.attributes.items()
                            if any(
                                kw in k.lower()
                                for kw in (
                                    "error", "exception", "message",
                                    "status", "http", "db", "rpc",
                                )
                            )
                        }
                        for k, v in list(relevant_attrs.items())[:8]:
                            prompt += f"  {k}: {v}\n"

        if context.related_logs:
            prompt += f"\n## Correlated Logs ({len(context.related_logs)} entries)\n"
            for log in context.related_logs[:15]:
                prompt += f"[{log.severity.value}] {log.timestamp}  {log.body[:200]}\n"

        if context.historical_context:
            prompt += (
                f"\n## Historical Similar Signatures "
                f"({len(context.historical_context)} patterns)\n"
            )
            for sig_h in context.historical_context[:3]:
                prompt += (
                    f"- {sig_h.error_type} in {sig_h.service} "
                    f"({sig_h.occurrence_count} occurrences)\n"
                )

        prompt += f"""
---

## Investigation Steps

1. Examine the trace to understand the call chain and where the failure originates.
2. Use Glob/Grep to find the source files for the services and operations in the trace.
3. Read the relevant code — especially the function or handler named in the error spans.
4. Identify the root cause from the combination of trace data and source code.

## Response Format

After reading the relevant source files, respond with a JSON object in exactly this format:
{{
  "summary": "One paragraph overview of the diagnosis findings — what is failing, why, and how severe it is",
  "root_cause": "The precise root cause, citing specific code locations and trace evidence",
  "evidence": [
    "evidence point citing a specific file:line or span attribute",
    "evidence point 2",
    "evidence point 3"
  ],
  "suggested_fix": "Concrete actionable fix with file paths and code changes if applicable",
  "confidence": "HIGH|MEDIUM|LOW"
}}

Workspace is at: {workspace}
"""
        return prompt

    def _format_span_tree(self, node: "Any", depth: int = 0) -> str:
        """Recursively format a SpanNode tree into readable text.

        Args:
            node: SpanNode to format.
            depth: Current indentation depth.

        Returns:
            Multi-line string showing the span hierarchy.
        """
        indent = "  " * depth
        status = " [ERROR]" if node.status == "error" else ""
        line = (
            f"{indent}• {node.service}: {node.operation}"
            f" ({node.duration_ms:.1f}ms){status}\n"
        )
        for child in node.children:
            line += self._format_span_tree(child, depth + 1)
        return line
