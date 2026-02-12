"""Configuration loading for the Rounds diagnostic system.

This module provides centralized configuration management:
- Load settings from environment variables and .env files
- Validate configuration using pydantic
- Provide typed access to all settings
- Support multiple environments (development, staging, production)
"""

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment.

    Uses pydantic-settings for environment variable handling with
    .env file support via python-dotenv.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Telemetry backend configuration
    telemetry_backend: Literal["signoz", "jaeger", "grafana_stack"] = Field(
        default="signoz",
        description="Telemetry backend type",
    )
    signoz_api_url: str = Field(
        default="http://localhost:4418",
        description="SigNoz API endpoint URL",
    )
    signoz_api_key: str = Field(
        default="",
        description="SigNoz API authentication key",
    )
    jaeger_api_url: str = Field(
        default="http://localhost:16686",
        description="Jaeger Query API endpoint URL",
    )
    grafana_loki_url: str = Field(
        default="http://localhost:3100",
        description="Grafana Loki API endpoint URL",
    )
    grafana_tempo_url: str = Field(
        default="http://localhost:3200",
        description="Grafana Tempo API endpoint URL",
    )
    grafana_api_key: str = Field(
        default="",
        description="Grafana API authentication key",
    )
    grafana_prometheus_url: str = Field(
        default="http://localhost:9090",
        description="Grafana Prometheus API endpoint URL",
    )

    # Signature store configuration
    store_backend: Literal["sqlite", "postgresql"] = Field(
        default="sqlite",
        description="Signature store backend type",
    )
    store_sqlite_path: str = Field(
        default="./data/signatures.db",
        description="SQLite database file path",
    )
    database_url: str = Field(
        default="",
        description="PostgreSQL connection URL",
    )

    # Diagnosis engine configuration
    diagnosis_backend: Literal["claude_code", "openai"] = Field(
        default="claude_code",
        description="Diagnosis engine backend type",
    )
    claude_code_budget_usd: float = Field(
        default=2.0,
        description="Budget per diagnosis for Claude Code in USD",
    )
    claude_code_model: str = Field(
        default="claude-opus",
        description="Claude model to use for diagnosis",
    )
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key for diagnosis",
    )
    openai_model: str = Field(
        default="gpt-4",
        description="OpenAI model to use for diagnosis",
    )

    # Notification configuration
    notification_backend: Literal["stdout", "markdown", "github_issue"] = Field(
        default="stdout",
        description="Notification backend type",
    )
    notification_output_dir: str = Field(
        default="./notifications",
        description="Output directory for markdown notifications",
    )
    github_token: str = Field(
        default="",
        description="GitHub personal access token for issue creation",
    )
    github_repo: str = Field(
        default="",
        description="GitHub repository for issue creation (owner/repo)",
    )
    github_repo_owner: str = Field(
        default="",
        description="GitHub repository owner for issue creation",
    )
    github_repo_name: str = Field(
        default="",
        description="GitHub repository name for issue creation",
    )

    # Poll cycle configuration
    poll_interval_seconds: int = Field(
        default=60,
        description="Polling interval in seconds",
    )
    poll_batch_size: int = Field(
        default=100,
        description="Number of events to retrieve per poll",
    )
    error_lookback_minutes: int = Field(
        default=15,
        description="Lookback window in minutes for error queries",
    )

    # Budget controls
    daily_budget_limit: float = Field(
        default=100.0,
        description="Daily limit for diagnosis spending in USD",
    )

    # Logging configuration
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Log level",
    )
    log_format: Literal["json", "text"] = Field(
        default="text",
        description="Log format",
    )

    # Run mode
    run_mode: Literal["daemon", "cli", "webhook"] = Field(
        default="daemon",
        description="Run mode",
    )

    # Webhook configuration
    webhook_host: str = Field(
        default="0.0.0.0",
        description="Host to listen on for webhook server",
    )
    webhook_port: int = Field(
        default=8080,
        description="Port to listen on for webhook server",
    )
    webhook_api_key: str = Field(
        default="",
        description="API key for webhook authentication (required for production)",
    )
    webhook_require_auth: bool = Field(
        default=False,
        description="Require API key authentication for webhook endpoints",
    )

    # Codebase configuration
    codebase_path: str = Field(
        default="./",
        description="Path to the codebase for diagnosis context",
    )

    # Development
    debug: bool = Field(
        default=False,
        description="Enable debug mode with verbose logging",
    )

    @field_validator("poll_interval_seconds")
    @classmethod
    def validate_poll_interval(cls, v: int) -> int:
        """Ensure poll interval is positive."""
        if v <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        return v

    @field_validator("poll_batch_size")
    @classmethod
    def validate_batch_size(cls, v: int) -> int:
        """Ensure batch size is positive."""
        if v <= 0:
            raise ValueError("poll_batch_size must be positive")
        return v

    @field_validator("claude_code_budget_usd")
    @classmethod
    def validate_claude_budget(cls, v: float) -> float:
        """Ensure per-diagnosis budget is non-negative."""
        if v < 0:
            raise ValueError("claude_code_budget_usd must be non-negative")
        return v

    @field_validator("daily_budget_limit")
    @classmethod
    def validate_budget_limit(cls, v: float) -> float:
        """Ensure budget limit is non-negative."""
        if v < 0:
            raise ValueError("daily_budget_limit must be non-negative")
        return v

    @field_validator("error_lookback_minutes")
    @classmethod
    def validate_lookback_minutes(cls, v: int) -> int:
        """Ensure lookback window is positive."""
        if v <= 0:
            raise ValueError("error_lookback_minutes must be positive")
        return v

    @field_validator("webhook_port")
    @classmethod
    def validate_webhook_port(cls, v: int) -> int:
        """Ensure webhook port is in valid range."""
        if v <= 0 or v > 65535:
            raise ValueError("webhook_port must be between 1 and 65535")
        return v


def load_settings(env_file: str | None = None) -> Settings:
    """Load application settings from environment.

    Args:
        env_file: Optional path to .env file. If not provided,
                 uses the default .env in the current directory.

    Returns:
        Validated Settings instance.

    Raises:
        ValidationError: If settings validation fails.
    """
    if env_file:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    return Settings()


__all__ = ["Settings", "load_settings"]
