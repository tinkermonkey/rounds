---
name: rounds-check
description: Run mypy type checking and ruff linting
user_invocable: true
args:
generated: true
generation_timestamp: 2026-02-13T22:10:47.785428Z
generation_version: "2.0"
source_project: rounds
source_codebase_hash: a44338f108beaf54
---

# Type Check & Lint

Quick-reference skill for **rounds** - runs mypy type checking and ruff linting to ensure code quality.

## Usage

```bash
/rounds-check
```

## Purpose

Validates code quality for the **rounds** continuous error diagnosis system by:

1. **Type checking with mypy** - Ensures 100% type annotation compliance (required by `CLAUDE.md:45`)
2. **Linting with ruff** - Enforces Python code style and catches common errors

This skill is critical because rounds uses **strict type safety** as a core architectural principle:
- All functions must be type-annotated with Python 3.11+ syntax
- Domain models use `@dataclass(frozen=True)` for immutability
- All I/O uses `async def` with proper async/await patterns
- Ports define abstract interfaces with precise type signatures

## Implementation

Runs the following commands from the project's development dependencies:

### 1. Type Check with mypy

```bash
mypy rounds/
```

**What it checks:**
- All functions have type annotations (`rounds/core/models.py:13-40`)
- Async/await usage is correct (`rounds/core/ports.py:15-85`)
- Port interfaces match adapter implementations
- No `Any` types in domain layer (`rounds/core/`)
- Frozen dataclasses used correctly (`rounds/core/models.py`)

### 2. Lint with ruff

```bash
ruff check rounds/
```

**What it checks:**
- Import order (standard lib → third-party → local, per `CLAUDE.md:125`)
- Snake_case for files/functions, PascalCase for classes (`CLAUDE.md:111-117`)
- Unused imports and variables
- Line length and formatting
- F-string usage and comprehensions
- Error handling patterns

## Examples

### Run both checks

```bash
/rounds-check
```

**Expected output (clean run):**
```
✓ Running type check with mypy
Success: no issues found in 15 source files

✓ Running lint check with ruff
All checks passed!
```

### Example failures and fixes

**mypy error - missing type annotation:**
```
rounds/core/fingerprint.py:42: error: Function is missing a return type annotation
```
**Fix:** Add return type to function signature

**ruff error - import order:**
```
rounds/adapters/store/sqlite.py:5:1: I001 Import block is un-sorted or un-formatted
```
**Fix:** Reorder imports (standard lib → third-party → local)

## Key Files Checked

Based on project architecture:

**Core domain layer** (`rounds/core/`):
- `models.py` - Immutable domain entities (Signature, Diagnosis, ErrorEvent)
- `ports.py` - Abstract port interfaces (8 ports total)
- `fingerprint.py` - Error fingerprinting service
- `triage.py` - Error classification service
- `investigator.py` - Investigation orchestration
- `poll_service.py` - Polling loop service
- `management_service.py` - CLI/webhook operations

**Adapter layer** (`rounds/adapters/`):
- `telemetry/signoz.py`, `jaeger.py`, `grafana_stack.py`
- `store/sqlite.py`
- `diagnosis/claude_code.py`
- `notification/stdout.py`, `markdown.py`, `github_issues.py`
- `scheduler/daemon.py`
- `webhook/http_server.py`, `receiver.py`
- `cli/commands.py`

**Composition root:**
- `main.py` - Dependency injection and entry point
- `config.py` - Pydantic settings with environment variables

## Why This Matters

From `CLAUDE.md:35-42`:
> **All code must be type-annotated** with Python 3.11+ syntax
> Use `from typing import ...` for complex types
> Use `TypeAlias` for custom type definitions
> Frozen dataclasses for immutable domain objects

Type safety and code quality are **non-negotiable** in rounds because:
- Hexagonal architecture requires precise port/adapter contracts
- Async I/O patterns must be verified (no accidental blocking calls)
- Immutable domain models prevent state corruption
- LLM diagnosis costs real money - bugs are expensive

---

*This skill was automatically generated from rounds project conventions.*
