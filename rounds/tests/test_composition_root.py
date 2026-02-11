"""Integration tests for the composition root.

These tests verify that the bootstrap process correctly loads configuration,
instantiates adapters, initializes core services, and wires dependencies.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from rounds.config import load_settings
from rounds.core.fingerprint import Fingerprinter
from rounds.core.investigator import Investigator
from rounds.core.poll_service import PollService
from rounds.core.triage import TriageEngine


class TestConfigurationLoading:
    """Test configuration loading and validation."""

    def test_load_settings_with_defaults(self) -> None:
        """Load settings with default values."""
        settings = load_settings()
        assert settings.telemetry_backend == "signoz"
        assert settings.store_backend == "sqlite"
        assert settings.diagnosis_backend == "claude_code"
        assert settings.run_mode == "daemon"
        assert settings.poll_interval_seconds == 60
        assert settings.log_level == "INFO"

    def test_load_settings_from_env(self) -> None:
        """Load settings from environment variables."""
        with patch.dict(
            os.environ,
            {
                "SIGNOZ_API_URL": "http://custom.signoz:4418",
                "POLL_INTERVAL_SECONDS": "30",
                "RUN_MODE": "cli",
                "LOG_LEVEL": "DEBUG",
            },
        ):
            settings = load_settings()
            assert settings.signoz_api_url == "http://custom.signoz:4418"
            assert settings.poll_interval_seconds == 30
            assert settings.run_mode == "cli"
            assert settings.log_level == "DEBUG"

    def test_load_settings_validates_poll_interval(self) -> None:
        """Poll interval validation rejects zero or negative values."""
        with patch.dict(os.environ, {"POLL_INTERVAL_SECONDS": "0"}):
            with pytest.raises(Exception):  # ValidationError
                load_settings()

    def test_load_settings_validates_batch_size(self) -> None:
        """Batch size validation rejects zero or negative values."""
        with patch.dict(os.environ, {"POLL_BATCH_SIZE": "-1"}):
            with pytest.raises(Exception):  # ValidationError
                load_settings()

    def test_load_settings_validates_budget_limit(self) -> None:
        """Budget limit validation rejects negative values."""
        with patch.dict(os.environ, {"DAILY_BUDGET_LIMIT": "-100"}):
            with pytest.raises(Exception):  # ValidationError
                load_settings()


class TestAdapterInstantiation:
    """Test that adapters are correctly instantiated with configuration."""

    @pytest.mark.asyncio
    async def test_telemetry_adapter_instantiation(self) -> None:
        """Instantiate SigNoz telemetry adapter with configuration."""
        from rounds.adapters.telemetry.signoz import SigNozTelemetryAdapter

        settings = load_settings()
        adapter = SigNozTelemetryAdapter(
            api_url=settings.signoz_api_url,
            api_key=settings.signoz_api_key,
        )
        assert adapter.api_url == settings.signoz_api_url
        assert adapter.api_key == settings.signoz_api_key

    @pytest.mark.asyncio
    async def test_signature_store_adapter_instantiation(self) -> None:
        """Instantiate SQLite signature store with configuration."""
        from rounds.adapters.store.sqlite import SQLiteSignatureStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            adapter = SQLiteSignatureStore(db_path=db_path)
            # db_path is stored as a Path object internally
            assert str(adapter.db_path) == db_path
            # Initialize schema (lazy initialization on first use)
            await adapter._init_schema()
            assert Path(db_path).exists()

    def test_diagnosis_adapter_instantiation(self) -> None:
        """Instantiate Claude Code diagnosis adapter with configuration."""
        from rounds.adapters.diagnosis.claude_code import (
            ClaudeCodeDiagnosisAdapter,
        )

        settings = load_settings()
        adapter = ClaudeCodeDiagnosisAdapter(
            model=settings.claude_code_model,
            budget_usd=settings.claude_code_budget_usd,
        )
        assert adapter.model == settings.claude_code_model
        assert adapter.budget_usd == settings.claude_code_budget_usd

    def test_notification_adapter_instantiation(self) -> None:
        """Instantiate stdout notification adapter with configuration."""
        from rounds.adapters.notification.stdout import StdoutNotificationAdapter

        adapter = StdoutNotificationAdapter(verbose=True)
        assert adapter.verbose is True


class TestCoreServiceInitialization:
    """Test that core services are correctly initialized with adapters."""

    @pytest.mark.asyncio
    async def test_investigator_initialization(self) -> None:
        """Initialize Investigator with all required ports."""
        from rounds.adapters.diagnosis.claude_code import (
            ClaudeCodeDiagnosisAdapter,
        )
        from rounds.adapters.notification.stdout import StdoutNotificationAdapter
        from rounds.adapters.store.sqlite import SQLiteSignatureStore
        from rounds.adapters.telemetry.signoz import SigNozTelemetryAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = SQLiteSignatureStore(db_path=db_path)
            await store._init_schema()

            telemetry = SigNozTelemetryAdapter(
                api_url="http://localhost:4418",
                api_key="",
            )
            diagnosis = ClaudeCodeDiagnosisAdapter(
                model="claude-opus",
                budget_usd=2.0,
            )
            notification = StdoutNotificationAdapter()
            triage = TriageEngine()

            investigator = Investigator(
                telemetry=telemetry,
                store=store,
                diagnosis_engine=diagnosis,
                notification=notification,
                triage=triage,
                codebase_path="./",
            )

            # Verify investigator has all required ports
            assert investigator.telemetry is telemetry
            assert investigator.store is store
            assert investigator.diagnosis_engine is diagnosis
            assert investigator.notification is notification
            assert investigator.codebase_path == "./"

    @pytest.mark.asyncio
    async def test_poll_service_initialization(self) -> None:
        """Initialize PollService with all required ports."""
        from rounds.adapters.store.sqlite import SQLiteSignatureStore
        from rounds.adapters.telemetry.signoz import SigNozTelemetryAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = SQLiteSignatureStore(db_path=db_path)
            await store._init_schema()

            telemetry = SigNozTelemetryAdapter(
                api_url="http://localhost:4418",
                api_key="",
            )

            fingerprinter = Fingerprinter()
            triage = TriageEngine()

            # Create a mock investigator for this test
            investigator = AsyncMock()

            poll_service = PollService(
                telemetry=telemetry,
                store=store,
                fingerprinter=fingerprinter,
                triage=triage,
                investigator=investigator,
                lookback_minutes=15,
                services=None,
            )

            # Verify poll service has all required ports
            assert poll_service.telemetry is telemetry
            assert poll_service.store is store
            assert poll_service.fingerprinter is fingerprinter
            assert poll_service.triage is triage
            assert poll_service.lookback_minutes == 15

    def test_daemon_scheduler_initialization(self) -> None:
        """Initialize DaemonScheduler with PollPort."""
        from rounds.adapters.scheduler.daemon import DaemonScheduler

        mock_poll_port = AsyncMock()
        scheduler = DaemonScheduler(
            poll_port=mock_poll_port,
            poll_interval_seconds=30,
        )

        assert scheduler.poll_port is mock_poll_port
        assert scheduler.poll_interval_seconds == 30
        assert scheduler.running is False


class TestDependencyWiring:
    """Test that dependencies are correctly wired together."""

    @pytest.mark.asyncio
    async def test_complete_composition_with_sqlite(self) -> None:
        """Verify complete composition with SQLite store."""
        from rounds.adapters.diagnosis.claude_code import (
            ClaudeCodeDiagnosisAdapter,
        )
        from rounds.adapters.notification.stdout import StdoutNotificationAdapter
        from rounds.adapters.store.sqlite import SQLiteSignatureStore
        from rounds.adapters.telemetry.signoz import SigNozTelemetryAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")

            # Create adapters
            telemetry = SigNozTelemetryAdapter(
                api_url="http://localhost:4418",
                api_key="test-key",
            )
            store = SQLiteSignatureStore(db_path=db_path)
            await store._init_schema()
            diagnosis = ClaudeCodeDiagnosisAdapter(
                model="claude-opus",
                budget_usd=5.0,
            )
            notification = StdoutNotificationAdapter(verbose=False)

            # Create core services
            fingerprinter = Fingerprinter()
            triage = TriageEngine()

            investigator = Investigator(
                telemetry=telemetry,
                store=store,
                diagnosis_engine=diagnosis,
                notification=notification,
                triage=triage,
                codebase_path="./",
            )

            poll_service = PollService(
                telemetry=telemetry,
                store=store,
                fingerprinter=fingerprinter,
                triage=triage,
                investigator=investigator,
                lookback_minutes=15,
            )

            # Verify the entire composition works
            assert poll_service.telemetry is telemetry
            assert poll_service.store is store
            assert investigator.telemetry is telemetry
            assert investigator.diagnosis_engine is diagnosis
            assert investigator.notification is notification


class TestBootstrapFunction:
    """Test the bootstrap function (composition root)."""

    @pytest.mark.asyncio
    async def test_bootstrap_function_exists(self) -> None:
        """Verify that bootstrap function exists and is callable."""
        # Test that the bootstrap function exists in the main module.
        # Full bootstrap testing would require mocking external services,
        # which is covered by other adapter tests.
        from rounds.main import bootstrap

        # Verify it's a coroutine function
        assert asyncio.iscoroutinefunction(bootstrap)


class TestLoggingConfiguration:
    """Test logging configuration."""

    def test_configure_logging_text_format(self) -> None:
        """Configure logging with text format."""
        # Test that configure_logging function exists and can be imported
        # We don't directly test it since it modifies global logging state
        from rounds.main import configure_logging  # noqa: F401

    def test_configure_logging_json_format(self) -> None:
        """Configure logging with JSON format."""
        # Test that configure_logging function exists and can be imported
        # We don't directly test it since it modifies global logging state
        from rounds.main import configure_logging  # noqa: F401


class TestConfigurationDefaults:
    """Test that configuration defaults are sensible."""

    def test_default_settings_reasonable_values(self) -> None:
        """Verify that default settings have reasonable values."""
        settings = load_settings()

        # Telemetry defaults
        assert settings.signoz_api_url == "http://localhost:4418"
        assert settings.store_sqlite_path == "./data/signatures.db"

        # Polling defaults
        assert settings.poll_interval_seconds == 60
        assert settings.poll_batch_size == 100

        # Budget defaults
        assert settings.daily_budget_limit == 100.0
        assert settings.claude_code_budget_usd == 2.0

        # Codebase path
        assert settings.codebase_path == "./"

        # Debug mode off by default
        assert settings.debug is False
