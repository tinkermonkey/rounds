"""Composition root for the Rounds diagnostic system.

This module is the ONLY location that imports both core domain logic
and concrete adapter implementations. All wiring of dependencies
happens here, creating a clear entry point for the application.

Module Structure:
- Configuration loading via config module
- Adapter instantiation
- Core service initialization
- Dependency injection
- Entry point selection (daemon, CLI, webhook, etc.)
"""

import asyncio
import logging
import sys
import urllib.parse
from typing import Any

from rounds.adapters.cli.commands import CLICommandHandler
from rounds.adapters.diagnosis.claude_code import ClaudeCodeDiagnosisAdapter
from rounds.adapters.notification.markdown import MarkdownNotificationAdapter
from rounds.adapters.notification.github_issues import GitHubIssueNotificationAdapter
from rounds.adapters.notification.stdout import StdoutNotificationAdapter
from rounds.adapters.scheduler.daemon import DaemonScheduler
from rounds.adapters.store.sqlite import SQLiteSignatureStore
from rounds.adapters.telemetry.jaeger import JaegerTelemetryAdapter
from rounds.adapters.telemetry.grafana_stack import GrafanaStackTelemetryAdapter
from rounds.adapters.telemetry.signoz import SigNozTelemetryAdapter
from rounds.adapters.webhook.receiver import WebhookReceiver
from rounds.adapters.webhook.http_server import WebhookHTTPServer
from rounds.config import load_settings
from rounds.core.fingerprint import Fingerprinter
from rounds.core.investigator import Investigator
from rounds.core.management_service import ManagementService
from rounds.core.poll_service import PollService
from rounds.core.triage import TriageEngine


async def _run_cli_interactive(cli_handler: CLICommandHandler) -> None:
    """Run interactive CLI loop.

    Provides a REPL-like interface for management commands.

    Args:
        cli_handler: CLICommandHandler instance for executing commands.
    """
    import json

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

            # Try to parse arguments as JSON
            try:
                if args_str:
                    args = json.loads(args_str)
                else:
                    args = {}
            except json.JSONDecodeError:
                logger.error("Invalid JSON arguments. Use 'help' for command syntax.")
                continue

            # Execute command
            try:
                result = await _execute_cli_command(cli_handler, command, args)
                # Print result
                print(json.dumps(result, indent=2, default=str))
            except Exception as e:
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
        except Exception as e:
            logger.error(f"CLI error: {e}", exc_info=True)


async def _execute_cli_command(
    cli_handler: CLICommandHandler,
    command: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Execute a CLI command.

    Args:
        cli_handler: CLICommandHandler instance.
        command: Command name.
        args: Command arguments.

    Returns:
        Command result dictionary.

    Raises:
        ValueError: If command is not recognized.
    """
    if command == "list":
        return await cli_handler.list_signatures(
            status=args.get("status"),
            output_format=args.get("format", "json"),
        )

    elif command == "details":
        if "signature_id" not in args:
            raise ValueError("Missing required parameter: signature_id")
        return await cli_handler.get_signature_details(
            signature_id=args["signature_id"],
            output_format=args.get("format", "json"),
        )

    elif command == "mute":
        if "signature_id" not in args:
            raise ValueError("Missing required parameter: signature_id")
        return await cli_handler.mute_signature(
            signature_id=args["signature_id"],
            reason=args.get("reason"),
            verbose=args.get("verbose", False),
        )

    elif command == "resolve":
        if "signature_id" not in args:
            raise ValueError("Missing required parameter: signature_id")
        return await cli_handler.resolve_signature(
            signature_id=args["signature_id"],
            fix_applied=args.get("fix_applied"),
            verbose=args.get("verbose", False),
        )

    elif command == "retriage":
        if "signature_id" not in args:
            raise ValueError("Missing required parameter: signature_id")
        return await cli_handler.retriage_signature(
            signature_id=args["signature_id"],
            verbose=args.get("verbose", False),
        )

    elif command == "reinvestigate":
        if "signature_id" not in args:
            raise ValueError("Missing required parameter: signature_id")
        return await cli_handler.reinvestigate_signature(
            signature_id=args["signature_id"],
            verbose=args.get("verbose", False),
        )

    else:
        raise ValueError(f"Unknown command: {command}. Use 'help' for available commands.")


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

    # Simple text format for now (json format can be added later)
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


async def bootstrap() -> None:
    """Load configuration, wire adapters, and start the application.

    This is the composition root: the single place where all components
    are instantiated and wired together.

    Steps:
    1. Load configuration from environment
    2. Configure logging
    3. Instantiate adapters with configuration
    4. Initialize core services
    5. Select and start run mode

    Raises:
        SystemExit: On fatal errors (configuration, adapter initialization)
        asyncio.CancelledError: On graceful shutdown signal
    """
    # Step 1: Load configuration
    settings = load_settings()

    # Step 2: Configure logging
    configure_logging(settings.log_level, settings.log_format)
    logger = logging.getLogger(__name__)
    logger.info("Loading Rounds diagnostic system...")

    # Step 3: Instantiate adapters
    logger.info("Initializing adapters...")

    # Telemetry adapter - select based on config
    if settings.telemetry_backend == "signoz":
        telemetry = SigNozTelemetryAdapter(
            api_url=settings.signoz_api_url,
            api_key=settings.signoz_api_key,
        )
        logger.info("Telemetry adapter: SigNoz")
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
    else:
        logger.error(f"Unknown telemetry backend: {settings.telemetry_backend}")
        sys.exit(1)

    # Signature store - select based on config
    if settings.store_backend == "sqlite":
        store = SQLiteSignatureStore(
            db_path=settings.store_sqlite_path,
        )
        logger.info(f"Signature store initialized: {settings.store_sqlite_path}")
    elif settings.store_backend == "postgresql":
        # Lazy import for optional PostgreSQL dependency
        from rounds.adapters.store.postgresql import PostgreSQLSignatureStore

        # Parse PostgreSQL connection URL or use individual parameters
        if settings.database_url:
            # Parse connection URL (postgresql://user:password@host:port/database)
            parsed = urllib.parse.urlparse(settings.database_url)
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
    if settings.diagnosis_backend == "claude_code":
        diagnosis_engine = ClaudeCodeDiagnosisAdapter(
            model=settings.claude_code_model,
            budget_usd=settings.claude_code_budget_usd,
        )
        logger.info("Diagnosis adapter: Claude Code")
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
    if settings.notification_backend == "stdout":
        notification = StdoutNotificationAdapter(verbose=settings.debug)
        logger.info("Notification adapter: Stdout")
    elif settings.notification_backend == "markdown":
        notification = MarkdownNotificationAdapter(report_path=settings.notification_output_dir)
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

    # Poll service (implements PollPort)
    poll_service = PollService(
        telemetry=telemetry,
        store=store,
        fingerprinter=fingerprinter,
        triage=triage,
        investigator=investigator,
        lookback_minutes=settings.error_lookback_minutes,
        services=None,  # None means all services
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
    )

    # Step 5: Select run mode and start
    logger.info(f"Starting in {settings.run_mode} mode...")

    try:
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
            except KeyboardInterrupt:
                await http_server.stop()

        else:
            logger.error(f"Unknown run mode: {settings.run_mode}")
            sys.exit(1)

    finally:
        # Clean up resources
        await telemetry.close()
        await store.close_pool()
        # Close notification adapter if it has a close method
        if hasattr(notification, 'close'):
            await notification.close()


def main() -> None:
    """Application entry point.

    Loads configuration, wires adapters, initializes core services,
    and starts the appropriate run mode (daemon, CLI, or webhook).

    Exit codes:
        0: Successful shutdown
        1: Fatal bootstrap or runtime error
        130: Interrupted by user (SIGINT/KeyboardInterrupt)
    """
    logger = logging.getLogger(__name__)
    try:
        asyncio.run(bootstrap())
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
