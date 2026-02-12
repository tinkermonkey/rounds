"""Claude Code diagnosis adapter.

Implements DiagnosisPort by invoking Claude Code CLI in headless mode
for LLM-powered code analysis and root cause diagnosis.

Invocation format: claude -p <prompt> --output-format json
"""

import asyncio
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rounds.core.models import Diagnosis, InvestigationContext
from rounds.core.ports import DiagnosisPort

logger = logging.getLogger(__name__)


class ClaudeCodeDiagnosisAdapter(DiagnosisPort):
    """Claude Code CLI-based diagnosis adapter."""

    def __init__(
        self,
        model: str = "claude-opus",
        budget_usd: float = 2.0,
    ):
        """Initialize Claude Code diagnosis adapter.

        Args:
            model: Claude model to use (e.g., 'claude-opus', 'claude-sonnet')
            budget_usd: Budget per diagnosis in USD
        """
        self.model = model
        self.budget_usd = budget_usd

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

            # Invoke Claude Code
            result = await self._invoke_claude_code(prompt)

            # Parse result
            diagnosis = self._parse_diagnosis_result(result, context)

            # Track actual cost (approximation based on tokens)
            diagnosis = Diagnosis(
                root_cause=diagnosis.root_cause,
                evidence=diagnosis.evidence,
                suggested_fix=diagnosis.suggested_fix,
                confidence=diagnosis.confidence,
                diagnosed_at=datetime.now(timezone.utc),
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
        base_cost = 0.30

        # Add cost for context size
        context_size = (
            len(context.recent_events)
            + len(context.trace_data)
            + len(context.related_logs)
        )

        # Rough estimate: $0.01 per 10 context items
        additional_cost = (context_size / 10) * 0.01

        total_cost = base_cost + additional_cost

        return total_cost

    def _build_investigation_prompt(self, context: InvestigationContext) -> str:
        """Build a comprehensive investigation prompt for Claude Code."""
        prompt = f"""You are a expert software engineer analyzing a failure pattern in production code.

## Signature Details
Error Type: {context.signature.error_type}
Service: {context.signature.service}
Message Template: {context.signature.message_template}
Status: {context.signature.status.value}
Occurrence Count: {context.signature.occurrence_count}
First Seen: {context.signature.first_seen}
Last Seen: {context.signature.last_seen}

## Recent Error Events ({len(context.recent_events)} total)
"""

        for i, event in enumerate(context.recent_events[:5], 1):
            prompt += f"""
### Event {i}
- Timestamp: {event.timestamp}
- Service: {event.service}
- Error: {event.error_type}: {event.error_message}
- Stack Trace:
"""
            for frame in event.stack_frames[:10]:
                prompt += f"  {frame.module}.{frame.function} ({frame.filename}:{frame.lineno})\n"

        # Add trace information
        if context.trace_data:
            prompt += f"\n## Distributed Traces ({len(context.trace_data)} traces)\n"
            for trace in context.trace_data[:2]:
                prompt += f"- Trace {trace.trace_id}: {len(trace.error_spans)} error spans\n"

        # Add logs
        if context.related_logs:
            prompt += f"\n## Related Logs ({len(context.related_logs)} logs)\n"
            for log in context.related_logs[:10]:
                prompt += f"- [{log.severity.value}] {log.timestamp}: {log.body}\n"

        # Add codebase context
        prompt += f"\n## Codebase Path: {context.codebase_path}\n"

        # Add historical context
        if context.historical_context:
            prompt += f"\n## Historical Context ({len(context.historical_context)} similar signatures)\n"
            for sig in context.historical_context[:3]:
                prompt += f"- {sig.error_type} in {sig.service} ({sig.occurrence_count} occurrences)\n"

        prompt += """
## Task
Based on the error events, traces, logs, and codebase context above, provide:

1. **Root Cause**: The underlying cause of this error pattern. Be specific and cite evidence.
2. **Evidence**: List 3-5 key pieces of evidence supporting your conclusion.
3. **Suggested Fix**: A concrete, actionable fix that would prevent this error.
4. **Confidence**: Rate your confidence as HIGH, MEDIUM, or LOW.

Respond with a JSON object in exactly this format:
{
  "root_cause": "The root cause explanation",
  "evidence": ["evidence point 1", "evidence point 2", "evidence point 3"],
  "suggested_fix": "The suggested fix",
  "confidence": "HIGH|MEDIUM|LOW"
}
"""

        return prompt

    async def _invoke_claude_code(self, prompt: str) -> dict[str, Any]:
        """Invoke Claude Code CLI with the investigation prompt asynchronously.

        Raises:
            ValueError: If JSON parsing fails or no valid JSON found in output.
            TimeoutError: If CLI invocation times out.
            RuntimeError: If CLI returns non-zero exit code.
        """
        try:
            # Invoke Claude Code CLI in an executor to avoid blocking the event loop
            loop = asyncio.get_running_loop()

            def _run_claude_code() -> str:
                """Synchronous wrapper for subprocess call."""
                try:
                    result = subprocess.run(
                        ["claude", "-p", prompt, "--output-format", "json"],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )

                    if result.returncode != 0:
                        error_output = result.stderr or result.stdout
                        raise RuntimeError(f"Claude Code CLI failed: {error_output}")

                    return result.stdout.strip()

                except subprocess.TimeoutExpired:
                    raise TimeoutError("Claude Code CLI timed out after 60 seconds")

            # Run in executor to avoid blocking
            output = await loop.run_in_executor(None, _run_claude_code)

            # Parse the JSON output
            # Claude Code returns json format, so we need to extract the content
            lines = output.split("\n")
            for line in lines:
                if line.startswith("{"):
                    try:
                        parsed: dict[str, Any] = json.loads(line)
                        return parsed
                    except json.JSONDecodeError as e:
                        logger.warning(
                            f"Failed to parse JSON line from Claude Code output: {e}. "
                            f"Line: {line[:200]}",
                            exc_info=True,
                        )
                        continue

            # No valid JSON found - raise exception instead of returning synthetic data
            raise ValueError(
                f"Claude Code CLI did not return valid JSON. Output: {output[:200]}"
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
            diagnosed_at=datetime.now(timezone.utc),
            model=self.model,
            cost_usd=0.0,  # Will be filled in by diagnose()
        )
