"""Composition root for the Rounds diagnostic system.

This module is the ONLY location that imports both core domain logic
and concrete adapter implementations. All wiring of dependencies
happens here, creating a clear entry point for the application.

Module Structure:
- Configuration loading via config module
- Adapter instantiation
- Core service initialization
- Dependency injection
- Entry point selection (daemon, CLI, webhook, scan, diagnose)
"""

import argparse
import asyncio
import json
import logging
import re
import sys
import urllib.parse
from typing import Any, Literal

import httpx
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pydantic import ValidationError

from rounds.adapters.cli.commands import CLICommandHandler
from rounds.adapters.diagnosis.claude_code import ClaudeCodeDiagnosisAdapter
from rounds.adapters.notification.github_issues import GitHubIssueNotificationAdapter
from rounds.adapters.notification.markdown import MarkdownNotificationAdapter
from rounds.adapters.notification.stdout import StdoutNotificationAdapter
from rounds.adapters.scheduler.daemon import DaemonScheduler
from rounds.adapters.store.sqlite import SQLiteSignatureStore
from rounds.adapters.telemetry.elasticsearch import ElasticsearchTelemetryAdapter
from rounds.adapters.telemetry.grafana_stack import GrafanaStackTelemetryAdapter
from rounds.adapters.telemetry.jaeger import JaegerTelemetryAdapter
from rounds.adapters.telemetry.signoz import SigNozTelemetryAdapter
from rounds.adapters.webhook.http_server import WebhookHTTPServer
from rounds.adapters.webhook.receiver import WebhookReceiver
from rounds.config import load_settings
from rounds.core.fingerprint import Fingerprinter
from rounds.core.investigator import Investigator
from rounds.core.management_service import ManagementService
from rounds.core.poll_service import PollService
from rounds.core.ports import DiagnosisPort, NotificationPort, SignatureStorePort, TelemetryPort
from rounds.core.triage import TriageEngine

# Only hex chars and hyphens are valid in a trace ID.
# Prevents path traversal and URL/shell injection when the value is forwarded
# to the telemetry backend as part of an HTTP request path.
_TRACE_ID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def _validate_trace_id(raw: str) -> str:
    """Return the trace ID if it is safe, otherwise raise ValueError.

    Accepts the 32-char lowercase hex format used by OpenTelemetry and the
    hyphenated UUID format (36 chars). Rejects anything else to prevent
    URL injection when the value is forwarded to the telemetry API.

    Raises:
        ValueError: If the string contains characters outside [0-9a-fA-F-]
            or is shorter than 8 or longer than 64 characters.
    """
    trace_id = raw.strip()
    if not _TRACE_ID_RE.match(trace_id):
        raise ValueError(
            f"Invalid trace ID {trace_id!r}: must be 8-64 hex characters "
            "(0-9, a-f, A-F) with optional hyphens."
        )
    return trace_id


async def _run_cli_interactive(cli_handler: CLICommandHandler) -> None:
    """Run interactive CLI loop.

    Provides a REPL-like interface for management commands.

    Args:
        cli_handler: CLICommandHandler instance for executing commands.
    """
    logger = logging.getLogger(__name__)
    logger.info("Starting interactive CLI. Type 'help' for available commands or 'exit' to quit.")

    loop = asyncio.get_running_loop()

    while True:
        try:
            # Read command from stdin in a thread to avoid blocking
            command_line = await loop.run_in_executor(
                None,
                input,
                "rounds> "
            )

            command_line = command_line.strip()

            if not command_line:
                continue

            if command_line.lower() == "exit":
                logger.info("Exiting CLI")
                break

            if command_line.lower() == "help":
                _print_cli_help()
                continue

            # Parse command and arguments
            parts = command_line.split(maxsplit=1)
            if not parts:
                continue

            command = parts[0].lower()
            args_str = parts[1] if len(parts) > 1 else ""

            # Try to parse arguments as JSON.
            # For `investigate-trace`, also accept a bare trace ID string so
            # the user can type: investigate-trace abc123def456...
            # instead of: investigate-trace {"trace_id": "abc123def456..."}
            try:
                if args_str:
                    args = json.loads(args_str)
                else:
                    args = {}
            except json.JSONDecodeError as e:
                if command == "investigate-trace" and args_str:
                    try:
                        args = {"trace_id": _validate_trace_id(args_str)}
                    except ValueError as ve:
                        print(json.dumps({"status": "error", "message": str(ve)}, indent=2))
                        continue
                else:
                    logger.error(f"Invalid JSON arguments: {e}. Input: {args_str!r}")
                    continue

            # Execute command
            try:
                result = await _execute_cli_command(cli_handler, command, args)
                # Print result
                print(json.dumps(result, indent=2, default=str))
            except (MemoryError, SystemError, SystemExit):
                # Re-raise critical system errors to outer handler
                raise
            except Exception as e:
                # Catch all other exceptions to keep CLI alive
                # Interactive CLI should survive individual command failures
                logger.error(f"Command execution error: {e}", exc_info=True)
                print(json.dumps({
                    "status": "error",
                    "message": str(e)
                }, indent=2))

        except EOFError:
            # Ctrl+D to exit
            logger.info("EOF received, exiting CLI")
            break
        except KeyboardInterrupt:
            # Ctrl+C
            logger.info("Interrupted by user")
            continue
        except (MemoryError, SystemError, SystemExit) as e:
            # Re-raise critical system errors
            logger.error(f"Critical system error: {e}", exc_info=True)
            raise
        except Exception as e:
            # Catch all other exceptions to keep CLI alive
            # Interactive CLI should survive input parsing and prompt errors
            logger.error(f"CLI error: {e}", exc_info=True)
            print(json.dumps({
                "status": "error",
                "message": str(e)
            }, indent=2))


async def _execute_cli_command(
    cli_handler: CLICommandHandler,
    command: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Execute a CLI command.

    Command-specific argument requirements:
    - 'list': Optional status (str), format (str, default "json")
    - 'details': Required signature_id (str), format (str, default "json")
    - 'mute': Required signature_id (str), optional reason (str), verbose (bool)
    - 'resolve': Required signature_id (str), optional fix_applied (str), verbose (bool)
    - 'retriage': Required signature_id (str), optional verbose (bool)
    - 'reinvestigate': Required signature_id (str), optional verbose (bool)
    - 'investigate-trace': Required trace_id (str), optional verbose (bool)

    Args:
        cli_handler: CLICommandHandler instance.
        command: Command name (list, details, mute, resolve, retriage, reinvestigate).
        args: Command arguments dictionary. Structure depends on command type.

    Returns:
        Command result dictionary with status and data.

    Raises:
        ValueError: If command is unknown or required parameters are missing.
    """
    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span(
        f"rounds.cli.command.{command}",
        attributes={"command": command},
    ) as span:
        try:
            if command == "list":
                result = await cli_handler.list_signatures(
                    status=args.get("status"),
                    output_format=args.get("format", "json"),
                )

            elif command == "details":
                if "signature_id" not in args:
                    raise ValueError("Missing required parameter: signature_id")
                span.set_attribute("signature_id", args["signature_id"])
                result = await cli_handler.get_signature_details(
                    signature_id=args["signature_id"],
                    output_format=args.get("format", "json"),
                )

            elif command == "mute":
                if "signature_id" not in args:
                    raise ValueError("Missing required parameter: signature_id")
                span.set_attribute("signature_id", args["signature_id"])
                result = await cli_handler.mute_signature(
                    signature_id=args["signature_id"],
                    reason=args.get("reason"),
                    verbose=args.get("verbose", False),
                )

            elif command == "resolve":
                if "signature_id" not in args:
                    raise ValueError("Missing required parameter: signature_id")
                span.set_attribute("signature_id", args["signature_id"])
                result = await cli_handler.resolve_signature(
                    signature_id=args["signature_id"],
                    fix_applied=args.get("fix_applied"),
                    verbose=args.get("verbose", False),
                )

            elif command == "retriage":
                if "signature_id" not in args:
                    raise ValueError("Missing required parameter: signature_id")
                span.set_attribute("signature_id", args["signature_id"])
                result = await cli_handler.retriage_signature(
                    signature_id=args["signature_id"],
                    verbose=args.get("verbose", False),
                )

            elif command == "reinvestigate":
                if "signature_id" not in args:
                    raise ValueError("Missing required parameter: signature_id")
                span.set_attribute("signature_id", args["signature_id"])
                result = await cli_handler.reinvestigate_signature(
                    signature_id=args["signature_id"],
                    verbose=args.get("verbose", False),
                )

            elif command == "investigate-trace":
                if "trace_id" not in args:
                    raise ValueError("Missing required parameter: trace_id")
                span.set_attribute("trace_id", args["trace_id"])
                result = await cli_handler.investigate_trace(
                    trace_id=args["trace_id"],
                    verbose=args.get("verbose", False),
                )

            elif command == "search-logs":
                result = await cli_handler.search_logs(
                    query=args.get("query", ""),
                    since_minutes=args.get("since_minutes", 60),
                    until_minutes=args.get("until_minutes"),
                    services=args.get("services"),
                    limit=args.get("limit", 50),
                )

            elif command == "search-spans":
                result = await cli_handler.search_spans(
                    since_minutes=args.get("since_minutes", 60),
                    until_minutes=args.get("until_minutes"),
                    services=args.get("services"),
                    operation=args.get("operation"),
                    has_error=args.get("has_error"),
                    limit=args.get("limit", 50),
                )

            elif command == "get-trace-tree":
                if "trace_id" not in args:
                    raise ValueError("Missing required parameter: trace_id")
                span.set_attribute("trace_id", args["trace_id"])
                result = await cli_handler.get_trace_tree(args["trace_id"])

            elif command == "list-services":
                result = await cli_handler.list_services()

            else:
                error_msg = f"Unknown command: {command}. Use 'help' for available commands."
                span.set_status(Status(StatusCode.ERROR, error_msg))
                span.set_attribute("error.type", "UnknownCommand")
                raise ValueError(error_msg)

            # Set span status based on result
            span.set_status(Status(StatusCode.OK))
            span.set_attribute("result.status", result.get("status", "unknown"))
            return result

        except ValueError as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.set_attribute("error.type", "ValueError")
            raise
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.set_attribute("error.type", type(e).__name__)
            raise


async def _run_scan(poll_service: PollService) -> None:
    """Execute single poll cycle and output results as JSON.

    Calls sys.exit(1) on error and never returns normally if scan fails.

    Args:
        poll_service: PollService instance for executing the poll cycle.

    Returns:
        None: Returns normally on success (no explicit return value).

    Raises:
        SystemExit: Calls sys.exit(1) on any error (does not raise, but terminates process).
    """
    logger = logging.getLogger(__name__)
    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span("rounds.scan") as span:
        try:
            # Execute single poll cycle
            result = await poll_service.execute_poll_cycle()

            # Output JSON to stdout
            output = {
                "status": "success",
                "new_signatures": result.new_signatures,
                "updated_signatures": result.updated_signatures,
                "errors_processed": result.errors_found,
                "errors_failed": result.errors_failed_to_process,
                "investigations_queued": result.investigations_queued,
                "timestamp": result.timestamp.isoformat(),
            }

            span.set_status(Status(StatusCode.OK))
            span.set_attribute("result.new_signatures", result.new_signatures)
            span.set_attribute("result.updated_signatures", result.updated_signatures)
            span.set_attribute("result.errors_processed", result.errors_found)
            span.set_attribute("result.errors_failed", result.errors_failed_to_process)
            span.set_attribute("result.investigations_queued", result.investigations_queued)

            print(json.dumps(output, indent=2))

        except ConnectionError as e:
            # Telemetry backend unreachable
            logger.error(f"Scan command failed: telemetry service unreachable: {e}", exc_info=True)
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.set_attribute("error.type", "ConnectionError")
            output = {
                "status": "error",
                "error_type": "connection_error",
                "message": f"Telemetry service unreachable: {e!s}"
            }
            print(json.dumps(output, indent=2), file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            logger.error(f"Scan command failed: {e}", exc_info=True)
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.set_attribute("error.type", type(e).__name__)
            output = {"status": "error", "message": str(e)}
            print(json.dumps(output, indent=2), file=sys.stderr)
            sys.exit(1)


async def _run_cli_once(
    cli_handler: CLICommandHandler,
    command: str,
    args_str: str,
) -> None:
    """Execute a single CLI command non-interactively, print JSON result, and return.

    Used by the ``cli-run`` invocation mode so skills and scripts can call rounds
    without starting an interactive REPL.

    Args:
        cli_handler: CLICommandHandler instance for executing commands.
        command: CLI sub-command name (investigate-trace, list, details, …).
        args_str: Argument string — either a JSON object or a bare trace ID.
    """
    logger = logging.getLogger(__name__)

    # Parse args using the same logic as the interactive CLI.
    try:
        args: dict[str, Any] = json.loads(args_str) if args_str else {}
    except json.JSONDecodeError:
        if command == "investigate-trace" and args_str:
            try:
                args = {"trace_id": _validate_trace_id(args_str)}
            except ValueError as ve:
                print(json.dumps({"status": "error", "message": str(ve)}, indent=2))
                sys.exit(1)
        else:
            print(
                json.dumps(
                    {"status": "error", "message": f"Invalid JSON arguments: {args_str!r}"},
                    indent=2,
                ),
                file=sys.stderr,
            )
            sys.exit(1)

    try:
        result = await _execute_cli_command(cli_handler, command, args)
        print(json.dumps(result, indent=2, default=str))
    except ValueError as e:
        print(json.dumps({"status": "error", "message": str(e)}, indent=2), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.error(f"cli-run failed: {e}", exc_info=True)
        print(json.dumps({"status": "error", "message": str(e)}, indent=2), file=sys.stderr)
        sys.exit(1)


async def _run_diagnose(
    signature_id: str,
    store: SignatureStorePort,
    investigator: Investigator,
) -> None:
    """Diagnose a specific signature and output results as JSON.

    Calls sys.exit(1) on error and never returns normally if diagnosis fails.

    Args:
        signature_id: Unique identifier string of the signature to diagnose (e.g., "sig_12345").
        store: SignatureStorePort implementation.
        investigator: Investigator instance.

    Raises:
        SystemExit: Calls sys.exit(1) on any error (does not raise, but terminates process).
    """
    logger = logging.getLogger(__name__)
    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span(
        "rounds.diagnose",
        attributes={"signature_id": signature_id},
    ) as span:
        try:
            # Retrieve signature
            signature = await store.get_by_id(signature_id)
            if not signature:
                error_msg = f"Signature not found: {signature_id}"
                span.set_status(Status(StatusCode.ERROR, error_msg))
                span.set_attribute("error.type", "SignatureNotFound")
                raise ValueError(error_msg)

            span.set_attribute("signature.service", signature.service)
            span.set_attribute("signature.error_type", signature.error_type)
            span.set_attribute("signature.fingerprint", signature.fingerprint)

            # Investigate
            diagnosis = await investigator.investigate(signature)

            # Output JSON to stdout
            output = {
                "status": "success",
                "signature_id": signature_id,
                "root_cause": diagnosis.root_cause,
                "confidence": diagnosis.confidence,
                "cost_usd": diagnosis.cost_usd,
                "diagnosed_at": diagnosis.diagnosed_at.isoformat(),
                "model": diagnosis.model,
            }

            span.set_status(Status(StatusCode.OK))
            span.set_attribute("diagnosis.confidence", diagnosis.confidence)
            span.set_attribute("diagnosis.cost_usd", diagnosis.cost_usd)
            span.set_attribute("diagnosis.model", diagnosis.model)

            print(json.dumps(output, indent=2))

        except Exception as e:
            logger.error(f"Diagnose command failed: {e}", exc_info=True)
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.set_attribute("error.type", type(e).__name__)
            output = {"status": "error", "message": str(e)}
            print(json.dumps(output, indent=2), file=sys.stderr)
            sys.exit(1)


def _print_cli_help() -> None:
    """Print CLI help message."""
    help_text = """
Available Commands (JSON format):

  list
    List all signatures, optionally filtered by status.
    Status options: new, investigating, diagnosed, resolved, muted

    Example: list {"status": "new", "format": "text"}

  details
    Get detailed information about a signature.
    Required: signature_id

    Example: details {"signature_id": "uuid-here"}

  mute
    Mute a signature to stop notifications.
    Required: signature_id
    Optional: reason

    Example: mute {"signature_id": "uuid-here", "reason": "false positive"}

  resolve
    Mark a signature as resolved.
    Required: signature_id
    Optional: fix_applied

    Example: resolve {"signature_id": "uuid-here", "fix_applied": "deployed fix"}

  retriage
    Re-evaluate a signature's triage status.
    Required: signature_id

    Example: retriage {"signature_id": "uuid-here"}

  reinvestigate
    Request a new diagnosis for a signature.
    Required: signature_id

    Example: reinvestigate {"signature_id": "uuid-here"}

  investigate-trace
    Fetch a trace by ID and explain the end-to-end code flow.
    Reads the mounted codebase to cite actual file:line locations.
    Required: trace_id (hex string)

    Examples:
      investigate-trace abcdef1234567890abcdef1234567890
      investigate-trace {"trace_id": "abcdef1234567890abcdef1234567890"}

  list-services
    List all service names visible in the telemetry backend.
    Returns exact, case-sensitive names as reported by the backend.

    Example: list-services

  help
    Show this help message.

  exit
    Exit the CLI.

Note: All commands accept arguments as a single JSON object.
Provide the JSON after the command name on the same line.
    """
    print(help_text)


def configure_logging(log_level: str, log_format: str) -> None:
    """Configure application logging.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_format: Log format (json, text).
    """
    # Map string level to logging constant
    level = getattr(logging, log_level, logging.INFO)

    if log_format == "json":
        format_str = '{"time": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}'
    else:
        format_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    logging.basicConfig(
        level=level,
        format=format_str,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


def _parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed arguments containing command and optional rest args.
    """
    parser = argparse.ArgumentParser(
        description="Rounds continuous error diagnosis system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m rounds.main                                         # Start interactive CLI
  python -m rounds.main scan                                    # Execute single poll cycle
  python -m rounds.main diagnose sig_12345                      # Diagnose specific signature
  python -m rounds.main cli-run list                            # List all signatures
  python -m rounds.main cli-run investigate-trace TRACE_ID      # Investigate a trace
  python -m rounds.main cli-run details '{"signature_id":"X"}'  # Get signature details
        """,
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["scan", "diagnose", "cli-run"],
        help="Non-interactive command to execute",
    )
    parser.add_argument(
        "rest",
        nargs="*",
        help="Additional arguments (signature_id for diagnose; sub-command and args for cli-run)",
    )
    args = parser.parse_args()
    args.signature_id = args.rest[0] if args.command == "diagnose" and args.rest else None
    return args


async def _verify_signoz_connection(adapter: "SigNozTelemetryAdapter") -> None:
    """Verify SigNoz connectivity; raise on 401/403 auth failures, warn on transient errors."""
    _logger = logging.getLogger(__name__)
    try:
        await adapter.verify_connection()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            _logger.error(
                "SigNoz authentication failed at startup — "
                "check SIGNOZ_API_KEY and restart"
            )
            raise
        _logger.warning(
            "SigNoz connectivity check failed at startup — "
            "proceeding, errors will surface on first poll cycle"
        )
    except Exception:
        _logger.warning(
            "SigNoz connectivity check failed at startup — "
            "proceeding, errors will surface on first poll cycle"
        )


async def bootstrap(
    command: Literal["scan", "diagnose", "cli-run"] | None = None,
    signature_id: str | None = None,
    cli_subcommand: str | None = None,
    cli_args_str: str = "",
) -> None:
    """Load configuration, wire adapters, and start the application.

    This is the composition root: the single place where all components
    are instantiated and wired together.

    Steps:
    1. Load configuration from environment
    2. Configure logging
    3. Instantiate adapters with configuration
    4. Initialize core services
    5. Select and start run mode

    Args:
        command: Optional command to execute (scan, diagnose, or cli-run).
        signature_id: Signature ID for diagnose command.
        cli_subcommand: CLI sub-command for cli-run mode (e.g. "investigate-trace").
        cli_args_str: Arguments string for cli-run sub-command (JSON or bare value).

    Design note: The invariant "diagnose requires signature_id" is enforced at runtime
    (validation in _run_diagnose function) rather than via type system (e.g., overloads
    or discriminated union). This keeps the API simple for callers and avoids requiring
    complex type narrowing at all call sites. The runtime check provides clear error messages.

    Raises:
        SystemExit: On fatal errors (configuration, adapter initialization)
        asyncio.CancelledError: On graceful shutdown signal
    """
    # Step 1: Load configuration
    try:
        settings = load_settings()
    except (ValueError, ValidationError) as e:
        # Sanitize error message to avoid leaking sensitive values
        import re
        error_msg = str(e)

        # Redact common API key patterns (e.g., sk-*, ghp-*, Bearer *, etc.)
        # Note: We don't redact generic hex strings to preserve UUIDs and trace IDs
        patterns = [
            (r'sk-[a-zA-Z0-9_-]{20,}', '[REDACTED_OPENAI_KEY]'),
            (r'ghp_[a-zA-Z0-9]{36,}', '[REDACTED_GITHUB_TOKEN]'),
            (r'Bearer\s+[a-zA-Z0-9_\-\.=]+', 'Bearer [REDACTED]'),
            (r'[A-Za-z0-9+/]{40,}={0,2}', '[REDACTED_BASE64]'),  # Base64 encoded secrets
        ]

        sanitized_msg = error_msg
        for pattern, replacement in patterns:
            sanitized_msg = re.sub(pattern, replacement, sanitized_msg)

        # Also redact any environment variable values from error messages
        # by removing quoted values that might contain secrets
        sanitized_msg = re.sub(r"'[^']{20,}'", "'[REDACTED]'", sanitized_msg)

        print("ERROR: Configuration validation failed.")
        print(f"Details: {sanitized_msg}")
        print("Please check your .env.rounds file and ensure all required variables are set.")
        print("See .env.rounds.template for required configuration options.")
        sys.exit(1)

    # Step 2: Configure logging
    configure_logging(settings.log_level, settings.log_format)
    logger = logging.getLogger(__name__)
    logger.info("Loading Rounds diagnostic system...")

    # Step 2.5: Initialize telemetry if enabled
    if settings.enable_self_telemetry:
        from rounds.telemetry import initialize_telemetry

        initialize_telemetry(
            service_name=settings.self_telemetry_service_name,
            otlp_endpoint=settings.self_telemetry_otlp_endpoint or None,
            enable_console_export=settings.self_telemetry_console_export,
        )
        logger.info("Self-telemetry enabled")
    else:
        # Use a no-op tracer if telemetry is disabled
        trace.get_tracer(__name__)
        logger.debug("Self-telemetry disabled")

    # Step 3: Instantiate adapters
    logger.info("Initializing adapters...")

    # Telemetry adapter - select based on config
    telemetry: TelemetryPort
    if settings.telemetry_backend == "signoz":
        _signoz = SigNozTelemetryAdapter(
            api_url=settings.signoz_api_url,
            api_key=settings.signoz_api_key,
        )
        logger.info("Telemetry adapter: SigNoz")
        await _verify_signoz_connection(_signoz)
        telemetry = _signoz
    elif settings.telemetry_backend == "jaeger":
        telemetry = JaegerTelemetryAdapter(
            api_url=settings.jaeger_api_url,
        )
        logger.info("Telemetry adapter: Jaeger")
    elif settings.telemetry_backend == "grafana_stack":
        telemetry = GrafanaStackTelemetryAdapter(
            tempo_url=settings.grafana_tempo_url,
            loki_url=settings.grafana_loki_url,
            prometheus_url=settings.grafana_prometheus_url,
        )
        logger.info("Telemetry adapter: Grafana Stack")
    elif settings.telemetry_backend == "elasticsearch":
        telemetry = ElasticsearchTelemetryAdapter(
            es_url=settings.es_url,
            api_key=settings.es_api_key.get_secret_value(),
            username=settings.es_username,
            password=settings.es_password.get_secret_value(),
            traces_index=settings.es_traces_index,
            logs_index=settings.es_logs_index,
        )
        logger.info(f"Telemetry adapter: Elasticsearch ({settings.es_url})")
    else:
        logger.error(f"Unknown telemetry backend: {settings.telemetry_backend}")
        sys.exit(1)

    # Signature store - select based on config
    store: SignatureStorePort
    if settings.store_backend == "sqlite":
        store = SQLiteSignatureStore(
            db_path=settings.store_sqlite_path,
        )
        logger.info(f"Signature store initialized: {settings.store_sqlite_path}")
    elif settings.store_backend == "postgresql":
        # Lazy import for optional PostgreSQL dependency
        from rounds.adapters.store.postgresql import PostgreSQLSignatureStore

        # Parse PostgreSQL connection URL or use individual parameters
        if settings.store_postgresql_url:
            # Parse connection URL (postgresql://user:password@host:port/database)
            parsed = urllib.parse.urlparse(settings.store_postgresql_url)
            store = PostgreSQLSignatureStore(
                host=parsed.hostname or "localhost",
                port=parsed.port or 5432,
                database=parsed.path.lstrip("/") or "rounds",
                user=parsed.username or "rounds",
                password=parsed.password or "",
            )
        else:
            # Use environment variable defaults from config
            store = PostgreSQLSignatureStore()
        logger.info("Signature store initialized: PostgreSQL")
    else:
        logger.error(f"Unknown store backend: {settings.store_backend}")
        sys.exit(1)

    # Diagnosis adapter - select based on config
    diagnosis_engine: DiagnosisPort
    if settings.diagnosis_backend == "claude_code":
        auth_method = "OAuth token" if settings.claude_code_oauth_token else "API key"
        diagnosis_engine = ClaudeCodeDiagnosisAdapter(
            model=settings.claude_model,
            budget_usd=settings.claude_code_budget_usd,
            api_key=settings.anthropic_api_key,
            oauth_token=settings.claude_code_oauth_token,
        )
        logger.info(f"Diagnosis adapter: Claude Code ({auth_method})")
    elif settings.diagnosis_backend == "openai":
        # Lazy import for optional OpenAI dependency
        from rounds.adapters.diagnosis.openai import OpenAIDiagnosisAdapter

        if not settings.openai_api_key:
            logger.error("OpenAI backend selected but OPENAI_API_KEY not set")
            sys.exit(1)
        diagnosis_engine = OpenAIDiagnosisAdapter(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            budget_usd=settings.openai_budget_usd,
        )
        logger.info("Diagnosis adapter: OpenAI")
    else:
        logger.error(f"Unknown diagnosis backend: {settings.diagnosis_backend}")
        sys.exit(1)

    # Notification adapter - select based on config
    notification: NotificationPort
    if settings.notification_backend == "stdout":
        notification = StdoutNotificationAdapter(verbose=settings.debug)
        logger.info("Notification adapter: Stdout")
    elif settings.notification_backend == "markdown":
        notification = MarkdownNotificationAdapter(report_dir=settings.notification_output_dir)
        logger.info("Notification adapter: Markdown")
    elif settings.notification_backend == "github_issue":
        notification = GitHubIssueNotificationAdapter(
            repo_owner=settings.github_repo_owner,
            repo_name=settings.github_repo_name,
            github_token=settings.github_token
        )
        logger.info("Notification adapter: GitHub Issue")
    else:
        logger.error(f"Unknown notification backend: {settings.notification_backend}")
        sys.exit(1)

    # Step 4: Initialize core services
    logger.info("Initializing core services...")

    # Domain logic components
    fingerprinter = Fingerprinter()
    triage = TriageEngine()

    # Create daemon scheduler first (needed for budget tracking in investigator)
    scheduler: DaemonScheduler | None = None
    if settings.run_mode == "daemon":
        scheduler = DaemonScheduler(
            poll_port=None,  # Will be set after poll_service is created
            poll_interval_seconds=settings.poll_interval_seconds,
            budget_limit=settings.daily_budget_limit,
        )

    # Investigator (orchestrates investigation workflow)
    investigator = Investigator(
        telemetry=telemetry,
        store=store,
        diagnosis_engine=diagnosis_engine,
        notification=notification,
        triage=triage,
        codebase_path=settings.codebase_path,
        budget_tracker=scheduler,
    )

    # Resolve service filter: None means all services, non-empty list filters to named services
    service_names = settings.get_service_names()
    service_filter: list[str] | None = service_names if service_names else None
    if service_filter:
        logger.info(f"Service filter active: {service_filter}")
    else:
        logger.info("Service filter: all services")

    # Poll service (implements PollPort)
    poll_service = PollService(
        telemetry=telemetry,
        store=store,
        fingerprinter=fingerprinter,
        triage=triage,
        investigator=investigator,
        lookback_minutes=settings.error_lookback_minutes,
        services=service_filter,
        batch_size=settings.poll_batch_size,
    )

    # Set poll_port in scheduler if it was created
    if scheduler is not None:
        scheduler.poll_port = poll_service

    # Management service (implements ManagementPort for CLI/webhook)
    management_service = ManagementService(
        store=store,
        telemetry=telemetry,
        diagnosis_engine=diagnosis_engine,
        notification=notification,
        triage=triage,
        codebase_path=settings.codebase_path,
    )

    # Step 5: Handle non-interactive commands or select run mode
    try:
        # Check for non-interactive commands first
        if command == "scan":
            logger.info("Executing scan command...")
            await _run_scan(poll_service=poll_service)

        elif command == "diagnose":
            if not signature_id:
                logger.error("diagnose command requires signature_id argument")
                output = {
                    "status": "error",
                    "message": "diagnose command requires signature_id argument",
                }
                print(json.dumps(output, indent=2), file=sys.stderr)
                sys.exit(1)
            logger.info(f"Executing diagnose command for signature {signature_id}...")
            await _run_diagnose(
                signature_id=signature_id,
                store=store,
                investigator=investigator,
            )

        elif command == "cli-run":
            if not cli_subcommand:
                output = {"status": "error", "message": "cli-run requires a sub-command argument"}
                print(json.dumps(output, indent=2), file=sys.stderr)
                sys.exit(1)
            logger.info(f"Executing cli-run: {cli_subcommand} {cli_args_str!r}")
            cli_handler = CLICommandHandler(management_service)
            await _run_cli_once(cli_handler, cli_subcommand, cli_args_str)

        else:
            # No non-interactive command, use run_mode
            logger.info(f"Starting in {settings.run_mode} mode...")

            if settings.run_mode == "daemon":
                # Start daemon polling loop
                assert scheduler is not None
                await scheduler.start()

            elif settings.run_mode == "cli":
                # CLI mode handles interactive commands via CLICommandHandler
                logger.info("CLI mode - Ready for interactive commands")
                # Create CLI command handler with the management service
                cli_handler = CLICommandHandler(management_service)
                # Run interactive CLI loop
                await _run_cli_interactive(cli_handler)

            elif settings.run_mode == "webhook":
                # Webhook mode starts an HTTP server for external triggers
                logger.info("Starting in webhook mode")

                # Create webhook receiver
                webhook_receiver = WebhookReceiver(
                    poll_port=poll_service,
                    management_port=management_service,
                    host=settings.webhook_host,
                    port=settings.webhook_port,
                )

                # Start HTTP server
                http_server = WebhookHTTPServer(
                    webhook_receiver=webhook_receiver,
                    host=settings.webhook_host,
                    port=settings.webhook_port,
                    api_key=settings.webhook_api_key if settings.webhook_api_key else None,
                    require_auth=settings.webhook_require_auth,
                )
                await http_server.start()

                # Keep the server running
                try:
                    while True:
                        await asyncio.sleep(1)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    await http_server.stop()

            else:
                logger.error(f"Unknown run mode: {settings.run_mode}")
                sys.exit(1)

    finally:
        # Clean up resources with individual error handling
        # Collect critical errors to re-raise after all cleanup attempts
        cleanup_critical_error: BaseException | None = None

        try:
            await telemetry.close()
        except (SystemExit, KeyboardInterrupt, MemoryError, SystemError) as e:
            # Capture critical error but continue cleanup
            logger.critical(f"Critical error during telemetry cleanup: {e}", exc_info=True)
            if cleanup_critical_error is None:
                cleanup_critical_error = e
        except Exception:
            logger.error("Failed to close telemetry adapter", exc_info=True)

        try:
            await store.close_pool()
        except (SystemExit, KeyboardInterrupt, MemoryError, SystemError) as e:
            # Capture critical error but continue cleanup
            logger.critical(f"Critical error during store cleanup: {e}", exc_info=True)
            if cleanup_critical_error is None:
                cleanup_critical_error = e
        except Exception:
            logger.error("Failed to close signature store", exc_info=True)

        try:
            await notification.close()
        except (SystemExit, KeyboardInterrupt, MemoryError, SystemError) as e:
            # Capture critical error but continue cleanup
            logger.critical(f"Critical error during notification cleanup: {e}", exc_info=True)
            if cleanup_critical_error is None:
                cleanup_critical_error = e
        except Exception:
            logger.error("Failed to close notification adapter", exc_info=True)

        # Shutdown self-telemetry last to capture all cleanup operations
        if settings.enable_self_telemetry:
            try:
                from rounds.telemetry import shutdown_telemetry

                shutdown_telemetry()
            except Exception:
                logger.error("Failed to shutdown self-telemetry", exc_info=True)

        # Re-raise first critical error after all cleanup attempts
        if cleanup_critical_error is not None:
            raise cleanup_critical_error


def main() -> None:
    """Application entry point.

    Loads configuration, wires adapters, initializes core services,
    and starts the appropriate run mode (daemon, CLI, or webhook).
    Alternatively executes non-interactive commands (scan, diagnose).

    Exit codes:
        0: Successful shutdown
        1: Fatal bootstrap or runtime error
        130: Interrupted by user (SIGINT/KeyboardInterrupt)
    """
    logger = logging.getLogger(__name__)
    try:
        # Parse command-line arguments
        args = _parse_arguments()

        # Resolve per-command arguments early to fail fast
        signature_id: str | None = None
        cli_subcommand: str | None = None
        cli_args_str: str = ""

        if args.command == "diagnose":
            if not args.rest:
                print("ERROR: diagnose command requires signature_id argument", file=sys.stderr)
                print("Usage: python -m rounds.main diagnose SIGNATURE_ID", file=sys.stderr)
                sys.exit(1)
            signature_id = args.rest[0]
        elif args.command == "cli-run":
            if not args.rest:
                print("ERROR: cli-run requires a sub-command argument", file=sys.stderr)
                print("Usage: python -m rounds.main cli-run COMMAND [ARGS]", file=sys.stderr)
                sys.exit(1)
            cli_subcommand = args.rest[0]
            cli_args_str = args.rest[1] if len(args.rest) > 1 else ""

        asyncio.run(
            bootstrap(
                command=args.command,
                signature_id=signature_id,
                cli_subcommand=cli_subcommand,
                cli_args_str=cli_args_str,
            )
        )
    except KeyboardInterrupt:
        logger.warning("Shutdown requested by user (SIGINT)")
        sys.exit(130)
    except asyncio.CancelledError:
        logger.info("Graceful shutdown completed")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
