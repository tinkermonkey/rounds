---
name: rounds-architecture
description: Display hexagonal architecture overview with ASCII diagram and key file locations
user_invocable: true
args:
generated: true
generation_timestamp: 2026-02-13T22:07:37.095564Z
generation_version: "2.0"
source_project: rounds
source_codebase_hash: a44338f108beaf54
---

# Rounds Architecture Overview

Quick-reference skill for understanding the **rounds** hexagonal architecture, continuous error diagnosis system.

## Usage

```bash
/rounds-architecture
```

## Purpose

Displays a comprehensive overview of the rounds project architecture including:
- **Hexagonal architecture diagram** showing core domain and adapters
- **Key file locations** with line references for critical components
- **Dependency flow** from composition root through ports to adapters
- **Component boundaries** between domain logic and infrastructure

This skill helps developers quickly understand:
- Where to find specific functionality (fingerprinting, triage, investigation)
- How the hexagonal architecture separates concerns
- Which adapters implement which ports
- Entry points for different run modes (daemon, CLI, webhook)

## Implementation

The skill displays the following information:

### 1. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     COMPOSITION ROOT                        │
│                      main.py:1-150                          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Dependency Injection & Wiring                        │  │
│  │ - Load Config (config.py)                            │  │
│  │ - Instantiate Adapters                               │  │
│  │ - Wire Services                                      │  │
│  │ - Start Run Mode (daemon/cli/webhook)               │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
┌────────────────────────────────────────────────────────────┐
│                      CORE DOMAIN                           │
│                    core/ (no external deps)                │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Domain Models (models.py:1-100)                      │ │
│  │ - Signature (frozen dataclass)                       │ │
│  │ - Diagnosis (immutable)                              │ │
│  │ - ErrorEvent                                         │ │
│  └──────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Ports (ports.py:1-200)                               │ │
│  │ - TelemetryPort (abstract)                           │ │
│  │ - StorePort (abstract)                               │ │
│  │ - DiagnosisPort (abstract)                           │ │
│  │ - NotificationPort (abstract)                        │ │
│  └──────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Services (orchestration logic)                       │ │
│  │ - fingerprint.py:1-80 (error fingerprinting)        │ │
│  │ - triage.py:1-60 (classification)                    │ │
│  │ - investigator.py:1-100 (diagnosis orchestration)   │ │
│  │ - poll_service.py:1-120 (polling loop)              │ │
│  │ - management_service.py:1-80 (CLI/webhook ops)      │ │
│  └──────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
            │               │               │
            ▼               ▼               ▼
┌────────────────────────────────────────────────────────────┐
│                    ADAPTER LAYER                           │
│              adapters/ (port implementations)              │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Telemetry Adapters (adapters/telemetry/)            │ │
│  │ - signoz.py:1-150 (SigNoz traces)                   │ │
│  │ - jaeger.py (Jaeger traces)                         │ │
│  │ - grafana_stack.py (Grafana Stack)                  │ │
│  └──────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Store Adapters (adapters/store/)                     │ │
│  │ - sqlite.py:1-180 (SQLite persistence)              │ │
│  └──────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Diagnosis Adapters (adapters/diagnosis/)             │ │
│  │ - claude_code.py:1-130 (LLM analysis)               │ │
│  └──────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Notification Adapters (adapters/notification/)       │ │
│  │ - stdout.py (console output)                         │ │
│  │ - markdown.py (file reports)                         │ │
│  │ - github_issues.py (GitHub integration)             │ │
│  └──────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Scheduler Adapters (adapters/scheduler/)             │ │
│  │ - daemon.py (polling orchestration)                  │ │
│  └──────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Webhook Adapters (adapters/webhook/)                 │ │
│  │ - http_server.py (HTTP server)                       │ │
│  │ - receiver.py (alert processing)                     │ │
│  └──────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ CLI Adapters (adapters/cli/)                         │ │
│  │ - commands.py (interactive commands)                 │ │
│  └──────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

### 2. Key File Locations

**Composition Root:**
- `rounds/main.py` - Entry point, dependency wiring, run mode selection

**Core Domain:**
- `rounds/core/models.py` - Signature, Diagnosis, ErrorEvent (immutable dataclasses)
- `rounds/core/ports.py` - Abstract port interfaces (TelemetryPort, StorePort, etc.)
- `rounds/core/fingerprint.py` - Error fingerprinting logic
- `rounds/core/triage.py` - Error classification
- `rounds/core/investigator.py` - Diagnosis orchestration
- `rounds/core/poll_service.py` - Polling loop coordinator
- `rounds/core/management_service.py` - CLI/webhook operations

**Configuration:**
- `rounds/config.py` - Pydantic BaseSettings, environment-based config

**Telemetry Adapters:**
- `rounds/adapters/telemetry/signoz.py` - SigNoz trace queries
- `rounds/adapters/telemetry/jaeger.py` - Jaeger trace queries
- `rounds/adapters/telemetry/grafana_stack.py` - Grafana Stack integration

**Persistence Adapters:**
- `rounds/adapters/store/sqlite.py` - SQLite signature store (async with aiosqlite)

**Diagnosis Adapters:**
- `rounds/adapters/diagnosis/claude_code.py` - Claude Code LLM integration

**Notification Adapters:**
- `rounds/adapters/notification/stdout.py` - Console reporting
- `rounds/adapters/notification/markdown.py` - File-based reports
- `rounds/adapters/notification/github_issues.py` - GitHub issue creation

**Scheduler Adapters:**
- `rounds/adapters/scheduler/daemon.py` - Continuous polling daemon

**Webhook Adapters:**
- `rounds/adapters/webhook/http_server.py` - HTTP server (aiohttp)
- `rounds/adapters/webhook/receiver.py` - Alert webhook processing

**CLI Adapters:**
- `rounds/adapters/cli/commands.py` - Interactive CLI commands

**Testing:**
- `rounds/tests/fakes/` - Fake implementations of ports for testing
- `rounds/tests/core/` - Domain logic unit tests
- `rounds/tests/adapters/` - Adapter integration tests
- `rounds/tests/integration/` - End-to-end tests

### 3. Component Boundaries

**Core Domain Rules:**
- ✅ **Can depend on:** Other core modules, Python standard library
- ❌ **Cannot depend on:** Adapters, external libraries (httpx, aiosqlite, etc.)
- ✅ **Contains:** Pure business logic, domain models, port interfaces
- 🔒 **Immutability:** All domain models are frozen dataclasses

**Adapter Layer Rules:**
- ✅ **Can depend on:** Core ports, external libraries
- ❌ **Cannot depend on:** Other adapters directly
- ✅ **Contains:** Infrastructure code, external API calls, I/O operations
- 🔄 **All I/O is async:** Uses async/await for all operations

**Composition Root Rules:**
- ✅ **Responsibilities:** Load config, instantiate adapters, wire dependencies
- 🎯 **Single location:** All dependency injection happens in `main.py`
- 🚫 **No business logic:** Only wiring and initialization

### 4. Dependency Flow

```
User/External System
    │
    ▼
main.py (Composition Root)
    │
    ├─► config.py (Settings)
    │
    ├─► Adapter Instantiation
    │   ├─► TelemetryPort ← SigNozAdapter
    │   ├─► StorePort ← SQLiteAdapter
    │   ├─► DiagnosisPort ← ClaudeCodeAdapter
    │   └─► NotificationPort ← StdoutAdapter
    │
    ├─► Service Instantiation
    │   ├─► FingerprintService
    │   ├─► TriageService
    │   ├─► InvestigatorService
    │   └─► PollService
    │
    └─► Run Mode Selection
        ├─► Daemon Mode → poll_service.run()
        ├─► CLI Mode → CLI prompt loop
        └─► Webhook Mode → HTTP server.start()
```

### 5. Run Modes

**Daemon Mode:**
```bash
TELEMETRY_BACKEND=signoz RUN_MODE=daemon python -m rounds.main
```
- Continuously polls telemetry for errors
- Fingerprints and triages new errors
- Invokes diagnosis for high-priority signatures
- Reports findings via configured notification adapter

**CLI Mode:**
```bash
RUN_MODE=cli python -m rounds.main
```
- Interactive command-line interface
- Manual investigation commands
- Signature management (list, review, force diagnosis)

**Webhook Mode:**
```bash
RUN_MODE=webhook WEBHOOK_PORT=8080 python -m rounds.main
```
- HTTP server listening for external triggers
- Processes alert webhooks from monitoring systems
- Asynchronous diagnosis triggering

### 6. Key Design Patterns

**Port-Adapter Pattern:**
- All external dependencies accessed through abstract ports
- Adapters implement ports for specific technologies
- Domain services depend only on port interfaces

**Dependency Injection:**
- Single composition root in `main.py`
- Dependencies passed explicitly to constructors
- No global state or service locators

**Immutable Domain Models:**
- `@dataclass(frozen=True)` for all domain entities
- Mutations only through service methods
- State changes return new instances

**Async-First I/O:**
- All ports defined with `async def` methods
- Blocking operations wrapped with `asyncio.to_thread()`
- Event loop managed by asyncio.run() in main.py

**Configuration as Environment:**
- Pydantic BaseSettings with `.env` support
- Loaded once at startup
- Passed to adapters via constructor injection

### 7. Testing Strategy

**Unit Tests (Domain Logic):**
- Use fake implementations from `tests/fakes/`
- Test core services in isolation
- No external dependencies

**Integration Tests (Adapters):**
- Test adapter implementations with real/test services
- Verify port contract compliance
- Check error handling and edge cases

**Example - Using Fakes:**
```python
from rounds.tests.fakes.store import FakeStore
from rounds.core.investigator import InvestigatorService

async def test_investigation():
    store = FakeStore()
    investigator = InvestigatorService(store=store, ...)
    # Test domain logic without SQLite
```

### 8. Critical Configuration

**Environment Variables:**
- `TELEMETRY_BACKEND`: "signoz" | "jaeger" | "grafana_stack"
- `STORE_BACKEND`: "sqlite" (default)
- `DIAGNOSIS_BACKEND`: "claude_code" (default)
- `RUN_MODE`: "daemon" | "cli" | "webhook"
- `POLL_INTERVAL_SECONDS`: Polling frequency (default: 60)
- `CLAUDE_CODE_BUDGET_USD`: Per-diagnosis budget limit
- `DAILY_BUDGET_LIMIT`: Daily spending cap

**See:** `rounds/config.py:1-100` for complete configuration schema

## Examples

### Example 1: Understanding the Poll Cycle

When running in daemon mode, the system follows this flow:

```
1. PollService.run() [poll_service.py:45]
   ↓
2. TelemetryPort.query_recent_errors() [signoz.py:60]
   ↓
3. FingerprintService.fingerprint() [fingerprint.py:30]
   ↓
4. StorePort.upsert_signature() [sqlite.py:80]
   ↓
5. TriageService.should_investigate() [triage.py:25]
   ↓
6. InvestigatorService.investigate() [investigator.py:50]
   ↓
7. DiagnosisPort.diagnose() [claude_code.py:40]
   ↓
8. NotificationPort.send() [stdout.py:20]
```

### Example 2: Adding a New Telemetry Adapter

To add support for a new telemetry backend:

1. **Create adapter:** `rounds/adapters/telemetry/myservice.py`
2. **Implement TelemetryPort:** Define `async def query_recent_errors()`
3. **Add config:** Add fields to `config.py` (e.g., `MYSERVICE_API_URL`)
4. **Wire in main.py:** Add instantiation logic in composition root
5. **Test:** Create integration test in `tests/adapters/telemetry/`

### Example 3: Exploring Architecture

```bash
# View core domain models
cat rounds/core/models.py

# View port interfaces
cat rounds/core/ports.py

# View SQLite adapter implementation
cat rounds/adapters/store/sqlite.py

# View composition root
cat rounds/main.py

# Run tests to see fakes in action
pytest rounds/tests/core/ -v
```

### Example 4: Understanding Hexagonal Architecture Benefits

**Benefit 1 - Testability:**
- Core services tested with fakes (no database required)
- Fast, isolated unit tests for business logic

**Benefit 2 - Flexibility:**
- Swap SQLite for PostgreSQL by implementing StorePort
- Add new telemetry backends without touching core logic

**Benefit 3 - Maintainability:**
- Clear boundaries between domain and infrastructure
- Changes to external APIs isolated to adapters

---

*This skill was automatically generated from project analysis on 2026-02-13.*
