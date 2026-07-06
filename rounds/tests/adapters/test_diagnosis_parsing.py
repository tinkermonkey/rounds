"""Unit tests for shared diagnosis/investigation JSON parsing."""

import pytest

from rounds.adapters.diagnosis._parsing import (
    parse_diagnosis_result,
    parse_trace_investigation_result,
)


class TestParseDiagnosisResult:
    """Tests for parse_diagnosis_result."""

    def test_parses_valid_result(self) -> None:
        result = {
            "summary": "Database connection pool exhausted under load",
            "root_cause": "Connection pool size too small for peak traffic",
            "evidence": ["handler.py:42 shows unbounded retries", "pool size is 5"],
            "suggested_fix": "Increase pool size to 20",
            "confidence": "HIGH",
        }

        diagnosis = parse_diagnosis_result(result, model="claude-opus")

        assert diagnosis.root_cause == "Connection pool size too small for peak traffic"
        assert diagnosis.evidence == (
            "handler.py:42 shows unbounded retries",
            "pool size is 5",
        )
        assert diagnosis.suggested_fix == "Increase pool size to 20"
        assert diagnosis.confidence == "high"
        assert diagnosis.model == "claude-opus"
        assert diagnosis.cost_usd == 0.0
        assert diagnosis.summary == "Database connection pool exhausted under load"

    def test_summary_falls_back_to_root_cause_when_missing(self) -> None:
        result = {
            "root_cause": "Root cause text",
            "evidence": ["evidence 1"],
            "suggested_fix": "Fix it",
            "confidence": "low",
        }

        diagnosis = parse_diagnosis_result(result, model="claude-opus")

        assert diagnosis.summary == "Root cause text"

    def test_confidence_is_case_insensitive(self) -> None:
        result = {
            "root_cause": "Root cause text",
            "evidence": ["evidence 1"],
            "suggested_fix": "Fix it",
            "confidence": "MeDiUm",
        }

        diagnosis = parse_diagnosis_result(result, model="claude-opus")

        assert diagnosis.confidence == "medium"

    def test_missing_root_cause_raises(self) -> None:
        result = {
            "evidence": ["evidence 1"],
            "suggested_fix": "Fix it",
            "confidence": "high",
        }

        with pytest.raises(ValueError, match="root_cause"):
            parse_diagnosis_result(result, model="claude-opus")

    def test_missing_evidence_raises(self) -> None:
        result = {
            "root_cause": "Root cause text",
            "suggested_fix": "Fix it",
            "confidence": "high",
        }

        with pytest.raises(ValueError, match="evidence"):
            parse_diagnosis_result(result, model="claude-opus")

    def test_evidence_not_a_list_raises(self) -> None:
        result = {
            "root_cause": "Root cause text",
            "evidence": "not a list",
            "suggested_fix": "Fix it",
            "confidence": "high",
        }

        with pytest.raises(ValueError, match="'evidence' must be a list"):
            parse_diagnosis_result(result, model="claude-opus")

    def test_missing_suggested_fix_raises(self) -> None:
        result = {
            "root_cause": "Root cause text",
            "evidence": ["evidence 1"],
            "confidence": "high",
        }

        with pytest.raises(ValueError, match="suggested_fix"):
            parse_diagnosis_result(result, model="claude-opus")

    def test_missing_confidence_raises(self) -> None:
        result = {
            "root_cause": "Root cause text",
            "evidence": ["evidence 1"],
            "suggested_fix": "Fix it",
        }

        with pytest.raises(ValueError, match="confidence"):
            parse_diagnosis_result(result, model="claude-opus")

    def test_invalid_confidence_value_raises(self) -> None:
        result = {
            "root_cause": "Root cause text",
            "evidence": ["evidence 1"],
            "suggested_fix": "Fix it",
            "confidence": "extremely-sure",
        }

        with pytest.raises(ValueError, match="Invalid confidence level"):
            parse_diagnosis_result(result, model="claude-opus")

    def test_empty_result_raises(self) -> None:
        with pytest.raises(ValueError, match="root_cause"):
            parse_diagnosis_result({}, model="claude-opus")


class TestParseTraceInvestigationResult:
    """Tests for parse_trace_investigation_result."""

    def test_parses_valid_result(self) -> None:
        result = {
            "summary": "Request flows from gateway to payment service",
            "code_flow": ["Step 1: gateway.py:10", "Step 2: payment.py:20"],
            "services_involved": ["gateway", "payment-service"],
            "key_findings": ["No retry logic on the payment call"],
        }

        investigation = parse_trace_investigation_result(
            result, trace_id="trace-123", model="claude-opus", cost_usd=0.42
        )

        assert investigation.trace_id == "trace-123"
        assert investigation.summary == "Request flows from gateway to payment service"
        assert investigation.code_flow == ("Step 1: gateway.py:10", "Step 2: payment.py:20")
        assert investigation.services_involved == ("gateway", "payment-service")
        assert investigation.key_findings == ("No retry logic on the payment call",)
        assert investigation.model == "claude-opus"
        assert investigation.cost_usd == 0.42

    def test_missing_summary_raises(self) -> None:
        result = {
            "code_flow": ["Step 1"],
            "services_involved": ["gateway"],
            "key_findings": ["finding"],
        }

        with pytest.raises(ValueError, match="summary"):
            parse_trace_investigation_result(
                result, trace_id="trace-123", model="claude-opus", cost_usd=0.1
            )

    def test_missing_code_flow_raises(self) -> None:
        result = {
            "summary": "Summary text",
            "services_involved": ["gateway"],
            "key_findings": ["finding"],
        }

        with pytest.raises(ValueError, match="code_flow"):
            parse_trace_investigation_result(
                result, trace_id="trace-123", model="claude-opus", cost_usd=0.1
            )

    def test_code_flow_not_a_list_raises(self) -> None:
        result = {
            "summary": "Summary text",
            "code_flow": "not a list",
            "services_involved": ["gateway"],
            "key_findings": ["finding"],
        }

        with pytest.raises(ValueError, match="'code_flow' must be a list"):
            parse_trace_investigation_result(
                result, trace_id="trace-123", model="claude-opus", cost_usd=0.1
            )

    def test_missing_services_involved_raises(self) -> None:
        result = {
            "summary": "Summary text",
            "code_flow": ["Step 1"],
            "key_findings": ["finding"],
        }

        with pytest.raises(ValueError, match="services_involved"):
            parse_trace_investigation_result(
                result, trace_id="trace-123", model="claude-opus", cost_usd=0.1
            )

    def test_missing_key_findings_raises(self) -> None:
        result = {
            "summary": "Summary text",
            "code_flow": ["Step 1"],
            "services_involved": ["gateway"],
        }

        with pytest.raises(ValueError, match="key_findings"):
            parse_trace_investigation_result(
                result, trace_id="trace-123", model="claude-opus", cost_usd=0.1
            )

    def test_key_findings_not_a_list_raises(self) -> None:
        result = {
            "summary": "Summary text",
            "code_flow": ["Step 1"],
            "services_involved": ["gateway"],
            "key_findings": "not a list",
        }

        with pytest.raises(ValueError, match="'key_findings' must be a list"):
            parse_trace_investigation_result(
                result, trace_id="trace-123", model="claude-opus", cost_usd=0.1
            )
