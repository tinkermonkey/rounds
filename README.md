# rounds

An autonomous diagnostic agent that watches your OpenTelemetry data, fingerprints failure patterns, and uses LLM-powered analysis to diagnose root causes in your codebase.

Rounds makes periodic passes over your telemetry — checking for errors, tracing them through your system, recognizing recurring patterns, and building up a persistent knowledge base of failure signatures and diagnoses. Think of it as a doctor making rounds on your running software.

## How it works

Rounds is a lightweight Python daemon that sits between your OTLP backend and your codebase. It runs a continuous control loop:

1. **Poll** — Query your OTLP backend (SigNoz, Jaeger, or Grafana Stack) for recent errors and anomalies
2. **Fingerprint** — Normalize and hash failure data (error type, service, stack trace structure, span context) into stable signatures that recognize the same bug across different manifestations
3. **Deduplicate** — Check each signature against a local SQLite database (or PostgreSQL), tracking occurrence counts, frequency, and recency
4. **Diagnose** — For new or undiagnosed signatures, invoke Claude Code CLI or OpenAI in headless mode with assembled error context and codebase access to perform root cause analysis
5. **Record** — Store the diagnosis, suggested fix, and supporting evidence back in the signature database

The daemon handles scheduling, polling, fingerprinting, deduplication, and state management deterministically. LLM reasoning is reserved for the tasks that actually require it: trace exploration, codebase analysis, and root cause diagnosis.

## Architecture

Rounds is built on **hexagonal architecture (ports and adapters)** to enable technology independence and testability:

```
┌─────────────────────────────────────────────────────────────┐
│                  DRIVING ADAPTERS                           │
│    (daemon, CLI, webhook) trigger core via ports            │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   CORE DOMAIN                               │
│  models  │  ports  │  fingerprint  │  triage  │  investigator│
│                                                             │
│        Zero external dependencies. Pure business logic.     │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   DRIVEN ADAPTERS                           │
│  telemetry/ (SigNoz, Jaeger, Grafana Stack)               │
│  store/ (SQLite, PostgreSQL)                               │
│  diagnosis/ (Claude Code, OpenAI)                          │
│  notification/ (stdout, markdown, GitHub)                 │
│  scheduler/ (daemon, webhook)                             │
│  cli/ (interactive commands)                              │
└─────────────────────────────────────────────────────────────┘
```

### Key architectural decisions:

- **Deterministic orchestration, LLM reasoning.** The control loop is plain Python. Claude Code is invoked only for analysis that requires judgment.
- **Pluggable backends** via abstract port interfaces: swap telemetry sources, LLM providers, or notification channels without touching core logic.
- **Direct backend REST APIs** for the polling loop, enabling predictable queries without LLM indirection.
- **SQLite or PostgreSQL** for signature persistence — zero-config single-file (SQLite) or production-grade multi-node (PostgreSQL). Designed for future vector similarity search via `sqlite-vec`.
- **Claude Code or OpenAI CLI** in headless mode for diagnosis, keeping LLM integration simple and observable with budget controls.

## Status

**Early development.** This project is in the proof-of-concept phase focused on getting the core loop working end-to-end for a single project.

## Prerequisites

- Python 3.11+
- A running OTLP backend (SigNoz, Jaeger, or Grafana Stack) with error data
- Claude Code CLI or OpenAI API key for diagnosis
- Optional: PostgreSQL for production-scale signature persistence

## Quick Start

The fastest way to get Rounds running is with Docker Compose:

```bash
# Copy and customize the environment file
cp .env.rounds.template .env.rounds

# Start the daemon
docker-compose -f docker-compose.rounds.yml up -d

# View logs
docker-compose logs -f rounds
```

See [DEPLOY.md](DEPLOY.md) for detailed deployment instructions and [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for all configuration options.

## Running Rounds

### Daemon mode (continuous polling and diagnosis)

```bash
# Direct Python (requires local setup)
TELEMETRY_BACKEND=signoz RUN_MODE=daemon python -m rounds.main

# Docker (recommended for production)
docker-compose -f docker-compose.rounds.yml up -d
```

### CLI mode (interactive management)

```bash
# Direct Python
RUN_MODE=cli python -m rounds.main

# Docker (interactive shell)
docker-compose -f docker-compose.rounds.yml \
               -f docker-compose.rounds.dev.yml run --rm rounds
```

### Webhook mode (external trigger listening)

```bash
# Docker with webhook override
docker-compose -f docker-compose.rounds.yml \
               -f docker-compose.webhook.yml up -d
```

## Configuration

Rounds is configured via environment variables. Copy `.env.rounds.template` to `.env.rounds` and customize:

```bash
# Telemetry backend
TELEMETRY_BACKEND=signoz                    # "signoz", "jaeger", or "grafana_stack"
SIGNOZ_API_URL=http://localhost:3301
SIGNOZ_API_KEY=your-api-key

# Signature store
STORE_BACKEND=sqlite                        # "sqlite" or "postgresql"
STORE_SQLITE_PATH=/app/data/signatures.db

# Diagnosis engine
DIAGNOSIS_BACKEND=claude_code               # "claude_code" or "openai"
ANTHROPIC_API_KEY=sk-ant-...

# Notifications
NOTIFICATION_BACKEND=markdown               # "stdout", "markdown", or "github_issue"

# Run mode
RUN_MODE=daemon                             # "daemon", "cli", or "webhook"
```

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for complete reference with examples and best practices.

## Documentation

- **[DEPLOY.md](DEPLOY.md)** — Deployment guide with quick start, configurations for different backends, and production setup
- **[docs/DOCKER.md](docs/DOCKER.md)** — Docker image specifications, customization, and security best practices
- **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)** — Complete environment variable reference with examples
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** — Common issues and solutions
- **[CLAUDE.md](CLAUDE.md)** — Project architecture, coding standards, and design decisions

## Project structure

```
rounds/
├── main.py                      # Composition root, entry point
├── config.py                    # Environment-based configuration
├── core/                        # Domain logic (no external dependencies)
│   ├── models.py                # Domain entities (Signature, Diagnosis, ErrorEvent)
│   ├── ports.py                 # Abstract interfaces for adapters
│   ├── fingerprint.py           # Error normalization and hashing
│   ├── triage.py                # Triage rules engine
│   ├── investigator.py          # Investigation orchestration
│   ├── poll_service.py          # Polling loop
│   └── management_service.py    # CLI/webhook operations
├── adapters/                    # Concrete adapter implementations
│   ├── telemetry/               # Query traces and errors
│   │   ├── signoz.py
│   │   ├── jaeger.py
│   │   └── grafana_stack.py
│   ├── store/                   # Persist signatures
│   │   ├── sqlite.py
│   │   └── postgresql.py
│   ├── diagnosis/               # Root cause analysis via LLM
│   │   ├── claude_code.py
│   │   └── openai.py
│   ├── notification/            # Report findings
│   │   ├── stdout.py
│   │   ├── markdown.py
│   │   └── github_issues.py
│   ├── scheduler/               # Polling orchestration
│   │   └── daemon.py
│   ├── webhook/                 # HTTP server for external triggers
│   │   ├── http_server.py
│   │   └── receiver.py
│   └── cli/                     # Interactive command-line interface
│       └── commands.py
└── tests/                       # Test suite
    ├── core/                    # Unit tests with fakes
    ├── adapters/                # Integration tests
    ├── integration/             # End-to-end pipeline tests
    └── fakes/                   # Fake port implementations
```

## Docker Files

- **Dockerfile.dist** — Production image (minimal, auto-updating Claude Code CLI)
- **Dockerfile.dev** — Development image (testing tools, live editing)
- **Dockerfile.agent** — Clauditoreum integration image
- **docker-compose.rounds.yml** — Base production configuration
- **docker-compose.rounds.dev.yml** — Development overrides
- **docker-compose.postgres.yml** — Multi-instance setup with PostgreSQL
- **docker-compose.full-stack.yml** — Complete stack with SigNoz
- **docker-compose.webhook.yml** — Event-driven webhook mode

## License

MIT
