# Critical Issues Summary

## Production Risk Assessment

This document identifies the **highest-risk test gaps** that could cause silent failures or security issues in production.

### Risk Level: HIGH (9-10/10)

#### 1. MarkdownNotificationAdapter: Concurrent Write Race Condition
**File**: `/workspace/rounds/adapters/notification/markdown.py` (line 30-44)
**Status**: NOT TESTED

```python
# Current implementation
async def report(self, signature: Signature, diagnosis: Diagnosis) -> None:
    entry = self._format_report_entry(signature, diagnosis)
    async with self._lock:  # <-- Lock is in place
        try:
            with open(self.report_path, "a") as f:
                f.write(entry)
                f.write("\n")
```

**The Risk**: If two `report()` calls occur simultaneously:
1. Lock should prevent file corruption ✓ (code looks correct)
2. BUT: No test verifies the lock actually works
3. Silent failure: Reports could interleave, corrupting the markdown file

**Impact**: Audit trail (the entire purpose of this adapter) becomes unreliable. Users trust the file but get corrupted output.

**Test Gap**: `test_concurrent_report_writes()` missing

---

#### 2. ManagementService: Timezone Bug in last_seen
**File**: `/workspace/rounds/core/management_service.py` (line 52, 84, 116)
**Status**: BUG EXISTS, NOT TESTED

```python
# Line 52: NAIVE datetime (no timezone)
signature.last_seen = datetime.utcnow()  # ❌ BUG

# Comparison with fixture
sample_signature.last_seen = datetime.now(tz=timezone.utc)  # ✓ Timezone-aware
```

**The Risk**:
1. Naive datetime loses timezone information
2. Comparisons between naive and aware datetimes will fail or behave unexpectedly
3. Database storage/retrieval may lose timezone info
4. Time-based queries (e.g., "errors in last 24 hours") could be off by UTC offset

**Impact**: Signature timestamps become unreliable, breaking features that depend on accurate timing (like cooldown periods, SLA tracking).

**Fix Required**: Change all three lines to use `datetime.now(tz=timezone.utc)` (note: `utcnow()` → `now(tz=timezone.utc)`)

**Test Gap**: `test_mute_signature_timezone_consistency()` missing (would FAIL against current code)

---

#### 3. Telemetry Adapters: Zero Real Testing
**Files**:
- `/workspace/rounds/adapters/telemetry/jaeger.py` (456 lines of code)
- `/workspace/rounds/adapters/telemetry/grafana_stack.py` (462 lines of code)

**Status**: COMPLETELY UNTESTED (only lifecycle tests exist)

```python
# Current test
async def test_adapter_lifecycle(self, adapter: JaegerTelemetryAdapter) -> None:
    """Test adapter initialization and cleanup."""
    async with adapter:
        pass  # ← Does NOTHING to verify functionality
    # If we get here, cleanup was successful
```

**The Risk**:
1. 918 lines of complex parsing logic has ZERO test coverage
2. JSON parsing, timestamp conversion, tree building all untested
3. API integration untested: what if response format changes?
4. Edge cases like malformed data, missing fields, wrong types all untested

**Examples of Untested Paths**:
- Jaeger response parsing (line 109-112)
- Stack frame extraction from logs (line 154)
- Span tree building from flat span list (line 307-372)
- Grafana's different JSON format vs Jaeger (batches → scopeSpans → spans)
- Timestamp conversion from microseconds (Jaeger) vs nanoseconds (Grafana)
- Error detection: multiple ways to mark error (tags, logs, status)

**Impact**: If any parsing logic is wrong, the entire diagnostic system fails silently. Errors are retrieved but not processed correctly.

**Test Gap**: 25+ missing tests covering API responses, data parsing, edge cases

---

### Risk Level: HIGH (7-8/10)

#### 4. GitHubIssueNotificationAdapter: No HTTP Error Handling Tests
**File**: `/workspace/rounds/adapters/notification/github_issues.py` (lines 70-159)
**Status**: NOT TESTED

```python
async def report(self, signature: Signature, diagnosis: Diagnosis) -> None:
    # ...
    response = await client.post(...)

    if response.status_code == 201:
        # Success path ✓ tested
    else:
        logger.error(f"Failed to create GitHub issue...")
        # Error path ✗ NOT TESTED
```

**Common Error Codes Not Tested**:
- **401 Unauthorized**: Invalid token
- **403 Forbidden**: Token doesn't have permission
- **404 Not Found**: Repository doesn't exist
- **422 Validation Failed**: Invalid label, duplicate issue, etc.
- **5xx Server Error**: GitHub API down

**The Risk**:
1. Auth failures (401, 403) are silent (just logged)
2. No way to distinguish transient (retry later) vs permanent (alert user) errors
3. If GitHub API response format changes, no test catches it
4. Missing context: which error is which? How should monitoring system respond?

**Impact**: Teams don't realize their issue tracking is broken. Diagnoses are computed but never reported.

**Test Gap**: Tests for all common HTTP error codes missing

---

#### 5. CLICommandHandler: Unhandled Exception Types
**File**: `/workspace/rounds/adapters/cli/commands.py` (lines 51-79, 101-129, etc.)
**Status**: PARTIALLY TESTED

```python
async def mute_signature(self, signature_id: str, reason: str | None = None, verbose: bool = False) -> dict[str, Any]:
    try:
        await self.management.mute_signature(signature_id, reason)
        return {"status": "success", ...}
    except ValueError as e:  # ← ONLY ValueError caught
        logger.error(f"Failed to mute signature: {e}")
        return {"status": "error", ...}
    # What about RuntimeError, TimeoutError, ConnectionError?
    # They will propagate unhandled!
```

**The Risk**:
1. If management port raises RuntimeError (database error), CLI crashes
2. Users get stack trace instead of graceful error message
3. No test verifies all exception types are handled

**Impact**: CLI becomes fragile. Production errors cause CLI to crash instead of reporting gracefully.

**Test Gap**: `test_mute_command_with_runtime_error()` missing

---

### Risk Level: MEDIUM (5-6/10)

#### 6. MarkdownNotificationAdapter: No Write Error Scenarios
**File**: `/workspace/rounds/adapters/notification/markdown.py`
**Status**: NOT TESTED

```python
async def report(self, signature: Signature, diagnosis: Diagnosis) -> None:
    # ...
    async with self._lock:
        try:
            with open(self.report_path, "a") as f:
                f.write(entry)
                f.write("\n")
        except IOError as e:
            logger.error(f"Failed to write markdown report: {e}", ...)
            raise  # Propagates the error
```

**Untested Scenarios**:
- File permissions: write access revoked (e.g., `chmod 444`)
- Disk full: `OSError: No space left on device`
- Parent directory deleted
- File is a directory instead of a file
- Network filesystem timeout

**Impact**: Notification system silently fails. No record of diagnoses. Monitoring gap.

**Test Gap**: All error scenarios untested

---

## Quick Reference: Tests to Add (Priority Order)

### MUST ADD (Sprint 1)
1. ✓ ManagementService: Fix timezone bug + add test
2. ✓ MarkdownNotificationAdapter: Concurrent write test
3. ✓ GitHubIssueNotificationAdapter: HTTP error tests (401, 403, 404, 422, 5xx)
4. ✓ CLICommandHandler: Non-ValueError exception handling
5. ✓ JaegerTelemetryAdapter: Basic API integration tests (5+ tests)

### SHOULD ADD (Sprint 2)
6. GrafanaStackTelemetryAdapter: Basic API integration tests (5+ tests)
7. MarkdownNotificationAdapter: Write error scenarios (disk full, permissions)
8. Telemetry adapters: Stack frame parsing tests
9. ManagementService: State transition validation
10. CLICommandHandler: Large dataset handling

### NICE TO ADD (Sprint 3)
11. Telemetry adapters: Complete parsing coverage
12. Integration tests: End-to-end workflows
13. Load testing: Concurrent operations
14. GitHub adapter: Client lifecycle verification

---

## Code Changes Recommended

### Change 1: Fix ManagementService Timezone Bug
**File**: `/workspace/rounds/core/management_service.py`

Replace all occurrences of `datetime.utcnow()` with `datetime.now(tz=timezone.utc)`:

```python
# Line 52 - in mute_signature()
signature.last_seen = datetime.now(tz=timezone.utc)

# Line 84 - in resolve_signature()
signature.last_seen = datetime.now(tz=timezone.utc)

# Line 116 - in retriage_signature()
signature.last_seen = datetime.now(tz=timezone.utc)
```

Also import at top:
```python
from datetime import datetime, timezone  # Add timezone import
```

### Change 2: Improve CLICommandHandler Exception Handling
**File**: `/workspace/rounds/adapters/cli/commands.py`

In `mute_signature()`, `resolve_signature()`, and `retriage_signature()` methods, catch all exceptions:

```python
async def mute_signature(self, signature_id: str, reason: str | None = None, verbose: bool = False) -> dict[str, Any]:
    try:
        await self.management.mute_signature(signature_id, reason)
        result = {"status": "success", ...}
        # ... rest of code
        return result
    except ValueError as e:
        logger.error(f"Failed to mute signature: {e}")
        return {"status": "error", "operation": "mute", "signature_id": signature_id, "message": str(e)}
    except Exception as e:  # ← Add this
        logger.error(f"Failed to mute signature (unexpected error): {e}")
        return {"status": "error", "operation": "mute", "signature_id": signature_id, "message": f"Unexpected error: {str(e)}"}
```

---

## Test Execution Verification

To verify fixes work:

```bash
# Run current tests
pytest rounds/tests/test_new_implementations.py -v

# Expected: 26/26 pass (timezone bug test will fail initially)
# After fix: Add new tests, expect them to pass

# Run with coverage
pytest rounds/tests/test_new_implementations.py --cov=rounds --cov-report=term-missing

# Look for low coverage in:
# - rounds/adapters/telemetry/jaeger.py
# - rounds/adapters/telemetry/grafana_stack.py
# - rounds/adapters/notification/github_issues.py (HTTP error paths)
```

---

## Dependencies for New Tests

Required imports to add to test file:

```python
import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import pytest

# Already in file - verify present
from datetime import datetime, timezone
from rounds.adapters.cli.commands import CLICommandHandler, run_command
from rounds.adapters.notification.github_issues import GitHubIssueNotificationAdapter
from rounds.adapters.notification.markdown import MarkdownNotificationAdapter
from rounds.adapters.telemetry.grafana_stack import GrafanaStackTelemetryAdapter
from rounds.adapters.telemetry.jaeger import JaegerTelemetryAdapter
from rounds.core.management_service import ManagementService
from rounds.core.models import (
    Confidence, Diagnosis, Severity, Signature, SignatureStatus, StackFrame,
)
from rounds.tests.fakes.store import FakeSignatureStorePort
```

---

## Expected Impact

| Metric | Current | After Fixes |
|--------|---------|-------------|
| Test Count | 26 | 85-90 |
| Code Coverage (lines) | ~50% | ~85% |
| Critical Issue Count | 5 | 0 |
| High Risk Gaps | 5 | 0 |
| Telemetry Test Coverage | 1% | 60% |

All improvements focus on preventing silent failures and improving observability of the diagnostic system.
