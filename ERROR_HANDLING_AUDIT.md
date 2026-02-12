# Error Handling Audit Report - Rounds PR Review

**Audit Date**: 2026-02-12
**Scope**: Recent PR changes (git diff main...HEAD)
**Risk Level**: HIGH - Multiple critical issues requiring immediate attention

---

## Executive Summary

The PR introduces significant new functionality across the adapter layer but contains **7 CRITICAL error handling deficiencies** that create silent failure modes, inadequate error context, and unhandled exception pathways in a production error diagnosis system.

### Critical Findings

- **3 CRITICAL severity issues**: Silent failures or unsafe fallback behavior without user awareness
- **4 HIGH severity issues**: Inadequate error context, broad exception catching, or missing re-throws
- **2 MEDIUM severity issues**: Missing error information that would aid debugging

---

## Detailed Issues

### CRITICAL SEVERITY

#### 1. CLI Commands: Exception Swallowing Without Propagation

**Location**: `/workspace/rounds/adapters/cli/commands.py` lines 50-78, 100-128, 146-171, 189-222, 288-337, 355-390

**Issue Type**: Silent Failure / Inadequate Error Handling

**Severity**: CRITICAL

**Problem**:
Multiple CLI command handlers catch `ValueError` but convert them into dictionary responses without propagating the error to the caller. This silently masks failures and prevents proper error handling at higher levels.

```python
# PROBLEMATIC PATTERN
try:
    await self.management.mute_signature(signature_id, reason)
    result = {"status": "success", ...}
    return result
except ValueError as e:
    logger.error(f"Failed to mute signature: {e}")
    return {"status": "error", "message": str(e)}  # Swallows exception
```

**Why This Is Critical**:
1. Errors are consumed and converted to dictionary responses instead of propagating
2. Callers cannot distinguish between actual failures and successful operations (both return dicts)
3. If this handler is called from async code expecting an exception to propagate (e.g., for retries or circuit breakers), the error disappears
4. Tests that expect exceptions will pass even when operations fail
5. **SILENT FAILURE**: No indication to the caller that the operation actually failed at the integration level

**Hidden Errors That Get Swallowed**:
- Database connection errors from management port
- Signature not found errors
- Permission/authorization errors
- Resource exhaustion errors
- Any exceptions raised by underlying management port that could need retry/fallback handling

**User Impact**:
- Users get error responses but code consuming this handler might not properly handle them (since no exception is raised)
- Error logs show "Failed to X" but operation continues as if successful
- Distributed tracing/error aggregation tools won't see these as failures

**Recommendation**:
Either:
1. **Return error responses AND re-raise** (for handlers that need to swallow errors for graceful degradation)
2. **Let exceptions propagate** (preferred for a reliability-focused system) and handle at the CLI/webhook boundary
3. Document if swallowing is intentional and why

**Example Fix**:
```python
try:
    await self.management.mute_signature(signature_id, reason)
    result = {
        "status": "success",
        "operation": "mute",
        "signature_id": signature_id,
        "message": f"Signature {signature_id} muted",
    }
    if reason:
        result["reason"] = reason
    if verbose:
        logger.info(
            f"Muted signature {signature_id}",
            extra={"reason": reason, "verbose": True},
        )
    return result
except ValueError as e:
    # Log the error with full context
    logger.error(
        f"Failed to mute signature {signature_id}: {e}",
        extra={"signature_id": signature_id, "reason": reason},
        exc_info=True  # Include traceback for debugging
    )
    # Return error response AND re-raise
    error_response = {
        "status": "error",
        "operation": "mute",
        "signature_id": signature_id,
        "message": str(e),
    }
    # Choose based on context: either raise or return based on handler's role
    raise  # Let caller decide what to do
```

**Affected Methods**:
- `mute_signature()` (line 71-78)
- `resolve_signature()` (line 121-128)
- `retriage_signature()` (line 164-171)
- `get_signature_details()` (line 215-222)
- `list_signatures()` (line 331-337)
- `reinvestigate_signature()` (line 383-390)

---

#### 2. Claude Code Diagnosis: Unsafe JSON Parsing Loop With Silent Skipping

**Location**: `/workspace/rounds/adapters/diagnosis/claude_code.py` lines 210-221

**Issue Type**: Silent Failure / Fallback Without Awareness

**Severity**: CRITICAL

**Problem**:
The JSON parsing logic iterates through output lines and silently skips any line that doesn't start with `{`. If Claude Code outputs are slightly malformed or formatted differently, the loop will exit without finding valid JSON and raise a ValueError, but **intermediate errors are silently swallowed**.

```python
# PROBLEMATIC CODE
lines = output.split("\n")
for line in lines:
    if line.startswith("{"):
        parsed: dict[str, Any] = json.loads(line)
        return parsed  # Returns on first match

# No valid JSON found - raise exception
raise ValueError(
    f"Claude Code CLI did not return valid JSON. Output: {output[:200]}"
)
```

**Why This Is Critical**:
1. **Silent JSON parsing failures**: If `json.loads(line)` fails partway through the output, the exception is NOT caught, causing unclear error messages
2. **No logging of parsing attempts**: If a line looks like JSON but fails to parse, there's no record of what was attempted
3. **Output truncation masks the real problem**: The error message only shows first 200 chars of output, potentially hiding the actual malformed JSON
4. **Caller has no visibility into what Claude Code actually returned**: Was it a network error? Timeout? Invalid output?

**Hidden Errors**:
- `json.JSONDecodeError` from malformed JSON in a line (will propagate uncaught)
- Invalid JSON structure that causes KeyError during parsing
- Lines that appear to start with `{` but aren't valid JSON

**User Impact**:
- Diagnosis failures with unclear error messages (only 200 chars of output shown)
- No way to debug what Claude Code actually returned
- If this runs in background (daemon mode), the error silently fails without clear logging

**Recommendation**:
Add explicit error handling and logging for each JSON parsing attempt:

```python
# BETTER VERSION
lines = output.split("\n")
parse_errors = []
for i, line in enumerate(lines):
    if line.startswith("{"):
        try:
            parsed: dict[str, Any] = json.loads(line)
            return parsed
        except json.JSONDecodeError as e:
            # Log each failed attempt
            logger.debug(
                f"Failed to parse line {i} as JSON",
                extra={"line": line[:100], "error": str(e)}
            )
            parse_errors.append((i, str(e)))

# All lines attempted, none worked
if parse_errors:
    error_msg = (
        f"All JSON parsing attempts failed. Attempted lines: {len(parse_errors)}. "
        f"First error: {parse_errors[0][1]}. "
        f"Full output (first 500 chars): {output[:500]}"
    )
else:
    error_msg = (
        f"Claude Code CLI did not return valid JSON. "
        f"Output (first 500 chars): {output[:500]}"
    )

logger.error(error_msg)
raise ValueError(error_msg)
```

---

#### 3. Daemon Scheduler: Investigation Cycle Error Handling Allows Silent Diagnosis Loss

**Location**: `/workspace/rounds/adapters/scheduler/daemon.py` lines 137-149

**Issue Type**: Silent Failure / Inadequate Error Context

**Severity**: CRITICAL

**Problem**:
Investigation cycles (where diagnosis happens) catch all exceptions but only log them—the operation continues as if successful. If diagnosis fails, **no one knows unless they check logs**.

```python
# PROBLEMATIC CODE
if result.investigations_queued > 0:
    logger.debug(f"Starting investigation cycle #{cycle_number}")
    try:
        diagnoses = await self.poll_port.execute_investigation_cycle()
        logger.info(f"Investigation cycle #{cycle_number} completed...")
    except Exception as e:
        logger.error(
            f"Error in investigation cycle #{cycle_number}: {e}",
            exc_info=True
        )
        # CONTINUES TO NEXT CYCLE WITHOUT RETRYING OR ALERTING
```

**Why This Is Critical**:
1. **Diagnosis loss**: Signatures are queued for diagnosis but if the investigation cycle fails, they're lost with no retry
2. **No differentiation between error types**: Budget exceeded errors vs. database errors vs. API errors all treated the same (logged and ignored)
3. **Daemon continues normally**: From the daemon's perspective, the cycle "completed" even though diagnosis failed
4. **No metrics/alerts**: No indication to monitoring systems that diagnosis is failing
5. **Silent diagnosis failure**: The core purpose of the system (diagnosing errors) silently fails in the background

**Hidden Errors**:
- `TimeoutError` from Claude Code (should probably pause pending diagnoses)
- `ValueError` from budget exceeded (should pause but not log as error, since it's expected)
- `RuntimeError` from Claude Code CLI issues (should retry later)
- Database errors that should fail-fast vs. transient errors that should retry

**User Impact**:
- Signatures remain in "investigating" state indefinitely with no explanation
- Administrators have no way to know diagnosis is failing unless they actively check logs
- System appears healthy while diagnosis silently stops working
- Cost tracking is broken if diagnosis fails (no costs recorded)

**Recommendation**:
Distinguish error types and take appropriate action:

```python
if result.investigations_queued > 0:
    logger.debug(f"Starting investigation cycle #{cycle_number}")
    try:
        diagnoses = await self.poll_port.execute_investigation_cycle()
        logger.info(
            f"Investigation cycle #{cycle_number} completed: "
            f"{len(diagnoses)} diagnoses produced"
        )
    except ValueError as e:
        # Budget/validation errors - expected, log at info level
        if "budget" in str(e).lower():
            logger.info(
                f"Investigation cycle #{cycle_number} skipped: {e}",
                extra={"cycle": cycle_number}
            )
        else:
            logger.error(
                f"Validation error in investigation cycle #{cycle_number}: {e}",
                exc_info=True,
                extra={"cycle": cycle_number}
            )
    except (TimeoutError, RuntimeError) as e:
        # Transient errors - should retry next cycle
        logger.warning(
            f"Transient error in investigation cycle #{cycle_number}: {e}. "
            f"Will retry next cycle.",
            exc_info=True,
            extra={"cycle": cycle_number, "error_type": type(e).__name__}
        )
    except Exception as e:
        # Unexpected errors
        logger.error(
            f"Unexpected error in investigation cycle #{cycle_number}: {e}",
            exc_info=True,
            extra={"cycle": cycle_number, "error_type": type(e).__name__}
        )
        # Consider raising or alerting here for truly exceptional errors
```

---

### HIGH SEVERITY

#### 4. Claude Code Diagnosis: Redundant Exception Re-raising Loses Context

**Location**: `/workspace/rounds/adapters/diagnosis/claude_code.py` lines 223-234

**Issue Type**: Poor Error Context / Exception Chain Abuse

**Severity**: HIGH

**Problem**:
Exception handling re-raises by converting to string and creating new exception, breaking the exception chain and losing the original traceback:

```python
except TimeoutError as e:
    logger.error(f"Claude Code CLI timeout: {e}")
    raise TimeoutError(str(e)) from e  # Creates new exception, original traceback lost
except RuntimeError as e:
    logger.error(f"Claude Code CLI error: {e}")
    raise RuntimeError(str(e)) from e  # Redundant re-wrapping
except ValueError as e:
    logger.error(f"Failed to parse Claude Code response: {e}")
    raise ValueError(str(e)) from e  # Converts to string, loses context
```

**Why This Is High Severity**:
1. **Redundant wrapping**: Converting exception to string and re-raising the same type adds no value
2. **Context loss**: Original exception details are converted to strings, losing structured error context
3. **Traceback confusion**: Using `from e` creates a chain but the re-raised exception has less context
4. **Poor error messages**: When caught at higher levels, these provide no additional context

**Recommendation**:
Simply re-raise without redundant wrapping:

```python
except TimeoutError as e:
    logger.error(f"Claude Code CLI timeout: {e}", exc_info=True)
    raise  # Preserve original traceback
except RuntimeError as e:
    logger.error(f"Claude Code CLI error: {e}", exc_info=True)
    raise
except ValueError as e:
    logger.error(f"Failed to parse Claude Code response: {e}", exc_info=True)
    raise
```

---

#### 5. Telemetry Adapters: Silent Stack Frame Parsing Failures

**Location**: `/workspace/rounds/adapters/telemetry/grafana_stack.py` lines 256-258

**Issue Type**: Silent Failure / Inadequate Error Context

**Severity**: HIGH

**Problem**:
Stack frame parsing catches all exceptions and silently returns empty list without logging what failed:

```python
try:
    lines = stack_str.split("\n")
    for line in lines:
        # ... parsing logic ...
except Exception as e:
    logger.debug(f"Failed to parse stack frames: {e}")
    # Returns empty list silently
return frames  # Empty list if exception occurred
```

**Why This Is High Severity**:
1. **Silent data loss**: Stack frames are lost with only debug-level logging
2. **No visibility into why parsing failed**: Debug logs are usually disabled in production
3. **Downstream impact**: Investigation context becomes incomplete without stack frames
4. **No indication to telemetry adapter**: The adapter doesn't know stack extraction failed
5. **Debug-level logging is insufficient**: For a failure in core data extraction, should be at warning/error level

**Recommendation**:
Log at appropriate level and return indicator of failure:

```python
try:
    lines = stack_str.split("\n")
    for line in lines:
        # ... parsing logic ...
except Exception as e:
    logger.warning(
        f"Failed to parse stack frames from stack trace: {e}",
        extra={"stack_length": len(stack_str), "error_type": type(e).__name__},
        exc_info=True
    )
    # Consider whether to return empty list or let exception propagate
    # For now, document that partial/empty stack frames are OK
return frames
```

---

#### 6. Store Adapter: Inconsistent Error Handling in Row Deserialization

**Location**: `/workspace/rounds/adapters/store/sqlite.py` lines 385-390

**Issue Type**: Inconsistent Error Handling / Overly Broad Catch

**Severity**: HIGH

**Problem**:
ValueError is logged and re-raised, but a second catch-all Exception block converts it to ValueError, creating confusing error chaining:

```python
try:
    # ... parsing logic ...
except ValueError as e:
    logger.error(f"Failed to parse database row: {e}")
    raise  # Re-raises ValueError
except Exception as e:
    logger.error(f"Unexpected error parsing database row: {e}")
    raise ValueError(f"Row parsing failed: {e}") from e  # Converts any exception to ValueError
```

**Why This Is High Severity**:
1. **Inconsistent exception types**: ValueError from parsing logic is re-raised as ValueError, but other exceptions are converted to ValueError (confusing API)
2. **Overly broad catch**: Second `except Exception` could catch `KeyError`, `AttributeError`, `TypeError` from bugs in parsing logic that should fail fast
3. **Silent bug masking**: Logic errors in parsing code are converted to generic ValueError instead of failing with clear exceptions
4. **Diagnosis/tags parsing errors are warnings, not errors**: Lines 354-358 and 364-368 log warnings and continue when JSON parsing fails, but row-level parsing failure is an error (inconsistent severity)

**Recommendation**:
Be specific about what exceptions are expected:

```python
try:
    # ... core parsing logic ...
except ValueError as e:
    # Validation failures are expected (bad data in DB)
    logger.error(
        f"Failed to parse database row - validation error: {e}",
        extra={"row": row},
        exc_info=True
    )
    raise
except json.JSONDecodeError as e:
    # JSON parsing failures (diagnosis or tags)
    logger.error(
        f"Failed to parse database row - JSON error: {e}",
        exc_info=True
    )
    raise ValueError(f"Row JSON parsing failed: {e}") from e
# Don't catch Exception - let unexpected errors fail fast
```

---

#### 7. Notification Adapters: Missing Error Context in File I/O Failures

**Location**: `/workspace/rounds/adapters/notification/markdown.py` lines 52-57, 72-77

**Issue Type**: Inadequate Error Context

**Severity**: HIGH

**Problem**:
IOError catching provides minimal context about what operation was being performed:

```python
except IOError as e:
    logger.error(
        f"Failed to write markdown report: {e}",
        extra={"path": str(self.report_path)},
    )
    raise  # Re-raises but limited context
```

**Why This Is High Severity**:
1. **Missing operation context**: Was this a report entry or a summary? What was being written?
2. **No file state information**: Is the file locked? Disk full? Permission denied? No indication
3. **No indication of data loss**: The error message doesn't indicate whether any data was persisted
4. **Ambiguous for retry logic**: Caller doesn't know if they should retry immediately, wait, or skip

**Recommendation**:
Add more context:

```python
except IOError as e:
    logger.error(
        f"Failed to write markdown report to {self.report_path}: {e}",
        extra={
            "path": str(self.report_path),
            "error_code": getattr(e, 'errno', None),
            "operation": "report" if "entry" in entry else "summary",
            "signature_id": getattr(signature, 'id', 'unknown') if 'signature' in locals() else None,
        },
        exc_info=True
    )
    raise IOError(
        f"Failed to persist diagnosis report to {self.report_path}: {e}. "
        f"This error prevents audit trail creation."
    ) from e
```

---

### MEDIUM SEVERITY

#### 8. Daemon Scheduler: Unhandled Budget Exceeded Case in Poll Cycle

**Location**: `/workspace/rounds/adapters/scheduler/daemon.py` lines 112-119

**Issue Type**: Logic Error / Missing Error Context

**Severity**: MEDIUM

**Problem**:
When budget is exceeded, poll_port.execute_poll_cycle() is called with no diagnosis, but the result's `investigations_queued` might be non-zero (signatures need diagnosis but can't get it due to budget). This creates orphaned signatures:

```python
if self._is_budget_exceeded():
    logger.warning(
        f"Daily budget limit exceeded (${self._daily_cost_usd:.2f}/"
        f"${self.budget_limit:.2f}), skipping investigation cycles"
    )
    # Still poll for errors, but don't diagnose
    result = await self.poll_port.execute_poll_cycle()
    # result.investigations_queued might be > 0 but no investigation cycle runs
```

**Why This Is Medium Severity**:
1. **Orphaned signatures**: New signatures found during budget-exceeded period remain undiagnosed (by design, but not explicitly handled)
2. **Misleading logs**: Next cycle log shows `investigations_queued` but investigation cycle doesn't run because budget is still exceeded
3. **No retry indication**: Caller doesn't know these signatures will be retried next day when budget resets

**Recommendation**:
Add explicit handling:

```python
if self._is_budget_exceeded():
    logger.warning(
        f"Daily budget limit exceeded (${self._daily_cost_usd:.2f}/"
        f"${self.budget_limit:.2f}). Pausing diagnosis until budget resets."
    )
    # Poll but don't diagnose
    result = await self.poll_port.execute_poll_cycle()
    logger.info(
        f"Poll cycle #{cycle_number} completed with budget exceeded: "
        f"{result.errors_found} errors found, "
        f"{result.new_signatures} new signatures queued for diagnosis (pending budget reset)"
    )
else:
    # ... normal cycle with diagnosis ...
```

---

#### 9. CLI Commands: Missing Error Handling for Unsupported Output Formats

**Location**: `/workspace/rounds/adapters/cli/commands.py` lines 208-213, 324-329

**Issue Type**: Silent Failure Masking

**Severity**: MEDIUM

**Problem**:
Unsupported output formats return error response but don't raise exception or log at error level:

```python
else:
    return {
        "status": "error",
        "operation": "get_details",
        "message": f"Unsupported format: {output_format}",
    }
```

**Why This Is Medium Severity**:
1. **No logging**: Invalid input isn't logged, making it hard to detect misuse
2. **Not at error level**: Should be at least a warning that someone is using unsupported format
3. **Caller might not check status field**: If code path checks response structure rather than status field, it might think operation succeeded

**Recommendation**:
Log the invalid input:

```python
else:
    logger.warning(
        f"Unsupported output format requested: {output_format}",
        extra={"supported_formats": ["json", "text"]}
    )
    return {
        "status": "error",
        "operation": "get_details",
        "message": f"Unsupported format: {output_format}. Supported formats: json, text",
    }
```

---

## Summary of Issues by Category

### Silent Failures (Unlogged or Soft-Handled Errors)
1. CLI command handlers swallowing exceptions (6 locations)
2. Daemon investigation cycle failure (silently continues)
3. Telemetry stack frame parsing failure (debug-level only)
4. JSON parsing in Claude Code adapter (loop skips silently)

### Inadequate Error Context
1. Claude Code exception re-wrapping (redundant)
2. Notification file I/O errors (minimal context)
3. CLI unsupported format handling (not logged)

### Inconsistent Error Handling
1. Store row deserialization (ValueError vs. other exceptions)
2. Daemon budget exceeded case (not explicitly handled in logs)

---

## Recommendations by Priority

### Immediate (Before Merge)
1. **Fix CLI command handlers** to not swallow exceptions or add explicit documentation why they do
2. **Fix Claude Code JSON parsing** to log each attempt and provide full error context
3. **Fix daemon investigation error handling** to distinguish error types
4. **Fix telemetry stack parsing** to log at warning level, not debug

### Before Production Deployment
1. Add structured error logging with error IDs for Sentry tracking
2. Add error handling tests that verify exceptions are properly propagated
3. Add integration tests that simulate failure modes (network errors, timeouts, etc.)
4. Document fallback behavior explicitly (when and why operations return errors instead of raising)

### Architectural Improvements
1. Consider using custom exception types (e.g., `DiagnosisError`, `TelemetryError`) instead of generic exceptions
2. Implement error budget/quota so diagnosis failures don't silently accumulate
3. Add health check endpoints that can detect failing adapters
4. Consider adding circuit breaker pattern for external services (Claude Code CLI, GitHub API)

---

## Files Most Needing Attention

1. **`/workspace/rounds/adapters/cli/commands.py`** - 6 identical error-swallowing patterns
2. **`/workspace/rounds/adapters/scheduler/daemon.py`** - Critical investigation cycle error handling
3. **`/workspace/rounds/adapters/diagnosis/claude_code.py`** - JSON parsing and error context issues
4. **`/workspace/rounds/adapters/store/sqlite.py`** - Inconsistent exception handling
5. **`/workspace/rounds/adapters/notification/markdown.py`** - Missing error context

---

## Testing Recommendations

Create tests for:
1. All error paths (database errors, API errors, timeouts)
2. Partial failures (e.g., some diagnoses succeed, others fail)
3. Error recovery (what happens when errors are transient?)
4. Error logging (verify appropriate log levels and context)
5. Exception propagation (errors reach the right handler level)

**Example Test Structure**:
```python
async def test_cli_mute_handles_not_found_error():
    """Verify mute command handles signature not found."""
    handler = CLICommandHandler(FakeManagementPort())

    # Should raise or return error response?
    with pytest.raises(ValueError, match="Signature not found"):
        # or should it be:
        result = await handler.mute_signature("nonexistent-id")
        assert result["status"] == "error"

async def test_daemon_investigation_continues_on_transient_error():
    """Verify daemon doesn't stop on transient diagnosis errors."""
    # Setup mock that raises TimeoutError on first call
    # Verify daemon logs warning but continues
    # Verify next cycle retries
```

---

## Conclusion

This PR introduces important new functionality but has **systematic issues with error handling that create silent failure modes**. Most critically:

1. **CLI commands silently convert exceptions to responses** - breaks error propagation
2. **Daemon scheduler allows diagnosis to fail silently** - core feature fails in background
3. **Error context is insufficient in multiple places** - debugging will be difficult

These issues should be addressed before merge, particularly for a system whose entire purpose is error diagnosis—it cannot afford to silently fail diagnoses.
