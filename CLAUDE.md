# Rounds: Continuous Error Diagnosis System - Project Conventions

This document outlines the architectural principles, coding standards, and development conventions for the Rounds project.

## Architecture Overview

Rounds is a continuous error diagnosis system built on **hexagonal architecture (ports and adapters)**.

### Core Domain Layer (`core/`)
The domain layer contains pure business logic with no external dependencies:
- **Models** (`models.py`): Immutable domain entities (Signature, Diagnosis, ErrorEvent)
- **Ports** (`ports.py`): Abstract interfaces defining what adapters must implement
- **Services** (`*_service.py`, `fingerprint.py`, `triage.py`, `investigator.py`): Domain logic orchestration

### Adapter Layer (`adapters/`)
Concrete implementations of ports, organized by external system type:
- **Telemetry** (`telemetry/`): Query traces from SigNoz, Jaeger, or Grafana Stack
- **Store** (`store/`): Persist signatures to SQLite or PostgreSQL
- **Diagnosis** (`diagnosis/`): Call Claude Code or OpenAI for root cause analysis
- **Notification** (`notification/`): Report findings to stdout, markdown, or GitHub
- **Scheduler** (`scheduler/`): Run polling loops (daemon or webhook-based)
- **Webhook** (`webhook/`): HTTP server for external triggers
- **CLI** (`cli/`): Interactive command-line interface

### Composition Root (`main.py`)
Single location where all adapters are wired together and core services initialized. Entry point for all run modes (daemon, CLI, webhook).

## Coding Standards

### Type Safety
- **All code must be type-annotated** with Python 3.11+ syntax
- Use `from typing import ...` for complex types
- Use `TypeAlias` for custom type definitions
- Frozen dataclasses for immutable domain objects
- Use `Literal` for fixed string/enum values

Example:
```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class Diagnosis:
    model: str
    confidence: Literal["low", "medium", "high"]
```

### Async/Await
- **All I/O must be async** - use `async def` for ports and adapters
- Use `asyncio.to_thread()` to wrap blocking I/O (files, subprocess)
- Use `asyncio.Lock()` for mutable shared state
- **Never use `asyncio.get_event_loop()`** - use `asyncio.get_running_loop()` inside async context (Python 3.10+)

### Error Handling
- **Validate at system boundaries** (user input, external APIs) not inside domain logic
- Raise specific exceptions with context: `raise ValueError(f"...got {value}")`
- Use `exc_info=True` in logger.error() calls to preserve tracebacks
- Catch broad exceptions only as last resort, always log/re-raise

### Configuration
- Use **pydantic BaseSettings** for environment-based configuration
- All config fields must have `.env` defaults
- Configuration is loaded once at startup in `main.py`, never modified

### Testing
- **Domain logic** (core/): Unit tests with fakes
- **Adapters**: Integration tests with real or test services
- Use fakes instead of mocks - implement actual port interfaces
- Test critical paths: happy path + error cases

### Documentation
- **Docstrings for public APIs** (classes, public methods)
- Module-level docstring explains purpose and key concepts
- Inline comments only for non-obvious logic
- Link to source locations using `file:line` format

## Project Layout

```
/workspace
├── CLAUDE.md                          # This file
├── README.md                          # User-facing overview
├── rounds/
│   ├── main.py                        # Composition root, entry point
│   ├── config.py                      # Environment-based settings
│   ├── core/                          # Domain logic (no external deps)
│   │   ├── models.py                  # Domain entities
│   │   ├── ports.py                   # Abstract interfaces
│   │   ├── fingerprint.py             # Error fingerprinting
│   │   ├── triage.py                  # Error classification
│   │   ├── investigator.py            # Investigation orchestration
│   │   ├── poll_service.py            # Polling loop
│   │   └── management_service.py      # CLI/webhook operations
│   ├── adapters/
│   │   ├── telemetry/                 # Trace query implementations
│   │   │   ├── signoz.py
│   │   │   ├── jaeger.py
│   │   │   └── grafana_stack.py
│   │   ├── store/                     # Signature persistence
│   │   │   └── sqlite.py
│   │   ├── diagnosis/                 # Root cause analysis
│   │   │   └── claude_code.py
│   │   ├── notification/              # Finding reports
│   │   │   ├── stdout.py
│   │   │   ├── markdown.py
│   │   │   └── github_issues.py
│   │   ├── scheduler/                 # Polling orchestration
│   │   │   └── daemon.py
│   │   ├── webhook/                   # HTTP server
│   │   │   ├── http_server.py
│   │   │   └── receiver.py
│   │   └── cli/                       # CLI commands
│   │       └── commands.py
│   └── tests/
│       ├── core/                      # Domain unit tests
│       ├── fakes/                     # Fake implementations of ports
│       ├── integration/               # End-to-end tests
│       └── adapters/                  # Adapter integration tests
```

## Run Modes

### Daemon Mode
Continuously polls telemetry sources and diagnoses errors:
```bash
TELEMETRY_BACKEND=signoz RUN_MODE=daemon python -m rounds.main
```

### CLI Mode
Interactive commands for manual investigation and management:
```bash
RUN_MODE=cli python -m rounds.main
```

### Webhook Mode
HTTP server listening for external triggers (e.g., alert notifications):
```bash
RUN_MODE=webhook WEBHOOK_PORT=8080 python -m rounds.main
```

## Configuration

All configuration is environment-based (see `config.py` for defaults and `SettingsConfigDict`):

### Telemetry Backends
- `TELEMETRY_BACKEND`: "signoz", "jaeger", or "grafana_stack"
- Backend-specific URLs and API keys (e.g., SIGNOZ_API_URL, JAEGER_API_URL)

### Signature Store
- `STORE_BACKEND`: "sqlite" (default)
- `STORE_SQLITE_PATH`: Path to signatures.db

### Diagnosis Engine
- `DIAGNOSIS_BACKEND`: "claude_code" (default)
- `CLAUDE_CODE_BUDGET_USD`: Per-diagnosis budget
- `DAILY_BUDGET_LIMIT`: Daily spending cap

### Polling
- `POLL_INTERVAL_SECONDS`: How often to check for new errors
- `ERROR_LOOKBACK_MINUTES`: Lookback window for error queries
- `POLL_BATCH_SIZE`: Events per poll

### Notifications
- `NOTIFICATION_BACKEND`: "stdout", "markdown", or "github_issue"
- Backend-specific settings (e.g., NOTIFICATION_OUTPUT_DIR, GITHUB_TOKEN)

## Key Design Decisions

### 1. Immutable Domain Models
Signature, Diagnosis, and ErrorEvent are frozen dataclasses to prevent accidental mutations. Mutable state (status, diagnosis_json) is managed through service methods only.

### 2. Port Abstraction
All adapter dependencies are defined as abstract port classes. Domain logic depends only on ports, never concrete adapters. This enables testing with fakes and swapping implementations.

### 3. Single Composition Root
Dependencies are wired in `main.py` and passed to services. No globals, no service locators. Configuration is loaded once at startup and passed to adapters.

### 4. Async-First, Blocking-Last
All I/O uses async/await. Blocking operations (file writes, subprocesses) run in thread pools via `asyncio.to_thread()`.

### 5. Error Diagnosis is Speculative
Roots causes are hypotheses from LLM analysis, not absolute truth. Confidence levels (low/medium/high) reflect uncertainty.

## Common Tasks

### Adding a New Telemetry Adapter
1. Create `adapters/telemetry/myservice.py` implementing `TelemetryPort`
2. Add config fields for service endpoints and credentials to `config.py`
3. Instantiate in `main.py` composition root
4. Add integration tests in `tests/adapters/`

### Adding a New Diagnosis Backend
1. Create `adapters/diagnosis/myservice.py` implementing `DiagnosisPort`
2. Add config fields to `config.py`
3. Instantiate in `main.py`
4. Tests should verify cost tracking and confidence levels

### Fixing a Bug
1. Add a test that reproduces the bug
2. Fix the bug in domain logic or adapter
3. Ensure test passes
4. Run full test suite before commit

## Performance Considerations

- **Poll cycle**: Scales with error volume and lookback window
- **Diagnosis cost**: Budget limits prevent runaway LLM spending
- **SQLite queries**: Use indexes on status, service for common filters
- **Blocking I/O**: Run in thread pool to avoid event loop stalls

## Security

- Configuration containing secrets (API keys, tokens) comes from environment
- HTTP webhook server should be behind authentication (not yet implemented)
- No hardcoded credentials or test data containing secrets
- Telemetry queries validated to prevent injection
