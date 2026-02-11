# rounds

An autonomous diagnostic agent that watches your OpenTelemetry data, fingerprints failure patterns, and uses LLM-powered analysis to diagnose root causes in your codebase.

Rounds makes periodic passes over your telemetry — checking for errors, tracing them through your system, recognizing recurring patterns, and building up a persistent knowledge base of failure signatures and diagnoses. Think of it as a doctor making rounds on your running software.

## How it works

Rounds is a lightweight Python daemon that sits between your OTLP backend and your codebase. It runs a continuous control loop:

1. **Poll** — Query your OTLP backend (currently SigNoz) for recent errors and anomalies
2. **Fingerprint** — Normalize and hash failure data (error type, service, stack trace structure, span context) into stable signatures that recognize the same bug across different manifestations
3. **Deduplicate** — Check each signature against a local SQLite database, tracking occurrence counts, frequency, and recency
4. **Diagnose** — For new or undiagnosed signatures, invoke Claude Code CLI in headless mode with assembled error context and codebase access to perform root cause analysis
5. **Record** — Store the diagnosis, suggested fix, and supporting evidence back in the signature database

The daemon handles scheduling, polling, fingerprinting, deduplication, and state management deterministically. LLM reasoning is reserved for the tasks that actually require it: trace exploration, codebase analysis, and root cause diagnosis.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    rounds daemon                     │
│                    (medic.py)                        │
│                                                     │
│  Poll Loop ──→ Fingerprinter ──→ Signature DB       │
│      │              │              (SQLite)          │
│      │              ▼                  │             │
│  SigNoz API    Dedup Check ──────→ Diagnosis Engine  │
│  (REST)        (hash match)       (Claude Code CLI)  │
│                                        │             │
│                                   MCP: SigNoz        │
│                                   (trace exploration)│
└─────────────────────────────────────────────────────┘
      │                                    │
      ▼                                    ▼
  OTLP Backend                     Local Codebase
  (SigNoz)                         (read-only or r/w)
```

Key architectural decisions:

- **Deterministic orchestration, LLM reasoning.** The control loop is plain Python. Claude Code is invoked only for analysis that requires judgment.
- **SigNoz MCP server** (by Doctor Droid) gives Claude Code structured access to traces and metrics during diagnosis, without hand-rolling API clients for the LLM.
- **Direct SigNoz REST API** for the polling loop, because the daemon's queries are predictable and don't benefit from LLM indirection.
- **SQLite** for the signature database — zero-config, portable, single-file persistence. Designed to be extended with vector similarity search via `sqlite-vec` when semantic fingerprint matching is needed.
- **Claude Code CLI in headless mode** (`claude -p --output-format stream-json`) for diagnosis, keeping the LLM integration simple and observable.

## Status

**Early development.** This project is in the proof-of-concept phase focused on getting the core loop working end-to-end for a single project.

## Prerequisites

- Python 3.11+
- A running SigNoz instance with OTLP data flowing in
- Claude Code CLI installed and authenticated
- SigNoz MCP server (DrDroidLab/monitoring-mcp-servers)

## Configuration

Rounds is configured via environment variables (`.env` file supported):

```bash
# SigNoz connection
SIGNOZ_API_URL=http://localhost:3301
SIGNOZ_API_KEY=your-api-key

# Polling behavior
POLL_INTERVAL_SECONDS=60
ERROR_LOOKBACK_MINUTES=15

# Claude Code
CLAUDE_MODEL=claude-sonnet-4-5-20250929
CLAUDE_MAX_BUDGET_USD=2.0

# Signature database
SIGNATURES_DB_PATH=./rounds.db
```

## Project structure

```
rounds/
├── medic.py              # Main daemon entry point
├── poller.py             # SigNoz API polling
├── fingerprint.py        # Error normalization and hashing
├── signatures.py         # SQLite signature database
├── diagnose.py           # Claude Code CLI invocation
├── config.py             # Configuration and env loading
├── rounds.db             # Signature database (created at runtime)
└── .env                  # Local configuration (not committed)
```

## License

MIT
