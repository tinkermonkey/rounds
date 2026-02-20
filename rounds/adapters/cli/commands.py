"""CLI command implementations for Rounds management.

Provides human-initiated actions through command-line interface.

This adapter maps CLI commands (mute, resolve, retriage, details) to ManagementPort
operations. It handles CLI-specific formatting and error reporting.
"""

import logging
from collections.abc import Sequence
from typing import Any

from rounds.core.models import Signature, SignatureDetails
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
            Dictionary with status and data:
            - On success: {"status": "success", "operation": "mute", "signature_id": str, "message": str}
            - On error: {"status": "error", "operation": "mute", "signature_id": str, "message": str}
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

        except (ValueError, Exception) as e:
            logger.error(f"Failed to mute signature: {e}", exc_info=True)
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
            Dictionary with status and data:
            - On success: {"status": "success", "operation": "resolve", "signature_id": str, "message": str}
            - On error: {"status": "error", "operation": "resolve", "signature_id": str, "message": str}
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

        except (ValueError, Exception) as e:
            logger.error(f"Failed to resolve signature: {e}", exc_info=True)
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
            Dictionary with status and data:
            - On success: {"status": "success", "operation": "retriage", "signature_id": str, "message": str}
            - On error: {"status": "error", "operation": "retriage", "signature_id": str, "message": str}
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

        except (ValueError, Exception) as e:
            logger.error(f"Failed to retriage signature: {e}", exc_info=True)
            return {
                "status": "error",
                "operation": "retriage",
                "signature_id": signature_id,
                "message": str(e),
            }

    async def get_signature_details(
        self, signature_id: str, output_format: str = "json"
    ) -> dict[str, Any]:
        """Retrieve signature details via CLI.

        Args:
            signature_id: UUID of the signature.
            output_format: Output format ('json', 'text'). Default 'json'.

        Returns:
            Dictionary with status and data:
            - On success: {"status": "success", "operation": "get_details", "data": {...}}
            - On error: {"status": "error", "operation": "get_details", "message": str}
        """
        try:
            details = await self.management.get_signature_details(signature_id)

            if output_format == "json":
                return {
                    "status": "success",
                    "operation": "get_details",
                    "data": details,
                }

            elif output_format == "text":
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
                    "message": f"Unsupported format: {output_format}",
                }

        except (ValueError, Exception) as e:
            logger.error(f"Failed to get signature details: {e}", exc_info=True)
            return {
                "status": "error",
                "operation": "get_details",
                "signature_id": signature_id,
                "message": str(e),
            }

    def _format_details_as_text(self, details: SignatureDetails) -> str:
        """Format signature details as human-readable text.

        Args:
            details: SignatureDetails object containing signature and related data.

        Returns:
            Formatted text string.
        """
        lines = []
        sig = details.signature

        # Header
        lines.append(f"Signature ID: {sig.id}")
        lines.append(f"Fingerprint: {sig.fingerprint}")
        lines.append(f"Service: {sig.service}")
        lines.append(f"Error Type: {sig.error_type}")
        lines.append("")

        # Status and counts
        lines.append(f"Status: {sig.status.value}")
        lines.append(f"Occurrences: {sig.occurrence_count}")
        lines.append(f"First Seen: {sig.first_seen.isoformat()}")
        lines.append(f"Last Seen: {sig.last_seen.isoformat()}")
        lines.append("")

        # Message template
        lines.append(f"Message Template: {sig.message_template}")
        lines.append("")

        # Diagnosis if available
        if sig.diagnosis:
            lines.append("Diagnosis:")
            lines.append(f"  Root Cause: {sig.diagnosis.root_cause}")
            lines.append(f"  Confidence: {sig.diagnosis.confidence}")
            lines.append("")

        # Recent events
        if details.recent_events:
            lines.append(f"Recent Events ({len(details.recent_events)}):")
            for event in details.recent_events[:5]:  # Show first 5
                lines.append(f"  - {event.timestamp.isoformat()}: {event.error_message}")
            lines.append("")

        # Related signatures
        if details.related_signatures:
            lines.append(f"Related Signatures ({len(details.related_signatures)}):")
            for related_sig in details.related_signatures[:5]:  # Show first 5
                lines.append(f"  - {related_sig.id}: {related_sig.service} ({related_sig.occurrence_count} occurrences)")
            lines.append("")

        return "\n".join(lines)


    async def list_signatures(
        self, status: str | None = None, output_format: str = "json"
    ) -> dict[str, Any]:
        """List signatures via CLI.

        Args:
            status: Optional status filter ('new', 'investigating', 'diagnosed', 'resolved', 'muted').
            output_format: Output format ('json', 'text'). Default 'json'.

        Returns:
            Dictionary with status and data:
            - On success: {"status": "success", "operation": "list", "signatures": [...]}
            - On error: {"status": "error", "operation": "list", "message": str}
        """
        try:
            from rounds.core.models import SignatureStatus

            status_enum = None
            if status:
                status_enum = SignatureStatus(status.lower())

            signatures = await self.management.list_signatures(status_enum)

            if output_format == "json":
                return {
                    "status": "success",
                    "operation": "list",
                    "signatures": [
                        {
                            "id": sig.id,
                            "fingerprint": sig.fingerprint,
                            "error_type": sig.error_type,
                            "service": sig.service,
                            "status": sig.status.value,
                            "occurrence_count": sig.occurrence_count,
                            "first_seen": sig.first_seen.isoformat(),
                            "last_seen": sig.last_seen.isoformat(),
                        }
                        for sig in signatures
                    ],
                }

            elif output_format == "text":
                text_output = self._format_signatures_as_text(signatures)
                return {
                    "status": "success",
                    "operation": "list",
                    "data": text_output,
                }

            else:
                return {
                    "status": "error",
                    "operation": "list",
                    "message": f"Unsupported format: {output_format}",
                }

        except (ValueError, Exception) as e:
            logger.error(f"Failed to list signatures: {e}", exc_info=True)
            return {
                "status": "error",
                "operation": "list",
                "message": str(e),
            }

    async def reinvestigate_signature(
        self, signature_id: str, verbose: bool = False
    ) -> dict[str, Any]:
        """Reinvestigate a signature via CLI.

        Args:
            signature_id: UUID of the signature.
            verbose: If True, print additional information.

        Returns:
            Dictionary with status and data:
            - On success: {"status": "success", "operation": "reinvestigate", "signature_id": str, "diagnosis": {...}}
            - On error: {"status": "error", "operation": "reinvestigate", "signature_id": str, "message": str}
        """
        try:
            diagnosis = await self.management.reinvestigate(signature_id)

            result = {
                "status": "success",
                "operation": "reinvestigate",
                "signature_id": signature_id,
                "diagnosis": {
                    "root_cause": diagnosis.root_cause,
                    "confidence": diagnosis.confidence,
                    "suggested_fix": diagnosis.suggested_fix,
                    "cost_usd": diagnosis.cost_usd,
                    "model": diagnosis.model,
                },
            }

            if verbose:
                logger.info(
                    f"Reinvestigated signature {signature_id}",
                    extra={
                        "confidence": diagnosis.confidence,
                        "cost_usd": diagnosis.cost_usd,
                        "verbose": True,
                    },
                )

            return result

        except (ValueError, Exception) as e:
            logger.error(f"Failed to reinvestigate signature: {e}", exc_info=True)
            return {
                "status": "error",
                "operation": "reinvestigate",
                "signature_id": signature_id,
                "message": str(e),
            }

    def _format_signatures_as_text(self, signatures: Sequence[Signature]) -> str:
        """Format signatures as human-readable text.

        Args:
            signatures: Sequence of signatures.

        Returns:
            Formatted text string.
        """
        lines = []
        lines.append(f"Found {len(signatures)} signatures\n")
        lines.append("-" * 80)

        for sig in signatures:
            lines.append(f"ID:          {sig.id}")
            lines.append(f"Fingerprint: {sig.fingerprint}")
            lines.append(f"Service:     {sig.service}")
            lines.append(f"Error Type:  {sig.error_type}")
            lines.append(f"Status:      {sig.status.value}")
            lines.append(f"Occurrences: {sig.occurrence_count}")
            lines.append(f"First Seen:  {sig.first_seen.isoformat()}")
            lines.append(f"Last Seen:   {sig.last_seen.isoformat()}")
            lines.append("-" * 80)

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
        command: Command name ('mute', 'resolve', 'retriage', 'details', 'list', 'reinvestigate').
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

    elif command == "list":
        return await handler.list_signatures(
            args.get("status"),
            args.get("format", "json"),
        )

    elif command == "reinvestigate":
        return await handler.reinvestigate_signature(
            args["signature_id"],
            args.get("verbose", False),
        )

    else:
        raise ValueError(f"Unknown command: {command}")
