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

from rounds.adapters.diagnosis.claude_code import ClaudeCodeDiagnosisAdapter
from rounds.adapters.notification.stdout import StdoutNotificationAdapter
from rounds.adapters.scheduler.daemon import DaemonScheduler
from rounds.adapters.store.sqlite import SQLiteSignatureStore
from rounds.adapters.telemetry.signoz import SigNozTelemetryAdapter
from rounds.config import load_settings
from rounds.core.fingerprint import Fingerprinter
from rounds.core.investigator import Investigator
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

    # Telemetry adapter (SigNoz)
    telemetry = SigNozTelemetryAdapter(
        api_url=settings.signoz_api_url,
        api_key=settings.signoz_api_key,
    )

    # Signature store (SQLite)
    store = SQLiteSignatureStore(
        db_path=settings.store_sqlite_path,
    )
    logger.info(f"Signature store initialized: {settings.store_sqlite_path}")

    # Diagnosis adapter (Claude Code)
    diagnosis_engine = ClaudeCodeDiagnosisAdapter(
        model=settings.claude_code_model,
        budget_usd=settings.claude_code_budget_usd,
    )

    # Notification adapter (Stdout)
    # Future: support multiple notification adapters based on config
    notification = StdoutNotificationAdapter(verbose=settings.debug)

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
            # CLI mode would handle interactive commands
            # Not yet implemented - stub for Phase 5b
            logger.error("CLI mode not yet implemented")
            sys.exit(1)

        elif settings.run_mode == "webhook":
            # Webhook mode would start an HTTP server
            # Not yet implemented - stub for Phase 5b
            logger.error("Webhook mode not yet implemented")
            sys.exit(1)

        else:
            logger.error(f"Unknown run mode: {settings.run_mode}")
            sys.exit(1)

    finally:
        # Clean up resources
        await telemetry.close()


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
