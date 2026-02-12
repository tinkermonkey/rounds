"""CLI command implementations for Rounds management.

Provides human-initiated actions through command-line interface.

This adapter maps CLI commands (mute, resolve, retriage, details) to ManagementPort
operations. It handles CLI-specific formatting and error reporting.
"""

import asyncio
import json
import logging
from typing import Any

from rounds.core.ports import ManagementPort

logger = logging.getLogger(__name__)


class CLICommandHandler:
    """Handles CLI commands by delegating to ManagementPort.

    Provides a command-line interface for management operations (mute, resolve,
    retriage, get details) on signatures.
    """

    def __init__(self, management: ManagementPort):
        """Initialize the CLI command handler.

        Args:
            management: ManagementPort implementation to execute commands.
        """
        self.management = management

    async def mute_signature(
        self, signature_id: str, reason: str | None = None, verbose: bool = False
    ) -> dict[str, Any]:
        """Mute a signature via CLI.

        Args:
            signature_id: UUID of the signature to mute.
            reason: Optional reason for muting.
            verbose: If True, print additional information.

        Returns:
            Dictionary with status and message.

        Raises:
            ValueError: If signature doesn't exist.
            Exception: If operation fails.
        """
        try:
            await self.management.mute_signature(signature_id, reason)

            result = {
                "status": "success",
                "operation": "mute",
                "signature_id": signature_id,
                "message": f"Signature {signature_id} muted",
            }

            if reason:
                result["reason"] = reason

            if verbose:
                logger.info(
                    f"Muted signature {signature_id}",
                    extra={"reason": reason, "verbose": True},
                )

            return result

        except ValueError as e:
            logger.error(f"Failed to mute signature: {e}")
            return {
                "status": "error",
                "operation": "mute",
                "signature_id": signature_id,
                "message": str(e),
            }

    async def resolve_signature(
        self,
        signature_id: str,
        fix_applied: str | None = None,
        verbose: bool = False,
    ) -> dict[str, Any]:
        """Resolve a signature via CLI.

        Args:
            signature_id: UUID of the signature.
            fix_applied: Optional description of the fix.
            verbose: If True, print additional information.

        Returns:
            Dictionary with status and message.

        Raises:
            ValueError: If signature doesn't exist.
            Exception: If operation fails.
        """
        try:
            await self.management.resolve_signature(signature_id, fix_applied)

            result = {
                "status": "success",
                "operation": "resolve",
                "signature_id": signature_id,
                "message": f"Signature {signature_id} resolved",
            }

            if fix_applied:
                result["fix_applied"] = fix_applied

            if verbose:
                logger.info(
                    f"Resolved signature {signature_id}",
                    extra={"fix_applied": fix_applied, "verbose": True},
                )

            return result

        except ValueError as e:
            logger.error(f"Failed to resolve signature: {e}")
            return {
                "status": "error",
                "operation": "resolve",
                "signature_id": signature_id,
                "message": str(e),
            }

    async def retriage_signature(
        self, signature_id: str, verbose: bool = False
    ) -> dict[str, Any]:
        """Retriage a signature via CLI.

        Args:
            signature_id: UUID of the signature.
            verbose: If True, print additional information.

        Returns:
            Dictionary with status and message.

        Raises:
            ValueError: If signature doesn't exist.
            Exception: If operation fails.
        """
        try:
            await self.management.retriage_signature(signature_id)

            result = {
                "status": "success",
                "operation": "retriage",
                "signature_id": signature_id,
                "message": f"Signature {signature_id} retriaged and queued for re-investigation",
            }

            if verbose:
                logger.info(
                    f"Retriaged signature {signature_id}",
                    extra={"verbose": True},
                )

            return result

        except ValueError as e:
            logger.error(f"Failed to retriage signature: {e}")
            return {
                "status": "error",
                "operation": "retriage",
                "signature_id": signature_id,
                "message": str(e),
            }

    async def get_signature_details(
        self, signature_id: str, format: str = "json"
    ) -> dict[str, Any]:
        """Retrieve signature details via CLI.

        Args:
            signature_id: UUID of the signature.
            format: Output format ('json', 'text'). Default 'json'.

        Returns:
            Dictionary with signature details or status/message on error.

        Raises:
            ValueError: If signature doesn't exist.
            Exception: If operation fails.
        """
        try:
            details = await self.management.get_signature_details(signature_id)

            if format == "json":
                return {
                    "status": "success",
                    "operation": "get_details",
                    "data": details,
                }

            elif format == "text":
                # Convert to human-readable text format
                text_output = self._format_details_as_text(details)
                return {
                    "status": "success",
                    "operation": "get_details",
                    "data": text_output,
                }

            else:
                return {
                    "status": "error",
                    "operation": "get_details",
                    "message": f"Unsupported format: {format}",
                }

        except ValueError as e:
            logger.error(f"Failed to get signature details: {e}")
            return {
                "status": "error",
                "operation": "get_details",
                "signature_id": signature_id,
                "message": str(e),
            }

    def _format_details_as_text(self, details: dict[str, Any]) -> str:
        """Format signature details as human-readable text.

        Args:
            details: Signature details dictionary.

        Returns:
            Formatted text string.
        """
        lines = []

        # Header
        lines.append(f"Signature ID: {details.get('id')}")
        lines.append(f"Fingerprint: {details.get('fingerprint')}")
        lines.append(f"Service: {details.get('service')}")
        lines.append(f"Error Type: {details.get('error_type')}")
        lines.append("")

        # Status and counts
        lines.append(f"Status: {details.get('status')}")
        lines.append(f"Occurrences: {details.get('occurrence_count')}")
        lines.append(f"First Seen: {details.get('first_seen')}")
        lines.append(f"Last Seen: {details.get('last_seen')}")
        lines.append("")

        # Message template
        lines.append(f"Message Template: {details.get('message_template')}")
        lines.append("")

        # Diagnosis if available
        if details.get("diagnosis"):
            diagnosis = details["diagnosis"]
            lines.append("Diagnosis:")
            lines.append(f"  Root Cause: {diagnosis.get('root_cause')}")
            lines.append(f"  Confidence: {diagnosis.get('confidence')}")
            lines.append(f"  Suggested Fix: {diagnosis.get('suggested_fix')}")
            lines.append("")

        # Related signatures
        if details.get("related_signatures"):
            lines.append("Related Signatures:")
            for sig in details["related_signatures"]:
                lines.append(f"  - {sig.get('id')}: {sig.get('service')} ({sig.get('occurrence_count')} occurrences)")
            lines.append("")

        return "\n".join(lines)


async def run_command(
    management: ManagementPort,
    command: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Run a CLI command.

    Entry point for executing CLI commands. Maps command names to handler methods.

    Args:
        management: ManagementPort implementation.
        command: Command name ('mute', 'resolve', 'retriage', 'details').
        args: Dictionary of command arguments.

    Returns:
        Dictionary with command result.

    Raises:
        ValueError: If command is not recognized.
    """
    handler = CLICommandHandler(management)

    if command == "mute":
        return await handler.mute_signature(
            args["signature_id"],
            args.get("reason"),
            args.get("verbose", False),
        )

    elif command == "resolve":
        return await handler.resolve_signature(
            args["signature_id"],
            args.get("fix_applied"),
            args.get("verbose", False),
        )

    elif command == "retriage":
        return await handler.retriage_signature(
            args["signature_id"],
            args.get("verbose", False),
        )

    elif command == "details":
        return await handler.get_signature_details(
            args["signature_id"],
            args.get("format", "json"),
        )

    else:
        raise ValueError(f"Unknown command: {command}")
