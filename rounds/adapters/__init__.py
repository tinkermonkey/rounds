"""External adapters for the Rounds diagnostic system.

This package contains all external dependencies (SigNoz, SQLite, Claude Code,
HTTP servers, etc.) and provides implementations of the core port interfaces.

Adapter Organization:

- telemetry/: Adapters for retrieving errors and traces (SigNoz, Jaeger, etc.)
- store/: Adapters for signature persistence (SQLite, PostgreSQL, etc.)
- diagnosis/: Adapters for LLM-powered analysis (Claude Code, OpenAI, etc.)
- notification/: Adapters for reporting findings (stdout, GitHub issues, etc.)
- scheduler/: Adapters for driving the poll loop (daemon, cron, etc.)
- cli/: Command-line interface and management commands
- webhook/: HTTP webhook receiver for external triggers
"""
