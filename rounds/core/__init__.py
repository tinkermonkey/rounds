"""Core domain logic for the Rounds diagnostic system.

This package contains zero external dependencies and represents
the pure business logic of the application. All adapters and
external integrations are handled by the adapters package.
"""

from .models import (
    Confidence,
    Diagnosis,
    ErrorEvent,
    InvestigationContext,
    LogEntry,
    PollResult,
    Severity,
    Signature,
    SignatureStatus,
    SpanNode,
    StackFrame,
    TraceTree,
)

__all__ = [
    "Confidence",
    "Diagnosis",
    "ErrorEvent",
    "InvestigationContext",
    "LogEntry",
    "PollResult",
    "Severity",
    "Signature",
    "SignatureStatus",
    "SpanNode",
    "StackFrame",
    "TraceTree",
]
