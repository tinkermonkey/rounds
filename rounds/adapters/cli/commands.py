"""CLI command implementations for Rounds management.

Provides human-initiated actions through command-line interface.

This adapter maps CLI commands (mute, resolve, retriage, details) to ManagementPort
operations. It handles CLI-specific formatting and error reporting.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from rounds.core.models import Signature, SignatureDetails, SpanNode, TraceInvestigation
from rounds.core.ports import ManagementPort

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


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
        with tracer.start_as_current_span(
            "cli.mute_signature",
            attributes={
                "signature_id": signature_id,
                "reason": reason or "",
                "verbose": verbose,
            },
        ) as span:
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

                span.set_status(Status(StatusCode.OK))
                span.set_attribute("result.status", "success")

                if verbose:
                    logger.info(
                        f"Muted signature {signature_id}",
                        extra={"reason": reason, "verbose": True},
                    )

                return result

            except Exception as e:
                logger.error(f"Failed to mute signature: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.set_attribute("result.status", "error")
                span.set_attribute("error.type", type(e).__name__)
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
        with tracer.start_as_current_span(
            "cli.resolve_signature",
            attributes={
                "signature_id": signature_id,
                "fix_applied": fix_applied or "",
                "verbose": verbose,
            },
        ) as span:
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

                span.set_status(Status(StatusCode.OK))
                span.set_attribute("result.status", "success")

                if verbose:
                    logger.info(
                        f"Resolved signature {signature_id}",
                        extra={"fix_applied": fix_applied, "verbose": True},
                    )

                return result

            except Exception as e:
                logger.error(f"Failed to resolve signature: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.set_attribute("result.status", "error")
                span.set_attribute("error.type", type(e).__name__)
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
        with tracer.start_as_current_span(
            "cli.retriage_signature",
            attributes={
                "signature_id": signature_id,
                "verbose": verbose,
            },
        ) as span:
            try:
                await self.management.retriage_signature(signature_id)

                result = {
                    "status": "success",
                    "operation": "retriage",
                    "signature_id": signature_id,
                    "message": f"Signature {signature_id} retriaged and queued for re-investigation",
                }

                span.set_status(Status(StatusCode.OK))
                span.set_attribute("result.status", "success")

                if verbose:
                    logger.info(
                        f"Retriaged signature {signature_id}",
                        extra={"verbose": True},
                    )

                return result

            except Exception as e:
                logger.error(f"Failed to retriage signature: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.set_attribute("result.status", "error")
                span.set_attribute("error.type", type(e).__name__)
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
        with tracer.start_as_current_span(
            "cli.get_signature_details",
            attributes={
                "signature_id": signature_id,
                "output_format": output_format,
            },
        ) as span:
            try:
                details = await self.management.get_signature_details(signature_id)

                if output_format == "json":
                    span.set_status(Status(StatusCode.OK))
                    span.set_attribute("result.status", "success")
                    return {
                        "status": "success",
                        "operation": "get_details",
                        "data": details,
                    }

                elif output_format == "text":
                    # Convert to human-readable text format
                    text_output = self._format_details_as_text(details)
                    span.set_status(Status(StatusCode.OK))
                    span.set_attribute("result.status", "success")
                    return {
                        "status": "success",
                        "operation": "get_details",
                        "data": text_output,
                    }

                else:
                    error_msg = f"Unsupported format: {output_format}"
                    span.set_status(Status(StatusCode.ERROR, error_msg))
                    span.set_attribute("result.status", "error")
                    span.set_attribute("error.type", "UnsupportedFormat")
                    return {
                        "status": "error",
                        "operation": "get_details",
                        "message": error_msg,
                    }

            except Exception as e:
                logger.error(f"Failed to get signature details: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.set_attribute("result.status", "error")
                span.set_attribute("error.type", type(e).__name__)
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
        with tracer.start_as_current_span(
            "cli.list_signatures",
            attributes={
                "status_filter": status or "all",
                "output_format": output_format,
            },
        ) as span:
            try:
                from rounds.core.models import SignatureStatus

                status_enum = None
                if status:
                    status_enum = SignatureStatus(status.lower())

                signatures = await self.management.list_signatures(status_enum)
                span.set_attribute("result.count", len(signatures))

                if output_format == "json":
                    span.set_status(Status(StatusCode.OK))
                    span.set_attribute("result.status", "success")
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
                    span.set_status(Status(StatusCode.OK))
                    span.set_attribute("result.status", "success")
                    return {
                        "status": "success",
                        "operation": "list",
                        "data": text_output,
                    }

                else:
                    error_msg = f"Unsupported format: {output_format}"
                    span.set_status(Status(StatusCode.ERROR, error_msg))
                    span.set_attribute("result.status", "error")
                    span.set_attribute("error.type", "UnsupportedFormat")
                    return {
                        "status": "error",
                        "operation": "list",
                        "message": error_msg,
                    }

            except Exception as e:
                logger.error(f"Failed to list signatures: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.set_attribute("result.status", "error")
                span.set_attribute("error.type", type(e).__name__)
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
        with tracer.start_as_current_span(
            "cli.reinvestigate_signature",
            attributes={
                "signature_id": signature_id,
                "verbose": verbose,
            },
        ) as span:
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

                span.set_status(Status(StatusCode.OK))
                span.set_attribute("result.status", "success")
                span.set_attribute("diagnosis.confidence", diagnosis.confidence)
                span.set_attribute("diagnosis.cost_usd", diagnosis.cost_usd)
                span.set_attribute("diagnosis.model", diagnosis.model)

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

            except Exception as e:
                logger.error(f"Failed to reinvestigate signature: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.set_attribute("result.status", "error")
                span.set_attribute("error.type", type(e).__name__)
                return {
                    "status": "error",
                    "operation": "reinvestigate",
                    "signature_id": signature_id,
                    "message": str(e),
                }

    async def investigate_trace(
        self, trace_id: str, verbose: bool = False
    ) -> dict[str, Any]:
        """Investigate a trace by ID via CLI.

        Fetches the full distributed trace from the telemetry backend, reads
        the relevant source files from the mounted codebase, and returns a
        step-by-step explanation of the code flow.

        Args:
            trace_id: OpenTelemetry trace ID (128-bit hex string).
            verbose: If True, log additional information.

        Returns:
            Dictionary with status and investigation data:
            - On success: {"status": "success", "operation": "investigate-trace",
                           "trace_id": str, "investigation": {...}}
            - On error: {"status": "error", "operation": "investigate-trace",
                         "trace_id": str, "message": str}
        """
        with tracer.start_as_current_span(
            "cli.investigate_trace",
            attributes={
                "trace_id": trace_id,
                "verbose": verbose,
            },
        ) as span:
            try:
                investigation = await self.management.investigate_trace(trace_id)

                result: dict[str, Any] = {
                    "status": "success",
                    "operation": "investigate-trace",
                    "trace_id": trace_id,
                    "investigation": self._format_trace_investigation(investigation),
                }

                span.set_status(Status(StatusCode.OK))
                span.set_attribute("result.status", "success")
                span.set_attribute("investigation.cost_usd", investigation.cost_usd)
                span.set_attribute("investigation.model", investigation.model)
                span.set_attribute("investigation.services_count", len(investigation.services_involved))
                span.set_attribute("investigation.key_findings_count", len(investigation.key_findings))

                if verbose:
                    logger.info(
                        f"Investigated trace {trace_id}",
                        extra={"cost_usd": investigation.cost_usd, "model": investigation.model},
                    )

                return result

            except Exception as e:
                logger.error(f"Failed to investigate trace: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.set_attribute("result.status", "error")
                span.set_attribute("error.type", type(e).__name__)
                return {
                    "status": "error",
                    "operation": "investigate-trace",
                    "trace_id": trace_id,
                    "message": str(e),
                }

    def _format_trace_investigation(self, inv: TraceInvestigation) -> dict[str, Any]:
        """Serialize a TraceInvestigation to a JSON-compatible dict."""
        return {
            "trace_id": inv.trace_id,
            "summary": inv.summary,
            "code_flow": list(inv.code_flow),
            "services_involved": list(inv.services_involved),
            "key_findings": list(inv.key_findings),
            "model": inv.model,
            "cost_usd": inv.cost_usd,
            "investigated_at": inv.investigated_at.isoformat(),
        }

    async def search_logs(
        self,
        query: str = "",
        since_minutes: int = 60,
        until_minutes: int | None = None,
        services: list[str] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search logs by keyword and optional metadata filters.

        Args:
            query: Keyword or phrase to search in log bodies. Empty matches all.
            since_minutes: Lookback window in minutes from now.
            until_minutes: If set, upper bound in minutes ago from now.
            services: Optional service name filter.
            limit: Maximum results to return.

        Returns:
            Dictionary with status and matching log entries.
        """
        with tracer.start_as_current_span(
            "cli.search_logs",
            attributes={"query": query, "since_minutes": since_minutes},
        ) as span:
            try:
                now = datetime.now(UTC)
                since = now - timedelta(minutes=since_minutes)
                until = (now - timedelta(minutes=until_minutes)) if until_minutes is not None else None

                logs = await self.management.search_logs(query, since, until, services, limit)

                result: dict[str, Any] = {
                    "status": "success",
                    "operation": "search-logs",
                    "query": query,
                    "count": len(logs),
                    "logs": [
                        {
                            "timestamp": log.timestamp.isoformat(),
                            "severity": log.severity.value,
                            "body": log.body,
                            "trace_id": log.trace_id,
                            "span_id": log.span_id,
                            "attributes": dict(log.attributes),
                        }
                        for log in logs
                    ],
                }

                span.set_status(Status(StatusCode.OK))
                span.set_attribute("result.count", len(logs))
                return result

            except Exception as e:
                logger.error(f"Failed to search logs: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                return {
                    "status": "error",
                    "operation": "search-logs",
                    "message": str(e),
                }

    async def search_spans(
        self,
        since_minutes: int = 60,
        until_minutes: int | None = None,
        services: list[str] | None = None,
        operation: str | None = None,
        has_error: bool | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search spans by metadata filters.

        Args:
            since_minutes: Lookback window in minutes from now.
            until_minutes: If set, upper bound in minutes ago from now.
            services: Optional service name filter.
            operation: Optional operation name substring filter.
            has_error: If set, filter to error or non-error spans only.
            limit: Maximum results to return.

        Returns:
            Dictionary with status and matching span summaries.
        """
        with tracer.start_as_current_span(
            "cli.search_spans",
            attributes={"since_minutes": since_minutes, "has_error": str(has_error)},
        ) as span:
            try:
                now = datetime.now(UTC)
                since = now - timedelta(minutes=since_minutes)
                until = (now - timedelta(minutes=until_minutes)) if until_minutes is not None else None

                spans = await self.management.search_spans(
                    since, until, services, operation, None, has_error, limit
                )

                result: dict[str, Any] = {
                    "status": "success",
                    "operation": "search-spans",
                    "count": len(spans),
                    "spans": [
                        {
                            "trace_id": s.trace_id,
                            "span_id": s.span_id,
                            "service": s.service,
                            "operation": s.operation,
                            "duration_ms": s.duration_ms,
                            "has_error": s.has_error,
                            "timestamp": s.timestamp.isoformat(),
                            "attributes": dict(s.attributes),
                        }
                        for s in spans
                    ],
                }

                span.set_status(Status(StatusCode.OK))
                span.set_attribute("result.count", len(spans))
                return result

            except Exception as e:
                logger.error(f"Failed to search spans: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                return {
                    "status": "error",
                    "operation": "search-spans",
                    "message": str(e),
                }

    async def get_trace_tree(self, trace_id: str) -> dict[str, Any]:
        """Fetch the full span tree for a trace without LLM analysis.

        Returns the raw span hierarchy assembled from the telemetry backend.
        Use /rounds-investigate for LLM-powered code-flow analysis.

        Args:
            trace_id: OpenTelemetry trace ID (128-bit hex string).

        Returns:
            Dictionary with status and the full span tree.
        """
        with tracer.start_as_current_span(
            "cli.get_trace_tree", attributes={"trace_id": trace_id}
        ) as span:
            try:
                tree = await self.management.get_trace_tree(trace_id)

                result: dict[str, Any] = {
                    "status": "success",
                    "operation": "get-trace-tree",
                    "trace_id": tree.trace_id,
                    "error_span_count": len(tree.error_spans),
                    "tree": self._format_span_node(tree.root_span),
                }

                span.set_status(Status(StatusCode.OK))
                span.set_attribute("error_span_count", len(tree.error_spans))
                return result

            except Exception as e:
                logger.error(f"Failed to get trace tree: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                return {
                    "status": "error",
                    "operation": "get-trace-tree",
                    "trace_id": trace_id,
                    "message": str(e),
                }

    async def list_services(self) -> dict[str, Any]:
        """List all service names visible in the telemetry backend.

        Returns:
            Dictionary with status and sorted list of service names:
            - On success: {"status": "success", "operation": "list-services",
                           "count": int, "services": [str, ...]}
            - On error: {"status": "error", "operation": "list-services",
                         "message": str}
        """
        with tracer.start_as_current_span("cli.list_services") as span:
            try:
                services = await self.management.list_services()

                result: dict[str, Any] = {
                    "status": "success",
                    "operation": "list-services",
                    "count": len(services),
                    "services": services,
                }

                span.set_status(Status(StatusCode.OK))
                span.set_attribute("result.count", len(services))
                return result

            except Exception as e:
                logger.error(f"Failed to list services: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                return {
                    "status": "error",
                    "operation": "list-services",
                    "message": str(e),
                }

    def _format_span_node(self, node: SpanNode) -> dict[str, Any]:
        """Recursively serialize a SpanNode to a JSON-compatible dict."""
        return {
            "span_id": node.span_id,
            "service": node.service,
            "operation": node.operation,
            "duration_ms": node.duration_ms,
            "status": node.status,
            "attributes": dict(node.attributes),
            "events": [dict(e) for e in node.events],
            "children": [self._format_span_node(child) for child in node.children],
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

    elif command == "investigate-trace":
        if "trace_id" not in args:
            raise ValueError("Missing required parameter: trace_id")
        return await handler.investigate_trace(
            args["trace_id"],
            args.get("verbose", False),
        )

    elif command == "search-logs":
        return await handler.search_logs(
            args.get("query", ""),
            args.get("since_minutes", 60),
            args.get("until_minutes"),
            args.get("services"),
            args.get("limit", 50),
        )

    elif command == "search-spans":
        return await handler.search_spans(
            args.get("since_minutes", 60),
            args.get("until_minutes"),
            args.get("services"),
            args.get("operation"),
            args.get("has_error"),
            args.get("limit", 50),
        )

    elif command == "get-trace-tree":
        if "trace_id" not in args:
            raise ValueError("Missing required parameter: trace_id")
        return await handler.get_trace_tree(args["trace_id"])

    else:
        raise ValueError(f"Unknown command: {command}")
