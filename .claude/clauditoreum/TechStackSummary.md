# Tech Stack Summary: rounds

## Project Overview
**Rounds** is a continuous error diagnosis system that autonomously monitors OpenTelemetry data, fingerprints errors, and uses LLMs to perform root cause analysis. The system is built on **hexagonal architecture (ports and adapters)** with a pure domain core and pluggable external adapters.

## Language & Runtime
- **Primary Language**: Python 3.11+
- **Runtime**: Python interpreter (supports 3.11 and 3.12)
- **Build System**: setuptools >= 68.0 with wheel

## Core Architecture Pattern

### Hexagonal Architecture (Ports & Adapters)
The codebase follows a strict separation between:
- **Core Domain** (`core/`): Pure business logic with zero external dependencies
- **Adapter Layer** (`adapters/`): Concrete implementations for external systems
- **Composition Root** (`main.py`): Single location for dependency wiring

## Major Frameworks & Libraries

### Configuration Management
- **pydantic-settings** >= 2.0
- **Purpose**: Type-safe environment variable configuration with .env file support
- **Best Practices**:
  - Uses `BaseSettings` for automatic environment variable loading
  - Provides validation at startup with field validators
  - Supports multiple environments (development, staging, production)
  - Case-insensitive environment variable matching
  - Built-in support for .env files via python-dotenv integration
- **Key Pattern**: All configuration loaded once at startup in `config.py` (lines 16-270)
- **Research Notes**: pydantic-settings is the official Pydantic extension for settings management, supporting environment variables, secrets files, and multiple configuration sources. It requires Python >=3.10 and provides structured, type-safe configuration validation.

### Data Validation & Type Safety
- **pydantic** >= 2.0
- **Purpose**: Runtime type validation and data modeling
- **Best Practices**:
  - Used for `Settings` configuration class with automatic validation
  - Field validators ensure configuration constraints (e.g., positive intervals, budget limits)
  - Integrates with Python's type hints for IDE support
- **Key Pattern**: Configuration validation at `config.py:16-270`

### Async Database Access
- **aiosqlite** >= 0.19
- **Purpose**: Async SQLite database adapter for Python's asyncio
- **Best Practices**:
  - Connection pooling to avoid blocking the event loop (`sqlite.py:25-64`)
  - Uses context managers for automatic connection cleanup
  - All database operations are async to maintain non-blocking I/O
  - Schema initialization with dedicated lock to prevent race conditions
- **Key Pattern**: Connection pool with `_get_connection()` / `_return_connection()` at `sqlite.py:40-57`
- **Research Notes**: aiosqlite provides an asyncio bridge to the standard sqlite3 module, allowing SQLite database interactions on the main AsyncIO event loop without blocking. It requires Python >=3.9 and uses a single shared thread per connection with a request queue to prevent overlapping actions. This makes it ideal for concurrent asyncio applications.

### HTTP Client
- **httpx** >= 0.25
- **Purpose**: Modern async-first HTTP client for API calls (telemetry backends, GitHub, etc.)
- **Best Practices**:
  - Supports both sync and async APIs
  - Provides connection pooling and HTTP/2 support
  - Used for telemetry backend queries (SigNoz, Jaeger, Grafana)
  - Context managers for automatic resource cleanup
- **Key Pattern**: Async HTTP requests in telemetry adapters (`adapters/telemetry/*.py`)
- **Research Notes**: HTTPX is a fully featured HTTP client offering both synchronous and asynchronous APIs with HTTP/1.1 and HTTP/2 support. The AsyncClient provides connection pooling, redirects, and cookie persistence. According to 2026 comparisons, HTTPX is recommended for heavy asynchronous requests and uses Python's asyncio with async/await syntax for concurrent request handling.

### Environment Configuration
- **python-dotenv** >= 1.0
- **Purpose**: Load environment variables from .env files
- **Best Practices**:
  - Integrates with pydantic-settings for automatic .env loading
  - Used for local development secrets management
  - Production environments use actual environment variables

## Testing Framework

### Test Runner & Async Testing
- **pytest** >= 7.0
- **pytest-asyncio** >= 0.21
- **Purpose**: Test framework with native async/await support
- **Configuration**: `pyproject.toml:51-57`
  - `asyncio_mode = "auto"` - automatic async test detection
  - Test discovery: `test_*.py` files, `Test*` classes, `test_*` functions
  - Verbose output enabled by default
- **Test Structure**:
  - Unit tests: `tests/core/` - domain logic with fakes
  - Integration tests: `tests/integration/` - end-to-end workflows
  - Adapter tests: `tests/adapters/` - external system integration
  - Fakes: `tests/fakes/` - fake implementations of ports for testing
- **Key Pattern**: All async code tested with `async def test_*()` functions
- **Example**: `tests/core/test_services.py:1-100` shows async test fixtures with pytest

### Testing Approach
- **Fakes over Mocks**: The project uses fake implementations of port interfaces rather than mocking frameworks
- **Benefits**: Fakes provide actual working implementations, making tests more realistic and maintainable
- **Location**: All fakes in `tests/fakes/` (FakeTelemetryPort, FakeSignatureStorePort, etc.)

## Development Tools

### Type Checking
- **mypy** >= 1.0
- **types-python-dateutil** (type stubs)
- **Configuration**: `pyproject.toml:59-71`
  - `strict = true` - maximum type safety
  - `disallow_untyped_defs = true` - all functions must have type annotations
  - `python_version = "3.11"`
- **Purpose**: Static type checking to catch type errors before runtime
- **Key Pattern**: Every function and method has complete type annotations

### Linting & Formatting
- **ruff** >= 0.1
- **Purpose**: Fast Python linter and formatter (replaces flake8, isort, black)
- **Configuration**: `pyproject.toml:72-89`
  - Line length: 100 characters
  - Target: Python 3.11
  - Enabled rules: E (pycodestyle errors), F (pyflakes), I (isort), N (naming), W (warnings), UP (pyupgrade), RUF (ruff-specific)
  - E501 (line length) handled by formatter, not linter
  - First-party module: "rounds"
- **Key Features**:
  - Import sorting with known first-party packages
  - Automatic code formatting
  - Enforces PEP 8 style guide

## Deployment & Infrastructure

### Containerization
- **None detected** - No Dockerfile or container configuration found
- Project uses direct Python execution via `python -m rounds.main`

### CI/CD
- **None detected** - No GitHub Actions, GitLab CI, or other CI/CD configuration found

### Database
- **SQLite** (default): File-based database (`./data/signatures.db`)
- **PostgreSQL** (optional): Production-ready RDBMS (lazy-loaded dependency)
- **Migration Strategy**: Schema initialization on first connection (`sqlite.py:66-115`)

## Code Patterns Detected

### 1. Async/Await Everywhere
- **Evidence**: All I/O operations use `async def` and `await`
- **Pattern**: Core services, adapters, and tests are fully async
- **Examples**:
  - `core/ports.py:62` - `async def get_recent_errors()`
  - `adapters/store/sqlite.py:117` - `async def get_by_id()`
  - `tests/core/test_services.py:1` - async test functions

### 2. Strict Type Safety
- **Evidence**: 100% type annotation coverage with mypy strict mode
- **Pattern**: Every function has parameter and return type annotations
- **Examples**:
  - `core/models.py:14-366` - frozen dataclasses with type hints
  - `core/ports.py:44-564` - abstract interfaces with typed methods
  - `config.py:16-270` - pydantic settings with field types

### 3. Immutability by Default
- **Evidence**: Frozen dataclasses for domain entities
- **Pattern**: Most domain models are immutable; only `Signature` is mutable for state transitions
- **Examples**:
  - `core/models.py:14` - `@dataclass(frozen=True)` for ErrorEvent, Diagnosis, etc.
  - `core/models.py:117` - `@dataclass` (mutable) only for Signature state machine
  - `core/models.py:59` - `MappingProxyType` for read-only dicts in frozen classes

### 4. Hexagonal Architecture (Ports & Adapters)
- **Evidence**: Clear separation between core domain and adapters
- **Pattern**: Core depends on abstract ports; adapters implement concrete behavior
- **Examples**:
  - `core/ports.py:44-165` - abstract port interfaces (TelemetryPort, SignatureStorePort)
  - `adapters/telemetry/signoz.py` - concrete SigNoz implementation
  - `adapters/store/sqlite.py:22` - concrete SQLite implementation
  - `main.py:270-499` - composition root wiring adapters to core

### 5. Constructor Injection
- **Evidence**: Dependencies passed via constructors, no globals
- **Pattern**: Services receive their dependencies at instantiation
- **Examples**:
  - `main.py:409-417` - Investigator constructed with all dependencies
  - `main.py:420-428` - PollService constructed with injected services
  - `main.py:435-440` - ManagementService constructed with required ports

### 6. Connection Pooling
- **Evidence**: Custom connection pool implementation for SQLite
- **Pattern**: Pre-allocated connections with lock-protected pool
- **Examples**:
  - `sqlite.py:34-64` - connection pool with `_get_connection()` and `_return_connection()`
  - `sqlite.py:35` - asyncio.Lock for thread-safe pool access

### 7. Error Handling at Boundaries
- **Evidence**: Validation at system boundaries, not in domain logic
- **Pattern**: Validate user input and external data; domain assumes valid data
- **Examples**:
  - `core/models.py:23-30` - `__post_init__` validation in domain models
  - `config.py:195-249` - field validators for configuration constraints
  - `adapters/store/sqlite.py:323-412` - error handling for database deserialization

### 8. Composition Root Pattern
- **Evidence**: Single location for dependency wiring
- **Pattern**: `main.py` is the ONLY place that imports both core and adapters
- **Examples**:
  - `main.py:1-13` - imports from both core and adapters
  - `main.py:270-499` - bootstrap() function wires everything together
  - All other modules only import from core or only from adapters, never both

## Dependencies List

### Production Dependencies (Required)
1. **pydantic** (>= 2.0) - Data validation and settings management
2. **pydantic-settings** (>= 2.0) - Environment-based configuration
3. **aiosqlite** (>= 0.19) - Async SQLite database adapter
4. **httpx** (>= 0.25) - Async HTTP client for API calls
5. **python-dotenv** (>= 1.0) - .env file loading for local development

### Development Dependencies (Optional)
1. **pytest** (>= 7.0) - Test framework and runner
2. **pytest-asyncio** (>= 0.21) - Async test support for pytest
3. **mypy** (>= 1.0) - Static type checker
4. **ruff** (>= 0.1) - Fast linter and formatter
5. **types-python-dateutil** - Type stubs for python-dateutil

### Optional Runtime Dependencies
1. **openai** - OpenAI API client (only if using OpenAI diagnosis backend)
2. **asyncpg** or **psycopg3** - PostgreSQL adapter (only if using PostgreSQL store)

## Run Modes

### 1. Daemon Mode
- **Purpose**: Continuous polling of telemetry sources
- **Command**: `RUN_MODE=daemon python -m rounds.main`
- **Pattern**: Infinite loop with configurable poll interval
- **Code**: `main.py:447-449`

### 2. CLI Mode
- **Purpose**: Interactive management commands
- **Command**: `RUN_MODE=cli python -m rounds.main`
- **Pattern**: REPL-like interface with JSON command arguments
- **Code**: `main.py:451-457`

### 3. Webhook Mode
- **Purpose**: HTTP server for external triggers
- **Command**: `RUN_MODE=webhook python -m rounds.main`
- **Pattern**: HTTP server listening for webhook events
- **Code**: `main.py:459-486`

## Key Design Decisions

### 1. Pure Domain Core
The core domain layer (`core/`) has ZERO external dependencies. It only imports from Python's standard library, ensuring the domain logic is:
- Testable without external services
- Portable across different infrastructure
- Free from framework lock-in

**Evidence**: `core/models.py:1-5` explicitly states "zero external dependencies"

### 2. Async-First, Blocking-Last
All I/O is async by default. Blocking operations (file writes, subprocess) are wrapped with `asyncio.to_thread()`.

**Benefits**:
- Non-blocking I/O maintains event loop responsiveness
- Efficient handling of concurrent operations
- Scales well with multiple telemetry sources

### 3. Budget-Constrained LLM Calls
The system enforces per-diagnosis and daily budget limits to prevent runaway costs.

**Pattern**:
- Cost estimation before LLM invocation
- Budget tracker in scheduler
- Graceful degradation if budget exceeded

**Code**: `config.py:82-147` for budget configuration

### 4. Fakes for Testing
Instead of mocking frameworks, the project uses fake implementations of port interfaces.

**Benefits**:
- Fakes are actual working implementations
- More realistic than mocks
- Easier to maintain and debug
- Can be reused across multiple tests

**Location**: `tests/fakes/` directory

### 5. State Machine for Signatures
Signatures follow a well-defined state machine with explicit transitions.

**States**: NEW → INVESTIGATING → DIAGNOSED → RESOLVED/MUTED

**Pattern**: Mutation methods (`mark_investigating()`, `mark_diagnosed()`) enforce valid transitions

**Code**: `core/models.py:163-235`

## Performance Considerations

### Poll Cycle Scaling
- Scales with error volume and lookback window
- Batch size configurable: `POLL_BATCH_SIZE` (default: 100)
- Lookback window: `ERROR_LOOKBACK_MINUTES` (default: 15)

### Database Performance
- SQLite indexes on common query fields: status, service, fingerprint
- Connection pooling prevents connection overhead
- Schema initialization only runs once per instance

### LLM Cost Management
- Cost estimation before diagnosis
- Per-diagnosis budget limit
- Daily spending cap to prevent surprises

### Async I/O Benefits
- Non-blocking telemetry queries
- Concurrent investigation of multiple signatures
- Efficient use of system resources

## Security Considerations

### Secrets Management
- All secrets loaded from environment variables
- No hardcoded credentials in codebase
- `.env` file support for local development (not committed)

### Webhook Authentication
- Optional API key authentication: `WEBHOOK_API_KEY`
- `webhook_require_auth` flag for production
- Currently no authentication implemented in webhook adapter

### Input Validation
- Configuration validated at startup with pydantic
- Telemetry queries validated to prevent injection
- Database queries use parameterized statements

## Project Structure

```
rounds/
├── core/                          # Pure domain logic (no external deps)
│   ├── models.py                  # Immutable domain entities
│   ├── ports.py                   # Abstract port interfaces
│   ├── fingerprint.py             # Error fingerprinting logic
│   ├── triage.py                  # Error classification
│   ├── investigator.py            # Investigation orchestration
│   ├── poll_service.py            # Polling loop implementation
│   └── management_service.py      # CLI/webhook operations
├── adapters/                      # Concrete implementations
│   ├── telemetry/                 # Trace/log query adapters
│   │   ├── signoz.py
│   │   ├── jaeger.py
│   │   └── grafana_stack.py
│   ├── store/                     # Signature persistence
│   │   ├── sqlite.py
│   │   └── postgresql.py
│   ├── diagnosis/                 # Root cause analysis
│   │   ├── claude_code.py
│   │   └── openai.py
│   ├── notification/              # Finding reports
│   │   ├── stdout.py
│   │   ├── markdown.py
│   │   └── github_issues.py
│   ├── scheduler/                 # Polling orchestration
│   │   └── daemon.py
│   ├── webhook/                   # HTTP server
│   │   ├── http_server.py
│   │   └── receiver.py
│   └── cli/                       # CLI commands
│       └── commands.py
├── tests/                         # Test suite
│   ├── core/                      # Domain unit tests
│   ├── fakes/                     # Fake port implementations
│   ├── integration/               # End-to-end tests
│   └── adapters/                  # Adapter integration tests
├── config.py                      # Environment-based settings
└── main.py                        # Composition root & entry point
```

## Research Sources

### pydantic-settings
- [Settings Management - Pydantic Validation](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [pydantic-settings · PyPI](https://pypi.org/project/pydantic-settings/)
- [Settings and Environment Variables - FastAPI](https://fastapi.tiangolo.com/advanced/settings/)

### aiosqlite
- [GitHub - omnilib/aiosqlite](https://github.com/omnilib/aiosqlite)
- [aiosqlite · PyPI](https://pypi.org/project/aiosqlite/)
- [aiosqlite: Sqlite for AsyncIO Documentation](https://aiosqlite.omnilib.dev/)

### httpx
- [Async Support - HTTPX](https://www.python-httpx.org/async/)
- [HTTPX Homepage](https://www.python-httpx.org/)
- [GitHub - encode/httpx](https://github.com/encode/httpx)
- [Getting Started with HTTPX: Python's Modern HTTP Client](https://betterstack.com/community/guides/scaling-python/httpx-explained/)

## Summary

The **rounds** project is a production-ready, async-first Python application built with:
- **Hexagonal architecture** for clean separation of concerns
- **Type-safe configuration** with pydantic-settings
- **Non-blocking I/O** throughout with asyncio
- **Immutable domain models** for predictable state
- **Comprehensive testing** with fakes and pytest-asyncio
- **Budget-aware LLM integration** for cost control
- **Multiple deployment modes** (daemon, CLI, webhook)

The tech stack emphasizes:
- **Type safety** (mypy strict mode, 100% annotations)
- **Async performance** (aiosqlite, httpx, asyncio)
- **Clean architecture** (ports & adapters, dependency injection)
- **Developer experience** (ruff for fast linting, pytest for testing)

This is a well-architected system that follows Python best practices for 2026, with strong emphasis on type safety, async patterns, and maintainable architecture.
