"""Claude Code diagnosis adapter.

Implements DiagnosisPort by invoking Claude Code CLI in headless mode
for LLM-powered code analysis and root cause diagnosis.

Invocation format: claude -p <prompt> --output-format json
"""

import asyncio
import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from typing import Any

from rounds.core.models import (
    Diagnosis,
    InvestigationContext,
    LogEntry,
    TraceInvestigation,
    TraceTree,
)
from rounds.core.ports import DiagnosisPort

logger = logging.getLogger(__name__)


class ClaudeCodeDiagnosisAdapter(DiagnosisPort):
    """Claude Code CLI-based diagnosis adapter."""

    def __init__(
        self,
        model: str = "claude-opus",
        budget_usd: float = 2.0,
        api_key: str = "",
        oauth_token: str = "",
    ):
        """Initialize Claude Code diagnosis adapter.

        Args:
            model: Claude model to use (e.g., 'claude-opus', 'claude-sonnet')
            budget_usd: Budget per diagnosis in USD
            api_key: Anthropic API key (sk-ant-...). Passed as ANTHROPIC_API_KEY to the CLI.
            oauth_token: Claude Code OAuth token for Claude.ai subscription access.
                Passed as CLAUDE_CODE_OAUTH_TOKEN to the CLI. Takes precedence over api_key
                when both are provided.
        """
        self.model = model
        self.budget_usd = budget_usd
        self.api_key = api_key
        self.oauth_token = oauth_token

    async def diagnose(
        self, context: InvestigationContext
    ) -> Diagnosis:
        """Invoke Claude Code CLI for LLM analysis on investigation context."""
        try:
            # Estimate cost first
            estimated_cost = await self.estimate_cost(context)

            if estimated_cost > self.budget_usd:
                raise ValueError(
                    f"Diagnosis cost ${estimated_cost:.2f} exceeds budget ${self.budget_usd:.2f}"
                )

            # Build the investigation prompt
            prompt = self._build_investigation_prompt(context)

            # Invoke Claude Code with codebase path for file access
            result = await self._invoke_claude_code(
                prompt, codebase_path=context.codebase_path
            )

            # Parse result
            diagnosis = self._parse_diagnosis_result(result, context)

            # Track actual cost (approximation based on tokens)
            diagnosis = Diagnosis(
                root_cause=diagnosis.root_cause,
                evidence=diagnosis.evidence,
                suggested_fix=diagnosis.suggested_fix,
                confidence=diagnosis.confidence,
                diagnosed_at=datetime.now(UTC),
                model=self.model,
                cost_usd=estimated_cost,
            )

            return diagnosis

        except (ValueError, TimeoutError, RuntimeError) as e:
            logger.error(f"Failed to diagnose: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error during diagnosis: {e}", exc_info=True)
            raise

    async def estimate_cost(self, context: InvestigationContext) -> float:
        """Estimate the cost (in USD) of diagnosing a signature.

        Simple heuristic: estimate based on context size.
        - Base cost: $0.30 per diagnosis
        - Additional cost based on context size (events, logs, code)

        Note: Returns the true estimated cost, not capped at budget.
        The caller is responsible for budget enforcement.
        """
        # Base cost $0.30: derived from Claude API pricing (~$3 per 1M input tokens,
        # estimated ~100K tokens for baseline diagnosis prompt at current model)
        base_cost = 0.30

        # Add cost for context size
        context_size = (
            len(context.recent_events)
            + len(context.trace_data)
            + len(context.related_logs)
        )

        # Additional cost calculation: $0.01 per 10 context items
        # Rationale: Each context item (event/trace/log) adds ~1K tokens to the prompt,
        # 10 items = ~10K tokens = ~$0.01 at current Claude pricing
        additional_cost = (context_size / 10) * 0.01

        total_cost = base_cost + additional_cost

        return total_cost

    def _build_investigation_prompt(self, context: InvestigationContext) -> str:
        """Build a comprehensive investigation prompt for Claude Code.

        Instructs Claude to read source files from the mounted codebase using
        its file-access tools, cross-referencing the full distributed trace
        (service names, operation names, error spans) with actual code.

        Args:
            context: InvestigationContext containing signature, events, traces,
                    logs, codebase path, and historical signatures.

        Returns:
            Formatted markdown prompt for Claude Code invocation.
        """
        sig = context.signature
        prompt = f"""You are an expert software engineer diagnosing a recurring production failure.
You have file-system access to the codebase at: {context.codebase_path}

Use your file reading tools (Read, Glob, Grep) to examine relevant source files \
based on the service names and operation names in the trace below. \
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

        # Full distributed trace trees
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

        # Correlated logs
        if context.related_logs:
            prompt += f"\n## Correlated Logs ({len(context.related_logs)} entries)\n"
            for log in context.related_logs[:15]:
                prompt += (
                    f"[{log.severity.value}] {log.timestamp}"
                    f"  {log.body[:200]}\n"
                )

        # Historical patterns
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
  "root_cause": "The precise root cause, citing specific code locations and trace evidence",
  "evidence": [
    "evidence point citing a specific file:line or span attribute",
    "evidence point 2",
    "evidence point 3"
  ],
  "suggested_fix": "Concrete actionable fix with file paths and code changes if applicable",
  "confidence": "HIGH|MEDIUM|LOW"
}}

Codebase is at: {context.codebase_path}
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

    async def _invoke_claude_code(
        self, prompt: str, codebase_path: str = ""
    ) -> dict[str, Any]:
        """Invoke Claude Code CLI with the investigation prompt asynchronously.

        Sets the working directory to codebase_path so Claude Code's file-reading
        tools can access the mounted project filesystem. Also passes --add-dir so
        Claude Code explicitly trusts the directory even if cwd differs at startup.

        Raises:
            ValueError: If JSON parsing fails or no valid JSON found in output.
            TimeoutError: If CLI invocation times out.
            RuntimeError: If CLI returns non-zero exit code.
        """
        try:
            # Build subprocess environment with exactly one auth method set.
            # OAuth token takes precedence when both are configured; the other key
            # is removed to prevent the CLI from picking up conflicting credentials.
            env = os.environ.copy()
            if self.oauth_token:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = self.oauth_token
                env.pop("ANTHROPIC_API_KEY", None)
            elif self.api_key:
                env["ANTHROPIC_API_KEY"] = self.api_key
                env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

            # Resolve codebase path; fall back to None if not set or not a directory
            resolved_path: str | None = None
            if codebase_path:
                expanded = os.path.abspath(os.path.expanduser(codebase_path))
                if os.path.isdir(expanded):
                    resolved_path = expanded

            # Build CLI command. --add-dir grants Claude Code file-read access to
            # the codebase directory even when the process was started elsewhere.
            cmd = ["claude", "-p", prompt, "--output-format", "json"]
            if resolved_path:
                cmd += ["--add-dir", resolved_path]

            # Invoke Claude Code CLI in an executor to avoid blocking the event loop
            def _run_claude_code() -> str:
                """Synchronous wrapper for subprocess call."""
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=120,
                        env=env,
                        cwd=resolved_path,
                    )

                    if result.returncode != 0:
                        error_output = result.stderr or result.stdout
                        raise RuntimeError(f"Claude Code CLI failed: {error_output}")

                    return result.stdout.strip()

                except subprocess.TimeoutExpired:
                    raise TimeoutError("Claude Code CLI timed out after 120 seconds")

            # Run in executor to avoid blocking
            output = await asyncio.to_thread(_run_claude_code)

            # Claude Code --output-format json wraps the response in an envelope:
            # {"type": "result", "subtype": "success", "result": "<text>", ...}
            # Unwrap that first; then find the diagnosis JSON inside the inner text.
            text_to_search = output
            try:
                outer: dict[str, Any] = json.loads(output)
                if isinstance(outer, dict) and "type" in outer and "result" in outer:
                    if outer.get("is_error"):
                        raise RuntimeError(
                            f"Claude Code returned error: {outer.get('result', 'unknown')}"
                        )
                    # The inner text is what Claude actually wrote
                    text_to_search = outer.get("result", "") or output
            except json.JSONDecodeError:
                pass  # raw output, search as-is

            # If the inner text is itself valid JSON dict, return it directly.
            # Field validation is the caller's responsibility (_parse_diagnosis_result
            # or _parse_trace_investigation_result), so we accept any non-empty dict.
            try:
                parsed: dict[str, Any] = json.loads(text_to_search)
                if isinstance(parsed, dict) and parsed:
                    return parsed
            except json.JSONDecodeError:
                pass

            # Find JSON block by brace-counting (handles prose + embedded JSON)
            lines = text_to_search.split("\n")
            json_buffer: list[str] = []
            in_json = False
            brace_count = 0

            for line in lines:
                stripped = line.strip()
                if stripped.startswith("{"):
                    in_json = True
                    brace_count = 0

                if in_json:
                    json_buffer.append(line)
                    brace_count += line.count("{") - line.count("}")

                    if brace_count == 0:
                        json_str = "\n".join(json_buffer)
                        try:
                            parsed = json.loads(json_str)
                            if isinstance(parsed, dict) and parsed:
                                return parsed
                        except json.JSONDecodeError as e:
                            logger.warning(
                                f"Failed to parse JSON block from Claude output: {e}. "
                                f"Content: {json_str[:200]}",
                                exc_info=True,
                            )
                        # Reset and keep searching
                        json_buffer = []
                        in_json = False

            raise ValueError(
                f"Claude Code output contained no parseable JSON dict. "
                f"Output: {text_to_search[:500]}"
            )

        except TimeoutError as e:
            logger.error(f"Claude Code CLI timeout: {e}", exc_info=True)
            raise TimeoutError(str(e)) from e
        except RuntimeError as e:
            logger.error(f"Claude Code CLI error: {e}", exc_info=True)
            raise RuntimeError(str(e)) from e
        except ValueError as e:
            logger.error(f"Failed to parse Claude Code response: {e}", exc_info=True)
            raise ValueError(str(e)) from e
        except Exception as e:
            logger.error(f"Failed to invoke Claude Code: {e}", exc_info=True)
            raise

    async def investigate_trace(
        self,
        trace: TraceTree,
        codebase_path: str,
        correlated_logs: tuple[LogEntry, ...] = (),
    ) -> TraceInvestigation:
        """Explain the code flow for a distributed trace by reading the codebase.

        Raises:
            ValueError: If JSON parsing fails.
            TimeoutError: If CLI invocation times out.
            RuntimeError: If CLI returns non-zero exit code.
        """
        estimated_cost = self._estimate_trace_cost(trace, correlated_logs)
        if estimated_cost > self.budget_usd:
            raise ValueError(
                f"Investigation cost ${estimated_cost:.2f} exceeds budget ${self.budget_usd:.2f}"
            )

        prompt = self._build_trace_investigation_prompt(trace, correlated_logs, codebase_path)
        result = await self._invoke_claude_code(prompt, codebase_path=codebase_path)
        return self._parse_trace_investigation_result(result, trace.trace_id, estimated_cost)

    def _estimate_trace_cost(
        self, trace: TraceTree, correlated_logs: tuple[LogEntry, ...]
    ) -> float:
        """Estimate cost for a trace investigation (same heuristic as error diagnosis)."""
        base_cost = 0.30
        context_size = len(correlated_logs)
        # Count spans via BFS
        queue = [trace.root_span]
        while queue:
            node = queue.pop()
            context_size += 1
            queue.extend(node.children)
        additional_cost = (context_size / 10) * 0.01
        return base_cost + additional_cost

    def _build_trace_investigation_prompt(
        self,
        trace: TraceTree,
        correlated_logs: tuple[LogEntry, ...],
        codebase_path: str,
    ) -> str:
        """Build a prompt asking Claude to explain the code flow for a trace."""
        prompt = f"""You are an expert software engineer explaining how a distributed request flows through a codebase.
You have file-system access to the codebase at: {codebase_path}

Use your file reading tools (Read, Glob, Grep) to find and read the source files
referenced in the span service names and operation names below.
Read the actual code before writing your explanation.

---

## Trace ID: {trace.trace_id}

## Span Tree
"""
        prompt += self._format_span_tree(trace.root_span)

        if trace.error_spans:
            prompt += "\n## Error Spans\n"
            for span in trace.error_spans:
                prompt += f"- [{span.service}] {span.operation} ({span.duration_ms:.1f}ms)\n"
                relevant_attrs = {
                    k: v
                    for k, v in span.attributes.items()
                    if any(
                        kw in k.lower()
                        for kw in ("error", "exception", "message", "status", "http", "db", "rpc")
                    )
                }
                for k, v in list(relevant_attrs.items())[:8]:
                    prompt += f"  {k}: {v}\n"

        if correlated_logs:
            prompt += f"\n## Correlated Logs ({len(correlated_logs)} entries)\n"
            for log in correlated_logs[:15]:
                prompt += f"[{log.severity.value}] {log.timestamp}  {log.body[:200]}\n"

        prompt += f"""
---

## Your Task

1. Use Glob/Grep to locate the source files for the services and operations listed in the span tree.
2. Read the relevant handlers, functions, and middleware that appear in the trace.
3. Trace the request from the root span through to the leaf spans, citing specific file:line references.

## Response Format

Respond with a JSON object in exactly this format:
{{
  "summary": "One paragraph explaining what this request does end-to-end",
  "code_flow": [
    "Step 1: <service> <file:line> — description of what happens here",
    "Step 2: ...",
    "Step 3: ..."
  ],
  "services_involved": ["service-a", "service-b"],
  "key_findings": [
    "Notable observation about the code or request pattern",
    "Performance note or architectural observation"
  ]
}}

Codebase is at: {codebase_path}
"""
        return prompt

    def _parse_trace_investigation_result(
        self, result: dict[str, Any], trace_id: str, cost_usd: float
    ) -> TraceInvestigation:
        """Parse Claude Code response into a TraceInvestigation object.

        Raises:
            ValueError: If required fields are missing or invalid.
        """
        summary = result.get("summary", "")
        if not summary:
            raise ValueError("Response missing 'summary' field")

        code_flow_raw = result.get("code_flow")
        if code_flow_raw is None:
            raise ValueError("Response missing 'code_flow' field")
        if not isinstance(code_flow_raw, list):
            raise ValueError(f"'code_flow' must be a list, got {type(code_flow_raw).__name__}")

        services_raw = result.get("services_involved")
        if services_raw is None:
            raise ValueError("Response missing 'services_involved' field")
        if not isinstance(services_raw, list):
            raise ValueError(
                f"'services_involved' must be a list, got {type(services_raw).__name__}"
            )

        findings_raw = result.get("key_findings")
        if findings_raw is None:
            raise ValueError("Response missing 'key_findings' field")
        if not isinstance(findings_raw, list):
            raise ValueError(
                f"'key_findings' must be a list, got {type(findings_raw).__name__}"
            )

        return TraceInvestigation(
            trace_id=trace_id,
            summary=summary,
            code_flow=tuple(code_flow_raw),
            services_involved=tuple(services_raw),
            key_findings=tuple(findings_raw),
            model=self.model,
            cost_usd=cost_usd,
            investigated_at=datetime.now(UTC),
        )

    def _parse_diagnosis_result(
        self, result: dict[str, Any], context: InvestigationContext
    ) -> Diagnosis:
        """Parse Claude Code response into a Diagnosis object.

        Raises:
            ValueError: If required fields are missing or invalid.
        """
        root_cause = result.get("root_cause", "")
        if not root_cause:
            raise ValueError("Response missing 'root_cause' field")

        evidence_raw = result.get("evidence")
        if evidence_raw is None:
            raise ValueError("Response missing 'evidence' field")
        if not isinstance(evidence_raw, list):
            raise ValueError(f"'evidence' must be a list, got {type(evidence_raw).__name__}")
        evidence = tuple(evidence_raw)

        suggested_fix = result.get("suggested_fix", "")
        if not suggested_fix:
            raise ValueError("Response missing 'suggested_fix' field")

        confidence_str = result.get("confidence", "")
        if not confidence_str:
            raise ValueError("Response missing 'confidence' field")

        # Parse confidence - raise on invalid value
        confidence_lower = confidence_str.lower()
        if confidence_lower not in ("high", "medium", "low"):
            raise ValueError(
                f"Invalid confidence level '{confidence_str}'. "
                f"Must be one of ['high', 'medium', 'low']"
            )

        return Diagnosis(
            root_cause=root_cause,
            evidence=evidence,
            suggested_fix=suggested_fix,
            confidence=confidence_lower,
            diagnosed_at=datetime.now(UTC),
            model=self.model,
            cost_usd=0.0,  # Will be filled in by diagnose()
        )
