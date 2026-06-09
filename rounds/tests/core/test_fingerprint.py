"""Unit tests for Fingerprinter (rounds/core/fingerprint.py).

Covers all four public static methods: templatize_message, normalize_stack,
hash_stack, and fingerprint.
"""

from datetime import UTC, datetime

import pytest

from rounds.core.fingerprint import Fingerprinter
from rounds.core.models import ErrorEvent, Severity, StackFrame


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_frame(
    module: str = "myapp.db",
    function: str = "connect",
    filename: str = "db.py",
    lineno: int | None = 42,
) -> StackFrame:
    return StackFrame(module=module, function=function, filename=filename, lineno=lineno)


def make_event(
    *,
    trace_id: str = "trace-001",
    span_id: str = "span-001",
    service: str = "payment-service",
    error_type: str = "ConnectionTimeoutError",
    error_message: str = "Connection to 10.0.0.5:5432 timed out after 30s",
    stack_frames: tuple[StackFrame, ...] | None = None,
) -> ErrorEvent:
    if stack_frames is None:
        stack_frames = (make_frame(),)
    return ErrorEvent(
        trace_id=trace_id,
        span_id=span_id,
        service=service,
        error_type=error_type,
        error_message=error_message,
        stack_frames=stack_frames,
        timestamp=datetime(2026, 6, 9, 14, 30, 0, tzinfo=UTC),
        attributes={},
        severity=Severity.ERROR,
    )


# ---------------------------------------------------------------------------
# templatize_message
# ---------------------------------------------------------------------------


class TestTemplatizeMessage:
    def test_replaces_ipv4_address(self) -> None:
        result = Fingerprinter.templatize_message("Connection to 10.0.0.5 failed")
        assert "10.0.0.5" not in result
        assert "*" in result

    def test_replaces_port(self) -> None:
        result = Fingerprinter.templatize_message("Connect to host:5432")
        assert ":5432" not in result
        assert ":*" in result

    def test_replaces_numeric_id(self) -> None:
        result = Fingerprinter.templatize_message("User ID 12345 not found")
        assert "12345" not in result
        assert "*" in result

    def test_does_not_replace_short_numbers(self) -> None:
        # Numbers with fewer than 3 digits should not be replaced
        result = Fingerprinter.templatize_message("Retry attempt 2 of 5")
        assert "2" in result
        assert "5" in result

    def test_replaces_date_timestamp(self) -> None:
        result = Fingerprinter.templatize_message("Error on 2026-06-09 in production")
        assert "2026-06-09" not in result
        assert "*" in result

    def test_replaces_time_timestamp(self) -> None:
        result = Fingerprinter.templatize_message("Occurred at 14:30:00 UTC")
        assert "14:30:00" not in result
        assert "*" in result

    def test_replaces_uuid_lowercase(self) -> None:
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        result = Fingerprinter.templatize_message(f"Request ID {uuid}")
        assert uuid not in result
        assert "*" in result

    def test_replaces_uuid_uppercase(self) -> None:
        uuid = "550E8400-E29B-41D4-A716-446655440000"
        result = Fingerprinter.templatize_message(f"Request ID {uuid}")
        assert uuid not in result
        assert "*" in result

    def test_combined_ip_and_port(self) -> None:
        # 30 has only 2 digits so the numeric-ID rule (3+ digits) leaves it intact
        result = Fingerprinter.templatize_message(
            "Connection to 10.0.0.5:5432 timed out after 30s"
        )
        assert result == "Connection to *:* timed out after 30s"

    def test_combined_ip_port_and_long_id(self) -> None:
        result = Fingerprinter.templatize_message(
            "Connection to 10.0.0.5:5432 failed for user 12345"
        )
        assert result == "Connection to *:* failed for user *"

    def test_static_text_unchanged(self) -> None:
        msg = "database connection pool exhausted"
        assert Fingerprinter.templatize_message(msg) == msg


# ---------------------------------------------------------------------------
# normalize_stack
# ---------------------------------------------------------------------------


class TestNormalizeStack:
    def test_strips_line_numbers(self) -> None:
        frame = make_frame(lineno=99)
        normalized = Fingerprinter.normalize_stack((frame,))
        assert normalized[0].lineno is None

    def test_preserves_module(self) -> None:
        frame = make_frame(module="myapp.db")
        normalized = Fingerprinter.normalize_stack((frame,))
        assert normalized[0].module == "myapp.db"

    def test_preserves_function(self) -> None:
        frame = make_frame(function="execute_query")
        normalized = Fingerprinter.normalize_stack((frame,))
        assert normalized[0].function == "execute_query"

    def test_preserves_filename(self) -> None:
        frame = make_frame(filename="myapp/db.py")
        normalized = Fingerprinter.normalize_stack((frame,))
        assert normalized[0].filename == "myapp/db.py"

    def test_handles_multiple_frames(self) -> None:
        frames = (
            make_frame(module="a", function="f1", filename="a.py", lineno=10),
            make_frame(module="b", function="f2", filename="b.py", lineno=20),
        )
        normalized = Fingerprinter.normalize_stack(frames)
        assert len(normalized) == 2
        assert all(f.lineno is None for f in normalized)
        assert normalized[0].module == "a"
        assert normalized[1].module == "b"

    def test_handles_empty_stack(self) -> None:
        assert Fingerprinter.normalize_stack(()) == []

    def test_accepts_list_input(self) -> None:
        frames = [make_frame(lineno=5)]
        normalized = Fingerprinter.normalize_stack(frames)
        assert normalized[0].lineno is None


# ---------------------------------------------------------------------------
# hash_stack
# ---------------------------------------------------------------------------


class TestHashStack:
    def test_deterministic(self) -> None:
        frames = Fingerprinter.normalize_stack((make_frame(),))
        assert Fingerprinter.hash_stack(frames) == Fingerprinter.hash_stack(frames)

    def test_same_frames_same_hash(self) -> None:
        frames_a = Fingerprinter.normalize_stack(
            (make_frame(module="a", function="f", filename="a.py", lineno=10),)
        )
        frames_b = Fingerprinter.normalize_stack(
            (make_frame(module="a", function="f", filename="a.py", lineno=99),)
        )
        assert Fingerprinter.hash_stack(frames_a) == Fingerprinter.hash_stack(frames_b)

    def test_different_frames_different_hash(self) -> None:
        frames_a = Fingerprinter.normalize_stack(
            (make_frame(module="a", function="f", filename="a.py"),)
        )
        frames_b = Fingerprinter.normalize_stack(
            (make_frame(module="b", function="g", filename="b.py"),)
        )
        assert Fingerprinter.hash_stack(frames_a) != Fingerprinter.hash_stack(frames_b)

    def test_ignores_line_numbers(self) -> None:
        frame_low = make_frame(lineno=1)
        frame_high = make_frame(lineno=9999)
        normalized_low = Fingerprinter.normalize_stack((frame_low,))
        normalized_high = Fingerprinter.normalize_stack((frame_high,))
        assert Fingerprinter.hash_stack(normalized_low) == Fingerprinter.hash_stack(normalized_high)

    def test_returns_16_char_hex(self) -> None:
        frames = Fingerprinter.normalize_stack((make_frame(),))
        h = Fingerprinter.hash_stack(frames)
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_same_error_same_fingerprint(self) -> None:
        event_a = make_event()
        event_b = make_event()
        assert Fingerprinter.fingerprint(event_a) == Fingerprinter.fingerprint(event_b)

    def test_different_ip_same_fingerprint(self) -> None:
        event_a = make_event(error_message="Connection to 10.0.0.1:5432 timed out after 30s")
        event_b = make_event(error_message="Connection to 192.168.1.99:5432 timed out after 30s")
        assert Fingerprinter.fingerprint(event_a) == Fingerprinter.fingerprint(event_b)

    def test_different_numeric_id_same_fingerprint(self) -> None:
        event_a = make_event(error_message="User ID 11111 not found")
        event_b = make_event(error_message="User ID 99999 not found")
        assert Fingerprinter.fingerprint(event_a) == Fingerprinter.fingerprint(event_b)

    def test_different_trace_id_same_fingerprint(self) -> None:
        event_a = make_event(trace_id="trace-aaa")
        event_b = make_event(trace_id="trace-bbb")
        assert Fingerprinter.fingerprint(event_a) == Fingerprinter.fingerprint(event_b)

    def test_different_span_id_same_fingerprint(self) -> None:
        event_a = make_event(span_id="span-111")
        event_b = make_event(span_id="span-999")
        assert Fingerprinter.fingerprint(event_a) == Fingerprinter.fingerprint(event_b)

    def test_different_lineno_same_fingerprint(self) -> None:
        frames_a = (make_frame(lineno=10),)
        frames_b = (make_frame(lineno=200),)
        event_a = make_event(stack_frames=frames_a)
        event_b = make_event(stack_frames=frames_b)
        assert Fingerprinter.fingerprint(event_a) == Fingerprinter.fingerprint(event_b)

    def test_different_error_type_different_fingerprint(self) -> None:
        event_a = make_event(error_type="ConnectionTimeoutError")
        event_b = make_event(error_type="AuthorizationError")
        assert Fingerprinter.fingerprint(event_a) != Fingerprinter.fingerprint(event_b)

    def test_different_service_different_fingerprint(self) -> None:
        event_a = make_event(service="payment-service")
        event_b = make_event(service="auth-service")
        assert Fingerprinter.fingerprint(event_a) != Fingerprinter.fingerprint(event_b)

    def test_different_message_template_different_fingerprint(self) -> None:
        event_a = make_event(error_message="Connection to 10.0.0.1:5432 timed out after 30s")
        event_b = make_event(error_message="User ID 12345 not found in database")
        assert Fingerprinter.fingerprint(event_a) != Fingerprinter.fingerprint(event_b)

    def test_different_stack_different_fingerprint(self) -> None:
        frames_a = (make_frame(module="myapp.db", function="connect"),)
        frames_b = (make_frame(module="myapp.auth", function="authenticate"),)
        event_a = make_event(stack_frames=frames_a)
        event_b = make_event(stack_frames=frames_b)
        assert Fingerprinter.fingerprint(event_a) != Fingerprinter.fingerprint(event_b)

    def test_returns_hex_string(self) -> None:
        result = Fingerprinter.fingerprint(make_event())
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic_across_calls(self) -> None:
        event = make_event()
        assert Fingerprinter.fingerprint(event) == Fingerprinter.fingerprint(event)
