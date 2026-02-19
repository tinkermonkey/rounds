"""Fingerprinting logic for normalizing and grouping errors.

This module provides the core algorithm for converting ErrorEvent instances
into stable fingerprints that identify failure patterns across multiple
occurrences.
"""

import hashlib
import re

from .models import ErrorEvent, StackFrame


class Fingerprinter:
    """Produces stable fingerprints from error events.

    No external dependencies — pure function over domain objects.
    All methods are static as the class carries no state.
    """

    @staticmethod
    def fingerprint(event: ErrorEvent) -> str:
        """Create a stable hash that identifies this class of error.

        Same bug, different occurrence → same fingerprint.

        Combines:
        - Error type
        - Service name
        - Templatized message
        - Normalized stack hash
        """
        message_template = Fingerprinter.templatize_message(event.error_message)
        normalized_stack = Fingerprinter.normalize_stack(event.stack_frames)
        stack_hash = Fingerprinter.hash_stack(normalized_stack)

        # Combine components for final fingerprint
        components = [
            event.error_type,
            event.service,
            message_template,
            stack_hash,
        ]

        fingerprint_input = "|".join(components)
        return hashlib.sha256(fingerprint_input.encode()).hexdigest()

    @staticmethod
    def normalize_stack(frames: tuple[StackFrame, ...] | list[StackFrame]) -> list[StackFrame]:
        """Strip line numbers, variable data. Keep module + function.

        Line numbers change frequently and shouldn't affect fingerprint.
        """
        return [
            StackFrame(
                module=frame.module,
                function=frame.function,
                filename=frame.filename,
                lineno=None,  # Intentionally strip line numbers
            )
            for frame in frames
        ]

    @staticmethod
    def templatize_message(message: str) -> str:
        """Replace variable parts (IPs, ports, IDs, timestamps) with placeholders.

        Examples:
        'Connection to 10.0.0.5:5432 timed out after 30s'
        → 'Connection to *:* timed out after *s'

        'User ID 12345 not found'
        → 'User ID * not found'
        """
        # Replace IP addresses
        message = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "*", message)
        # Replace ports
        message = re.sub(r":\d+\b", ":*", message)
        # Replace numeric IDs
        message = re.sub(r"\b\d{3,}\b", "*", message)
        # Replace timestamps (YYYY-MM-DD format)
        message = re.sub(r"\d{4}-\d{2}-\d{2}", "*", message)
        # Replace timestamps (HH:MM:SS format)
        message = re.sub(r"\d{2}:\d{2}:\d{2}", "*", message)
        # Replace UUIDs
        message = re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "*",
            message,
            flags=re.IGNORECASE,
        )

        return message

    @staticmethod
    def hash_stack(frames: list[StackFrame]) -> str:
        """Create a hash of the normalized stack structure."""
        stack_repr = "|".join(f"{frame.module}::{frame.function}" for frame in frames)
        return hashlib.sha256(stack_repr.encode()).hexdigest()[:16]
