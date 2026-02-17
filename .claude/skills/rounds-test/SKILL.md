---
name: rounds-test
description: Run pytest with coverage and display results
user_invocable: true
args: [test-path]
generated: true
generation_timestamp: 2026-02-13T22:08:52.861819Z
generation_version: "2.0"
source_project: rounds
source_codebase_hash: a44338f108beaf54
---

# Rounds Test Runner

Quick-reference skill for running **pytest** tests with async support in the **rounds** continuous error diagnosis system.

## Usage

```bash
# Run all tests
/rounds-test

# Run specific test file
/rounds-test test_composition_root.py

# Run specific test directory
/rounds-test core

# Run tests matching a pattern
/rounds-test test_workflows.py::test_poll_cycle
```

## Purpose

Executes the pytest test suite for the rounds project with proper async handling and verbose output. This skill:

- Runs tests from the `rounds/tests/` directory (line 53 of `rounds/pyproject.toml`)
- Uses **pytest-asyncio** with `asyncio_mode = "auto"` (line 52 of `rounds/pyproject.toml`)
- Displays verbose output with `-v` flag (line 57 of `rounds/pyproject.toml`)
- Supports the project's three-tier testing strategy:
  - **Unit tests** in `tests/core/` - Pure domain logic tests with fakes
  - **Integration tests** in `tests/adapters/` - Adapter implementations with real dependencies
  - **E2E tests** in `tests/integration/` - Full composition root validation

The rounds project uses **fakes over mocks** (fake implementations of ports in `tests/fakes/`) to ensure tests are maintainable and reflect real adapter behavior.

## Implementation

```bash
# Change to the rounds package directory (where pyproject.toml lives)
cd /home/austinsand/workspace/orchestrator/rounds/rounds

# Run pytest with the specified test path (or all tests if no path given)
if [ -n "$TEST_PATH" ]; then
    pytest -v "tests/$TEST_PATH"
else
    pytest -v
fi
```

**Configuration details** (from `rounds/pyproject.toml`):
- Test discovery pattern: `test_*.py` files (line 54)
- Test class pattern: `Test*` classes (line 55)
- Test function pattern: `test_*` functions (line 56)
- Async mode: Automatic asyncio loop handling (line 52)
- Test paths: `tests/` directory (line 53)

## Examples

### Example 1: Run all tests
```bash
/rounds-test
```
**Output:** Runs the entire test suite including:
- `tests/core/` - Domain logic unit tests
- `tests/adapters/` - Adapter integration tests
- `tests/fakes/` - Fake implementation validation
- `tests/integration/` - End-to-end workflow tests
- Root level tests: `test_composition_root.py`, `test_new_implementations.py`, `test_workflows.py`

### Example 2: Run specific test file
```bash
/rounds-test test_composition_root.py
```
**Output:** Runs only the composition root tests (13,693 bytes of dependency injection validation)

### Example 3: Run core domain tests
```bash
/rounds-test core
```
**Output:** Runs unit tests for:
- `core/models.py` - Immutable domain entities (Signature, Diagnosis, ErrorEvent)
- `core/ports.py` - Abstract port interfaces
- `core/fingerprint.py` - Error fingerprinting logic
- `core/triage.py` - Error classification
- `core/investigator.py` - Investigation orchestration

### Example 4: Run adapter tests
```bash
/rounds-test adapters
```
**Output:** Runs integration tests for concrete adapter implementations:
- `adapters/store/sqlite.py` - SQLite persistence layer
- `adapters/diagnosis/claude_code.py` - Claude-powered diagnosis engine
- `adapters/telemetry/` - Trace query implementations (SigNoz, Jaeger, etc.)

### Example 5: Run specific test function
```bash
/rounds-test test_workflows.py::test_poll_cycle
```
**Output:** Runs only the poll cycle workflow test (complete error detection → fingerprinting → diagnosis flow)

## Test Strategy Overview

The rounds project follows **hexagonal architecture** testing principles:

1. **Domain layer tests** (`tests/core/`) use **fakes** from `tests/fakes/` to validate business logic without external dependencies
2. **Adapter tests** (`tests/adapters/`) verify concrete implementations against port contracts
3. **Integration tests** (`tests/integration/`) validate full system composition in `main.py`

**Key test files** (from `rounds/tests/`):
- `test_composition_root.py` (13KB) - Dependency wiring validation
- `test_new_implementations.py` (25KB) - New feature integration tests
- `test_workflows.py` (16KB) - End-to-end diagnostic workflows

**Fake implementations** (from `tests/fakes/`):
- `fakes/store.py` - In-memory signature repository
- `fakes/telemetry.py` - Synthetic error event generator
- `fakes/diagnosis.py` - Deterministic diagnosis engine

---

*This skill was automatically generated from the rounds project structure and pyproject.toml configuration.*
