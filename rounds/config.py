"""Configuration loading for the Rounds diagnostic system.

This module provides centralized configuration management:
- Load settings from environment variables and .env files
- Validate configuration using pydantic
- Provide typed access to all settings
- Support multiple environments (development, staging, production)
"""

import json
import warnings
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from rounds.core.models import Severity

# Valid OpenTelemetry severity level names, used to validate PHONE_HOME_SEVERITY_GATE.
_VALID_SEVERITY_NAMES = {s.value for s in Severity}


class Settings(BaseSettings):
    """Application configuration loaded from environment.

    Uses pydantic-settings for environment variable handling with
    .env file support via python-dotenv.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telemetry backend configuration
    telemetry_backend: Literal["signoz", "jaeger", "grafana_stack", "elasticsearch"] = Field(
        default="signoz",
        description="Telemetry backend type",
    )
    signoz_api_url: str = Field(
        default="http://localhost:3301",
        description="SigNoz base URL (unified image: port 3301; API paths /api/v3/* are appended by the adapter)",
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
    es_url: str = Field(
        default="http://localhost:9200",
        description="Elasticsearch base URL for telemetry queries",
    )
    es_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Elasticsearch API key (id:key format). Takes precedence over es_username/es_password.",
    )
    es_username: str = Field(
        default="",
        description="Elasticsearch HTTP Basic auth username",
    )
    es_password: SecretStr = Field(
        default=SecretStr(""),
        description="Elasticsearch HTTP Basic auth password",
    )
    es_traces_index: str = Field(
        default="otel-traces-*",
        description="Elasticsearch index pattern for OTEL trace documents",
    )
    es_logs_index: str = Field(
        default="otel-logs-*",
        description="Elasticsearch index pattern for OTEL log documents",
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
    store_postgresql_url: str = Field(
        default="",
        description="PostgreSQL connection URL",
    )

    # Diagnosis engine configuration
    diagnosis_backend: Literal["claude_code", "openai", "agent_node"] = Field(
        default="claude_code",
        description="Diagnosis engine backend type",
    )
    claude_code_budget_usd: float = Field(
        default=2.0,
        description="Budget per diagnosis for Claude Code in USD",
    )
    claude_model: str = Field(
        default="claude-opus",
        description="Claude model to use for diagnosis",
    )
    # Claude Code authentication — set exactly one of these two when using claude_code backend.
    # API key (sk-ant-...): direct Anthropic API access.
    # OAuth token: Claude.ai subscription access (Claude Pro/Teams/Enterprise).
    # When both are set, oauth_token takes precedence.
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API key (sk-ant-...) for Claude Code authentication",
    )
    claude_code_oauth_token: str = Field(
        default="",
        description="Claude Code OAuth token for Claude.ai subscription authentication",
    )
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key for diagnosis",
    )
    openai_model: str = Field(
        default="gpt-4",
        description="OpenAI model to use for diagnosis",
    )
    openai_budget_usd: float = Field(
        default=2.0,
        description="Budget per diagnosis for OpenAI in USD",
    )
    agent_node_service_map: str = Field(
        default="",
        description=(
            "Comma-separated service-to-agent-node mappings for the agent_node "
            "diagnosis backend. Each entry is a colon-separated triple: "
            "signoz_service_name:mcp_key:workspace_name. "
            "Example: my-api:node1:workspace-a,worker:node2:workspace-b"
        ),
    )
    agent_node_host_map: str = Field(
        default="",
        description=(
            "JSON map from agent-node mcp_key to SSH-reachable hostname. "
            "Used by the agent_node diagnosis backend to resolve the SSH "
            "target for a given mcp_key. "
            'Example: AGENT_NODE_HOST_MAP={"node1":"host-a","node2":"host-b"}'
        ),
    )
    agent_node_budget_usd: float = Field(
        default=2.0,
        description="Budget per diagnosis for the agent_node backend in USD",
    )

    # Notification configuration
    notification_backend: Literal["stdout", "markdown", "github_issue"] = Field(
        default="stdout",
        description="Notification backend type",
    )
    notification_output_dir: str = Field(
        default="./notifications",
        description="Base directory for markdown reports. Reports written to YYYY-MM-DD subdirectories with individual diagnosis files. Summary written to parent directory.",
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
    resolution_threshold_hours_default: int = Field(
        default=24,
        description=(
            "Default number of hours a diagnosed signature must have no new "
            "occurrences before it is auto-resolved and its issue closed. "
            "Overridden per-signature by Signature.resolution_threshold_hours "
            "(populated from Diagnosis.suggested_resolution_hours)."
        ),
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

    # Service filter and host mapping
    service_filter: str = Field(
        default="",
        description=(
            "Comma-separated service names to monitor (empty = all services). "
            "Names must match the telemetry backend exactly (case-sensitive). "
            "Example: SERVICE_FILTER=my-api,worker-service"
        ),
    )
    service_host_map: str = Field(
        default="",
        description=(
            "JSON map from telemetry service name to host machine. "
            "Used to route diagnosis context to the correct codebase host. "
            'Example: SERVICE_HOST_MAP={"my-api":"t5610","worker":"petit-cochon"}'
        ),
    )
    service_repo_map: str = Field(
        default="",
        description=(
            "JSON map from telemetry service name to GitHub 'owner/repo'. "
            "Used to route auto-created GitHub issues to the correct repository. "
            "Unlike SERVICE_HOST_MAP, this is not templated by Ansible: repo "
            "ownership is a small, stable mapping that changes rarely (only "
            "when a new service gets its own repo). "
            'Example: SERVICE_REPO_MAP={"my-api":"myorg/my-api","worker":"myorg/worker-svc"}'
        ),
    )

    # Phone-home configuration (reports diagnosed defects to an external
    # aggregation endpoint, gated by severity and rate-limited by cooldown)
    phone_home_endpoint_url: str = Field(
        default="",
        description="URL of the phone-home endpoint that receives diagnosed defect reports",
    )
    phone_home_auth_token: SecretStr = Field(
        default=SecretStr(""),
        description="Bearer credential for authenticating to the phone-home endpoint",
    )
    phone_home_severity_gate: str = Field(
        default="ERROR,FATAL",
        description=(
            "Comma-separated severity levels that trigger a phone-home report "
            "(TRACE, DEBUG, INFO, WARN, ERROR, FATAL). "
            "Example: PHONE_HOME_SEVERITY_GATE=ERROR,FATAL"
        ),
    )
    phone_home_cooldown_hours: int = Field(
        default=24,
        description="Minimum hours between phone-home reports for the same signature",
    )

    # Development
    debug: bool = Field(
        default=False,
        description="Enable debug mode with verbose logging",
    )

    # Self-Telemetry configuration (for instrumenting rounds CLI itself)
    enable_self_telemetry: bool = Field(
        default=False,
        description="Enable OpenTelemetry instrumentation for rounds CLI operations",
    )
    self_telemetry_otlp_endpoint: str = Field(
        default="",
        description="OTLP HTTP endpoint URL for exporting rounds CLI telemetry (e.g., http://localhost:4318/v1/traces)",
    )
    self_telemetry_service_name: str = Field(
        default="rounds-cli",
        description="Service name for rounds CLI telemetry",
    )
    self_telemetry_console_export: bool = Field(
        default=False,
        description="Enable console export for rounds CLI telemetry (for debugging)",
    )

    @field_validator("service_host_map")
    @classmethod
    def validate_service_host_map(cls, v: str) -> str:
        """Validate SERVICE_HOST_MAP is valid JSON with string keys and values."""
        stripped = v.strip()
        if not stripped:
            return v
        try:
            result = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ValueError(f"SERVICE_HOST_MAP must be valid JSON: {e}") from e
        if not isinstance(result, dict):
            raise ValueError(
                f"SERVICE_HOST_MAP must be a JSON object, got {type(result).__name__}"
            )
        non_string = {k: type(val).__name__ for k, val in result.items() if not isinstance(val, str)}
        if non_string:
            raise ValueError(
                f"SERVICE_HOST_MAP values must be strings, got non-string values: {non_string}"
            )
        return v

    @field_validator("service_repo_map")
    @classmethod
    def validate_service_repo_map(cls, v: str) -> str:
        """Validate SERVICE_REPO_MAP is valid JSON with string 'owner/repo' values."""
        stripped = v.strip()
        if not stripped:
            return v
        try:
            result = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ValueError(f"SERVICE_REPO_MAP must be valid JSON: {e}") from e
        if not isinstance(result, dict):
            raise ValueError(
                f"SERVICE_REPO_MAP must be a JSON object, got {type(result).__name__}"
            )
        malformed = {
            k: val
            for k, val in result.items()
            if not isinstance(val, str)
            or val.count("/") != 1
            or not all(part.strip() for part in val.split("/"))
        }
        if malformed:
            raise ValueError(
                f"SERVICE_REPO_MAP values must be 'owner/repo' strings, "
                f"got malformed values: {malformed}"
            )
        return v

    @field_validator("phone_home_severity_gate")
    @classmethod
    def validate_phone_home_severity_gate(cls, v: str) -> str:
        """Validate PHONE_HOME_SEVERITY_GATE contains only known severity level names."""
        stripped = v.strip()
        if not stripped:
            return v
        levels = [s.strip().upper() for s in stripped.split(",") if s.strip()]
        invalid = [s for s in levels if s not in _VALID_SEVERITY_NAMES]
        if invalid:
            raise ValueError(
                f"PHONE_HOME_SEVERITY_GATE contains invalid severity levels: {invalid}. "
                f"Valid levels: {sorted(_VALID_SEVERITY_NAMES)}"
            )
        return v

    @field_validator("phone_home_cooldown_hours")
    @classmethod
    def validate_phone_home_cooldown_hours(cls, v: int) -> int:
        """Ensure phone-home cooldown is positive."""
        if v <= 0:
            raise ValueError("phone_home_cooldown_hours must be positive")
        return v

    @field_validator("agent_node_service_map")
    @classmethod
    def validate_agent_node_service_map(cls, v: str) -> str:
        """Validate AGENT_NODE_SERVICE_MAP entries are well-formed triples."""
        stripped = v.strip()
        if not stripped:
            return v
        for entry in stripped.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(":")
            if len(parts) != 3 or not all(p.strip() for p in parts):
                raise ValueError(
                    "AGENT_NODE_SERVICE_MAP entries must be "
                    "'signoz_service_name:mcp_key:workspace_name' triples, "
                    f"got malformed entry: {entry!r}"
                )
        return v

    @field_validator("agent_node_host_map")
    @classmethod
    def validate_agent_node_host_map(cls, v: str) -> str:
        """Validate AGENT_NODE_HOST_MAP is valid JSON with string keys and values."""
        stripped = v.strip()
        if not stripped:
            return v
        try:
            result = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ValueError(f"AGENT_NODE_HOST_MAP must be valid JSON: {e}") from e
        if not isinstance(result, dict):
            raise ValueError(
                f"AGENT_NODE_HOST_MAP must be a JSON object, got {type(result).__name__}"
            )
        non_string = {k: type(val).__name__ for k, val in result.items() if not isinstance(val, str)}
        if non_string:
            raise ValueError(
                f"AGENT_NODE_HOST_MAP values must be strings, got non-string values: {non_string}"
            )
        return v

    @field_validator("agent_node_budget_usd")
    @classmethod
    def validate_agent_node_budget(cls, v: float) -> float:
        """Ensure per-diagnosis budget is non-negative."""
        if v < 0:
            raise ValueError("agent_node_budget_usd must be non-negative")
        return v

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

    @field_validator("openai_budget_usd")
    @classmethod
    def validate_openai_budget(cls, v: float) -> float:
        """Ensure per-diagnosis budget is non-negative."""
        if v < 0:
            raise ValueError("openai_budget_usd must be non-negative")
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

    @field_validator("resolution_threshold_hours_default")
    @classmethod
    def validate_resolution_threshold_hours_default(cls, v: int) -> int:
        """Ensure the default resolution threshold is positive."""
        if v <= 0:
            raise ValueError("resolution_threshold_hours_default must be positive")
        return v

    @field_validator("webhook_port")
    @classmethod
    def validate_webhook_port(cls, v: int) -> int:
        """Ensure webhook port is in valid range."""
        if v <= 0 or v > 65535:
            raise ValueError("webhook_port must be between 1 and 65535")
        return v

    @model_validator(mode="after")
    def validate_backend_dependencies(self) -> "Settings":
        """Ensure backend-specific API keys are set when required.

        Cross-field validation for backend-specific configuration:
        - Claude Code backend requires anthropic_api_key or claude_code_oauth_token
        - OpenAI backend requires openai_api_key
        - Agent node backend requires agent_node_service_map, agent_node_host_map,
          and openai_api_key (used as fallback for unmapped services)
        - GitHub notification requires github_token and github_repo
        - PostgreSQL store requires store_postgresql_url
        - Elasticsearch telemetry warns when no credentials are configured
        """
        # Validate diagnosis backend dependencies
        if self.diagnosis_backend == "claude_code":
            if not self.anthropic_api_key and not self.claude_code_oauth_token:
                raise ValueError(
                    "Either anthropic_api_key or claude_code_oauth_token must be set "
                    "when diagnosis_backend is 'claude_code'"
                )
        if self.diagnosis_backend == "openai" and not self.openai_api_key:
            raise ValueError(
                "openai_api_key must be set when diagnosis_backend is 'openai'"
            )
        if self.diagnosis_backend == "agent_node":
            if not self.get_agent_node_service_map():
                raise ValueError(
                    "agent_node_service_map must be set when diagnosis_backend is 'agent_node'"
                )
            if not self.get_agent_node_host_map():
                raise ValueError(
                    "agent_node_host_map must be set when diagnosis_backend is 'agent_node'"
                )
            if not self.openai_api_key:
                raise ValueError(
                    "openai_api_key must be set when diagnosis_backend is 'agent_node' "
                    "(OpenAIDiagnosisAdapter is required as fallback for services not "
                    "present in agent_node_service_map)"
                )

        # Validate notification backend dependencies
        if self.notification_backend == "github_issue":
            if not self.github_token:
                raise ValueError(
                    "github_token must be set when notification_backend is 'github_issue'"
                )
            if not self.github_repo:
                raise ValueError(
                    "github_repo must be set when notification_backend is 'github_issue'"
                )

        # Validate store backend dependencies
        if self.store_backend == "postgresql" and not self.store_postgresql_url:
            raise ValueError(
                "store_postgresql_url must be set when store_backend is 'postgresql'"
            )

        # Validate elasticsearch telemetry dependencies
        if self.telemetry_backend == "elasticsearch":
            has_api_key = bool(self.es_api_key.get_secret_value())
            has_basic_auth = bool(self.es_username and self.es_password.get_secret_value())
            if not has_api_key and not has_basic_auth:
                warnings.warn(
                    "No Elasticsearch credentials configured (es_api_key or "
                    "es_username+es_password). Connecting unauthenticated to "
                    f"{self.es_url}.",
                    UserWarning,
                    stacklevel=2,
                )

        return self


    def get_service_names(self) -> list[str]:
        """Parse service_filter string into a list of service names.

        Returns an empty list when SERVICE_FILTER is empty (meaning all services
        will be monitored).

        Supports comma-separated format: ``svc-a,svc-b``
        """
        v = self.service_filter.strip()
        if not v:
            return []
        return [s.strip() for s in v.split(",") if s.strip()]

    def get_service_host_map(self) -> dict[str, str]:
        """Parse service_host_map string into a dict mapping service → host.

        Returns an empty dict when SERVICE_HOST_MAP is not configured.
        Expects JSON format: ``{"my-api": "t5610", "worker": "petit-cochon"}``
        """
        v = self.service_host_map.strip()
        if not v:
            return {}
        result = json.loads(v)
        if not isinstance(result, dict):
            raise ValueError(f"SERVICE_HOST_MAP must be a JSON object, got {type(result).__name__}")
        non_string = {k: type(val).__name__ for k, val in result.items() if not isinstance(val, str)}
        if non_string:
            raise ValueError(
                f"SERVICE_HOST_MAP values must be strings, got non-string values: {non_string}"
            )
        return result

    def get_service_repo_map(self) -> dict[str, str]:
        """Parse service_repo_map string into a dict mapping service → 'owner/repo'.

        Returns an empty dict when SERVICE_REPO_MAP is not configured.
        Expects JSON format: ``{"my-api": "myorg/my-api", "worker": "myorg/worker-svc"}``
        """
        v = self.service_repo_map.strip()
        if not v:
            return {}
        result = json.loads(v)
        if not isinstance(result, dict):
            raise ValueError(f"SERVICE_REPO_MAP must be a JSON object, got {type(result).__name__}")
        return result

    def get_phone_home_severity_gate(self) -> list[str]:
        """Parse phone_home_severity_gate into a list of severity level names.

        Returns an empty list when PHONE_HOME_SEVERITY_GATE is not configured,
        meaning phone-home reporting is disabled for all severities.
        """
        v = self.phone_home_severity_gate.strip()
        if not v:
            return []
        return [s.strip().upper() for s in v.split(",") if s.strip()]

    def get_agent_node_service_map(self) -> dict[str, tuple[str, str]]:
        """Parse agent_node_service_map string into a dict mapping service -> (mcp_key, workspace).

        Returns an empty dict when AGENT_NODE_SERVICE_MAP is not configured.
        Expects comma-separated triples: ``signoz_service_name:mcp_key:workspace_name``
        """
        v = self.agent_node_service_map.strip()
        if not v:
            return {}
        result: dict[str, tuple[str, str]] = {}
        for entry in v.split(","):
            entry = entry.strip()
            if not entry:
                continue
            service_name, mcp_key, workspace = (p.strip() for p in entry.split(":"))
            result[service_name] = (mcp_key, workspace)
        return result

    def get_agent_node_host_map(self) -> dict[str, str]:
        """Parse agent_node_host_map string into a dict mapping mcp_key -> ssh hostname.

        Returns an empty dict when AGENT_NODE_HOST_MAP is not configured.
        Expects JSON format: ``{"node1": "host-a", "node2": "host-b"}``
        """
        v = self.agent_node_host_map.strip()
        if not v:
            return {}
        result = json.loads(v)
        if not isinstance(result, dict):
            raise ValueError(f"AGENT_NODE_HOST_MAP must be a JSON object, got {type(result).__name__}")
        non_string = {k: type(val).__name__ for k, val in result.items() if not isinstance(val, str)}
        if non_string:
            raise ValueError(
                f"AGENT_NODE_HOST_MAP values must be strings, got non-string values: {non_string}"
            )
        return result

    def get_effective_service_map(self) -> dict[str, tuple[str, str]]:
        """Merge service_host_map into the agent-node service map (BA Req 5).

        SERVICE_HOST_MAP entries route via the configured host acting as the
        agent node's mcp_key, with codebase_path as the workspace. When a
        service name appears in both SERVICE_HOST_MAP and
        AGENT_NODE_SERVICE_MAP, the AGENT_NODE_SERVICE_MAP entry wins.
        """
        merged: dict[str, tuple[str, str]] = {
            service_name: (host, self.codebase_path)
            for service_name, host in self.get_service_host_map().items()
        }
        merged.update(self.get_agent_node_service_map())
        return merged

    def get_effective_agent_node_host_map(self) -> dict[str, str]:
        """Merge service_host_map hosts into the agent node host lookup table.

        SERVICE_HOST_MAP supplies the SSH-reachable host directly rather than
        an mcp_key, so get_effective_service_map() uses each host as its own
        mcp_key. This registers the identity mapping needed for
        SshAgentNodeClient to resolve it, unless AGENT_NODE_HOST_MAP already
        defines that key explicitly.
        """
        merged = {host: host for host in self.get_service_host_map().values()}
        merged.update(self.get_agent_node_host_map())
        return merged


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
