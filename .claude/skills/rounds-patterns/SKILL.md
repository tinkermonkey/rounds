---
name: rounds-patterns
description: Show common coding patterns: frozen dataclasses, async ports, immutable collections
user_invocable: true
args:
generated: true
generation_timestamp: 2026-02-13T22:09:52.359861Z
generation_version: "2.0"
source_project: rounds
source_codebase_hash: a44338f108beaf54
---

# Rounds Coding Patterns

Quick-reference skill for **rounds** project coding patterns and conventions.

## Usage

```bash
/rounds-patterns
```

## Purpose

Displays the core coding patterns used throughout the rounds project, including:
- **Frozen dataclasses** for immutable domain entities
- **Async port interfaces** for all I/O operations
- **Immutable collections** for safe state management
- **Type-safe configuration** with pydantic-settings
- **Dependency injection** through composition root

This skill provides quick access to copy-paste examples directly from the codebase.

## Implementation

When invoked, this skill displays code examples from actual project files organized by pattern category:

### 1. Frozen Dataclasses (Immutable Domain Models)

From `rounds/core/models.py`:
```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class Signature:
    """Immutable fingerprint of a unique error pattern."""
    fingerprint: str
    service: str
    error_pattern: str
    first_seen: str
    last_seen: str
    occurrence_count: int
    status: Literal["new", "investigating", "diagnosed", "resolved"]

@dataclass(frozen=True)
class Diagnosis:
    """Immutable result from root cause analysis."""
    signature_fingerprint: str
    model: str
    confidence: Literal["low", "medium", "high"]
    root_cause_hypothesis: str
    suggested_fixes: list[str]
    cost_usd: float
```

**Key Pattern**: Use `frozen=True` for all domain entities. Mutations happen through service methods that return new instances.

### 2. Async Port Interfaces

From `rounds/core/ports.py`:
```python
from abc import ABC, abstractmethod

class TelemetryPort(ABC):
    """Abstract interface for trace/error telemetry systems."""

    @abstractmethod
    async def query_recent_errors(
        self,
        lookback_minutes: int,
        batch_size: int
    ) -> list[ErrorEvent]:
        """Async I/O operation - must be implemented as async."""
        pass

class StorePort(ABC):
    """Abstract interface for signature persistence."""

    @abstractmethod
    async def save_signature(self, signature: Signature) -> None:
        """All database operations are async."""
        pass

    @abstractmethod
    async def get_signature(self, fingerprint: str) -> Signature | None:
        """Returns None if not found (no exceptions for missing data)."""
        pass
```

**Key Pattern**: All ports use `async def`. Return `None` for missing data rather than raising exceptions.

### 3. Immutable Collections

From `rounds/core/fingerprint.py`:
```python
def generate_fingerprint(
    service: str,
    error_type: str,
    stack_trace_lines: list[str]
) -> str:
    """
    Pure function - no mutations of input parameters.
    Returns new string, never modifies inputs.
    """
    normalized_stack = tuple(  # Convert to immutable tuple
        _normalize_stack_line(line)
        for line in stack_trace_lines[:5]
    )

    fingerprint_parts = (service, error_type, *normalized_stack)
    combined = "|".join(fingerprint_parts)

    return hashlib.sha256(combined.encode()).hexdigest()[:16]
```

**Key Pattern**: Use `tuple()` for immutable sequences. Pure functions never mutate inputs.

### 4. Async Service Methods

From `rounds/core/investigator.py`:
```python
class Investigator:
    """Orchestrates diagnosis process using injected ports."""

    def __init__(
        self,
        store: StorePort,
        diagnosis_port: DiagnosisPort,
        notification_port: NotificationPort
    ):
        self._store = store
        self._diagnosis = diagnosis_port
        self._notification = notification_port

    async def investigate(self, signature: Signature) -> None:
        """Async orchestration of multiple async port operations."""
        try:
            diagnosis = await self._diagnosis.diagnose(signature)

            # Create new immutable instance with updated status
            updated_signature = Signature(
                fingerprint=signature.fingerprint,
                service=signature.service,
                error_pattern=signature.error_pattern,
                first_seen=signature.first_seen,
                last_seen=signature.last_seen,
                occurrence_count=signature.occurrence_count,
                status="diagnosed"
            )

            await self._store.update_signature(updated_signature)
            await self._store.save_diagnosis(diagnosis)
            await self._notification.send(diagnosis)

        except Exception as e:
            logger.error(f"Investigation failed: {e}", exc_info=True)
            raise
```

**Key Pattern**: Services depend on ports (abstractions), not concrete adapters. Async methods orchestrate multiple async port calls.

### 5. Pydantic Settings Configuration

From `rounds/config.py`:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

class Settings(BaseSettings):
    """Environment-based configuration with validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Type-safe enums using Literal
    telemetry_backend: Literal["signoz", "jaeger", "grafana_stack"] = "signoz"
    store_backend: Literal["sqlite"] = "sqlite"

    # Validated primitives
    poll_interval_seconds: int = 60
    error_lookback_minutes: int = 15

    # Optional fields with defaults
    claude_code_budget_usd: float = 5.0
    daily_budget_limit: float | None = None
```

**Key Pattern**: Use `pydantic-settings` for all config. `Literal` for enums, optional with `| None`.

### 6. Dependency Injection via Composition Root

From `rounds/main.py`:
```python
async def build_services(settings: Settings) -> tuple[PollService, ManagementService]:
    """Wire all dependencies in single location."""

    # Instantiate adapters based on config
    if settings.telemetry_backend == "signoz":
        telemetry = SigNozAdapter(settings.signoz_api_url, settings.signoz_api_key)
    elif settings.telemetry_backend == "jaeger":
        telemetry = JaegerAdapter(settings.jaeger_api_url)
    else:
        telemetry = GrafanaStackAdapter(settings.grafana_api_url)

    store = SQLiteStore(settings.store_sqlite_path)
    diagnosis = ClaudeCodeAdapter(settings.claude_code_budget_usd)
    notification = StdoutNotification()

    # Inject ports into domain services
    investigator = Investigator(store, diagnosis, notification)
    poll_service = PollService(telemetry, store, investigator, settings)
    management_service = ManagementService(store, investigator)

    return poll_service, management_service
```

**Key Pattern**: All wiring happens in `main.py`. Domain services receive port interfaces, never concrete adapters.

### 7. Fake Implementations for Testing

From `rounds/tests/fakes/store.py`:
```python
class FakeStore(StorePort):
    """In-memory implementation of StorePort for testing."""

    def __init__(self):
        self._signatures: dict[str, Signature] = {}
        self._diagnoses: dict[str, Diagnosis] = {}

    async def save_signature(self, signature: Signature) -> None:
        """Implements port interface without real database."""
        self._signatures[signature.fingerprint] = signature

    async def get_signature(self, fingerprint: str) -> Signature | None:
        """Returns None for missing signatures (no exceptions)."""
        return self._signatures.get(fingerprint)

    async def list_signatures_by_status(
        self,
        status: Literal["new", "investigating", "diagnosed", "resolved"]
    ) -> list[Signature]:
        """Filter in-memory collection."""
        return [
            sig for sig in self._signatures.values()
            if sig.status == status
        ]
```

**Key Pattern**: Fakes implement full port interface with in-memory state. Use in tests instead of mocks.

## Examples

### Example 1: Creating a New Immutable Domain Model

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class NewDomainEntity:
    id: str
    name: str
    status: Literal["active", "inactive"]
    tags: tuple[str, ...]  # Immutable sequence
```

### Example 2: Adding a New Async Port

```python
from abc import ABC, abstractmethod

class NewPort(ABC):
    """Port for new external system."""

    @abstractmethod
    async def fetch_data(self, query: str) -> list[dict]:
        """All I/O operations must be async."""
        pass
```

### Example 3: Implementing a Service with Injected Ports

```python
class NewService:
    def __init__(self, store: StorePort, external: NewPort):
        self._store = store
        self._external = external

    async def process(self, item_id: str) -> None:
        """Orchestrate multiple async port calls."""
        data = await self._external.fetch_data(item_id)
        signature = await self._store.get_signature(item_id)
        # ... business logic
```

### Example 4: Writing Tests with Fakes

```python
import pytest
from rounds.tests.fakes.store import FakeStore

@pytest.mark.asyncio
async def test_new_service():
    """Use fakes instead of mocks."""
    fake_store = FakeStore()
    service = NewService(fake_store)

    await service.process("test-id")

    result = await fake_store.get_signature("test-id")
    assert result is not None
```

## Related Files

- **Domain Models**: `rounds/core/models.py`
- **Port Interfaces**: `rounds/core/ports.py`
- **Configuration**: `rounds/config.py`
- **Composition Root**: `rounds/main.py`
- **Service Examples**: `rounds/core/investigator.py`, `rounds/core/fingerprint.py`
- **Adapter Examples**: `rounds/adapters/store/sqlite.py`, `rounds/adapters/diagnosis/claude_code.py`
- **Fake Implementations**: `rounds/tests/fakes/store.py`
- **Full Conventions**: `CLAUDE.md` and `.claude/clauditoreum/PatternsSummary.md`

---

*This skill was automatically generated from rounds project analysis.*
