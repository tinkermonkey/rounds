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

    # Signature store - select based on config (currently only SQLite supported)
    if settings.store_backend == "sqlite":
        store = SQLiteSignatureStore(
            db_path=settings.store_sqlite_path,
        )
        logger.info(f"Signature store initialized: {settings.store_sqlite_path}")
    else:
        logger.error(f"Unknown store backend: {settings.store_backend}")
        sys.exit(1)

    # Diagnosis adapter - select based on config (currently only Claude Code supported)
    if settings.diagnosis_backend == "claude_code":
        diagnosis_engine = ClaudeCodeDiagnosisAdapter(
            model=settings.claude_code_model,
            budget_usd=settings.claude_code_budget_usd,
        )
        logger.info("Diagnosis adapter: Claude Code")
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

    # Investigator (orchestrates investigation workflow)
    investigator = Investigator(
        telemetry=telemetry,
        store=store,
        diagnosis_engine=diagnosis_engine,
        notification=notification,
        triage=triage,
        codebase_path=settings.codebase_path,
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
    )

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
            scheduler = DaemonScheduler(
                poll_port=poll_service,
                poll_interval_seconds=settings.poll_interval_seconds,
            )
            await scheduler.start()

        elif settings.run_mode == "cli":
            # CLI mode handles interactive commands via CLICommandHandler
            # Fully implemented with mute, resolve, retriage, and details commands
            logger.info("CLI mode - ManagementService available for command handling")
            # Create CLI command handler with the management service
            cli_handler = CLICommandHandler(management_service)
            # CLI interaction loop would be implemented in main entry point
            # The handler is ready for use by CLI adapters
            sys.exit(0)

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
