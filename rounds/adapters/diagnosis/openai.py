"""OpenAI diagnosis adapter.

Implements DiagnosisPort by invoking OpenAI API (GPT-4, GPT-4o, etc.)
for LLM-powered code analysis and root cause diagnosis.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from rounds.core.models import Diagnosis, InvestigationContext
from rounds.core.ports import DiagnosisPort

logger = logging.getLogger(__name__)


class OpenAIDiagnosisAdapter(DiagnosisPort):
    """OpenAI API-based diagnosis adapter."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4",
        budget_usd: float = 2.0,
    ):
        """Initialize OpenAI diagnosis adapter.

        Args:
            api_key: OpenAI API key.
            model: OpenAI model to use (e.g., 'gpt-4', 'gpt-4o').
            budget_usd: Budget per diagnosis in USD.

        Raises:
            ValueError: If API key is empty or not provided.
        """
        if not api_key or not api_key.strip():
            raise ValueError(
                "OpenAI API key must be provided and non-empty. "
                "Set OPENAI_API_KEY environment variable."
            )
        self.api_key = api_key
        self.model = model
        self.budget_usd = budget_usd

        # Lazy import to avoid requiring openai if not used
        self._client: Any | None = None

    async def _get_client(self) -> Any:
        """Get or initialize the OpenAI synchronous client."""
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self.api_key)
            except ImportError:
                raise ImportError(
                    "openai package required for OpenAI adapter. "
                    "Install with: pip install openai"
                )
        return self._client

    async def diagnose(
        self, context: InvestigationContext
    ) -> Diagnosis:
        """Invoke OpenAI API for LLM analysis on investigation context."""
        try:
            # Estimate cost first
            estimated_cost = await self.estimate_cost(context)

            if estimated_cost > self.budget_usd:
                raise ValueError(
                    f"Diagnosis cost ${estimated_cost:.2f} exceeds budget ${self.budget_usd:.2f}"
                )

            # Build the investigation prompt
            prompt = self._build_investigation_prompt(context)

            # Invoke OpenAI
            result = await self._invoke_openai(prompt)

            # Parse result
            diagnosis = self._parse_diagnosis_result(result, context)

            # Track actual cost (approximation based on model)
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

        Simple heuristic: estimate based on context size and model.
        - Base cost: $0.15 per diagnosis
        - Additional cost based on context size (events, logs, code)

        Note: Returns the true estimated cost, not capped at budget.
        The caller is responsible for budget enforcement.
        """
        # Base cost varies by model
        if self.model == "gpt-4":
            base_cost = 0.15
        elif self.model == "gpt-4o":
            base_cost = 0.10
        else:
            base_cost = 0.15

        # Add cost for context size
        context_size = (
            len(context.recent_events)
            + len(context.trace_data)
            + len(context.related_logs)
        )

        # Rough estimate: $0.005 per 10 context items
        additional_cost = (context_size / 10) * 0.005

        total_cost = base_cost + additional_cost

        return total_cost

    def _build_investigation_prompt(self, context: InvestigationContext) -> str:
        """Build a comprehensive investigation prompt for OpenAI."""
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

    async def _invoke_openai(self, prompt: str) -> dict[str, Any]:
        """Invoke OpenAI API with the investigation prompt.

        Raises:
            ValueError: If JSON parsing fails or response is invalid.
            TimeoutError: If API call times out.
            RuntimeError: If API returns an error.
        """
        try:
            client = await self._get_client()

            # Call OpenAI API in executor to avoid blocking event loop
            loop = asyncio.get_running_loop()

            def _call_openai() -> str:
                """Synchronous wrapper for OpenAI API call."""
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are an expert software engineer analyzing production errors. "
                                       "Respond only with valid JSON matching the requested format.",
                        },
                        {
                            "role": "user",
                            "content": prompt,
                        },
                    ],
                    temperature=0.7,
                    timeout=60,
                )

                # Extract response text
                if response.choices and response.choices[0].message:
                    return response.choices[0].message.content
                else:
                    raise RuntimeError("OpenAI API returned empty response")

            # Run in executor to avoid blocking
            output = await loop.run_in_executor(None, _call_openai)

            # Parse the JSON output
            lines = output.split("\n")
            for line in lines:
                if line.startswith("{"):
                    try:
                        parsed: dict[str, Any] = json.loads(line)
                        return parsed
                    except json.JSONDecodeError as e:
                        logger.warning(
                            f"Failed to parse JSON line from OpenAI output: {e}. "
                            f"Line: {line[:200]}",
                            exc_info=True,
                        )
                        continue

            # No valid JSON found
            raise ValueError(
                f"OpenAI API did not return valid JSON. Output: {output[:200]}"
            )

        except TimeoutError as e:
            logger.error(f"OpenAI API timeout: {e}", exc_info=True)
            raise TimeoutError(str(e)) from e
        except RuntimeError as e:
            logger.error(f"OpenAI API error: {e}", exc_info=True)
            raise RuntimeError(str(e)) from e
        except ValueError as e:
            logger.error(f"Failed to parse OpenAI response: {e}", exc_info=True)
            raise ValueError(str(e)) from e
        except Exception as e:
            logger.error(f"Failed to invoke OpenAI API: {e}", exc_info=True)
            raise

    def _parse_diagnosis_result(
        self, result: dict[str, Any], context: InvestigationContext
    ) -> Diagnosis:
        """Parse OpenAI response into a Diagnosis object.

        Raises:
            ValueError: If required fields are missing or invalid.
        """
        root_cause = result.get("root_cause", "")
        if not root_cause:
            raise ValueError("Response missing 'root_cause' field")

        evidence_raw = result.get("evidence")
        if evidence_raw is None:
            raise ValueError("Response missing 'evidence' field")

        evidence = tuple(evidence_raw) if isinstance(evidence_raw, list) else (str(evidence_raw),)

        suggested_fix = result.get("suggested_fix", "")
        if not suggested_fix:
            raise ValueError("Response missing 'suggested_fix' field")

        confidence_raw = result.get("confidence", "").upper()
        if confidence_raw not in ("HIGH", "MEDIUM", "LOW"):
            raise ValueError(
                f"Invalid confidence level '{confidence_raw}'. "
                f"Must be one of ['HIGH', 'MEDIUM', 'LOW']"
            )

        return Diagnosis(
            root_cause=root_cause,
            evidence=evidence,
            suggested_fix=suggested_fix,
            confidence=confidence_raw.lower(),
            diagnosed_at=datetime.now(timezone.utc),
            model=self.model,
            cost_usd=0.0,  # Will be overwritten by caller
        )
