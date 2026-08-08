"""Shared parsing of LLM diagnosis JSON output into domain models.

Both the Claude Code and agent-node diagnosis adapters invoke Claude with the
same response format (see the `## Response Format` sections of the prompts
built in `claude_code.py`), so the JSON-to-domain-model parsing lives here to
avoid duplicating field validation across adapters.
"""

from datetime import UTC, datetime
from typing import Any

from rounds.core.models import Diagnosis, TraceInvestigation


def parse_diagnosis_result(result: dict[str, Any], model: str) -> Diagnosis:
    """Parse an LLM diagnosis response into a Diagnosis object.

    Args:
        result: Parsed JSON dict from the LLM response, expected to contain
            root_cause, evidence, suggested_fix, confidence, and optionally
            summary and suggested_resolution_hours.
        model: Name of the model that produced the response.

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

    confidence_lower = confidence_str.lower()
    if confidence_lower not in ("high", "medium", "low"):
        raise ValueError(
            f"Invalid confidence level '{confidence_str}'. "
            f"Must be one of ['high', 'medium', 'low']"
        )

    # summary is optional; fall back to root_cause for backward compatibility
    summary = result.get("summary", "") or root_cause

    suggested_resolution_hours = parse_suggested_resolution_hours(
        result.get("suggested_resolution_hours")
    )

    return Diagnosis(
        root_cause=root_cause,
        evidence=evidence,
        suggested_fix=suggested_fix,
        confidence=confidence_lower,
        diagnosed_at=datetime.now(UTC),
        model=model,
        cost_usd=0.0,  # Will be filled in by the caller
        summary=summary,
        suggested_resolution_hours=suggested_resolution_hours,
    )


def parse_suggested_resolution_hours(raw: Any) -> int | None:
    """Parse the optional `suggested_resolution_hours` field.

    Absent or null means the LLM was uncertain; the global default resolution
    window applies instead (see Diagnosis.suggested_resolution_hours).

    Raises:
        ValueError: If present but not coercible to an int.
    """
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(
            "'suggested_resolution_hours' must be an integer, got "
            f"{raw!r}"
        ) from None


def parse_trace_investigation_result(
    result: dict[str, Any], trace_id: str, model: str, cost_usd: float
) -> TraceInvestigation:
    """Parse an LLM trace-investigation response into a TraceInvestigation object.

    Args:
        result: Parsed JSON dict from the LLM response, expected to contain
            summary, code_flow, services_involved, and key_findings.
        trace_id: ID of the trace that was investigated.
        model: Name of the model that produced the response.
        cost_usd: Cost of the investigation in USD.

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
        raise ValueError(f"'key_findings' must be a list, got {type(findings_raw).__name__}")

    return TraceInvestigation(
        trace_id=trace_id,
        summary=summary,
        code_flow=tuple(code_flow_raw),
        services_involved=tuple(services_raw),
        key_findings=tuple(findings_raw),
        model=model,
        cost_usd=cost_usd,
        investigated_at=datetime.now(UTC),
    )
