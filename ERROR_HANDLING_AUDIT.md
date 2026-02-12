# Error Handling Audit Report
## Pull Request: Feature/Issue-1-Sketch-Out-The-Project-Architecture

**Date**: 2026-02-12
**Scope**: Complete error handling analysis for adapters and services
**Severity Categories**: CRITICAL, IMPORTANT, SUGGESTION

---

## Executive Summary

This PR introduces a comprehensive diagnostic system with multiple adapters and services handling external integrations (webhooks, databases, telemetry, notifications). The error handling analysis identified **7 CRITICAL issues** (data loss/silent failures), **8 IMPORTANT issues** (poor error messages, unvalidated inputs, missing context), and **6 SUGGESTIONS** (improvements to robustness).

**Key Risk Areas**:
1. Undefined variable in Jaeger trace building (runtime crash risk)
2. Silent failure in timestamp parsing without logging
3. JSON parsing errors swallowed without feedback
4. Missing error context in webhook handler
5. Enum conversion errors not validated in webhook receiver
6. Unvalidated string-to-enum conversion could crash
7. Missing error tracking for failures in investigation cycles

---

## CRITICAL ISSUES (Data Loss / Silent Failures)

### Issue 1: Undefined Variable in Jaeger Adapter
**Location**: `/workspace/rounds/adapters/telemetry/jaeger.py:423`
**Severity**: CRITICAL
**Category**: Runtime crash / Logic error

```python
def has_error(node: SpanNode) -> bool:
    # Check current span tags
    span = spans_by_id.get(span_id, {})  # spans_by_id is UNDEFINED
```

**Problem**: The variable `spans_by_id` is referenced but never defined in the `get_trace()` method. The code uses `span_dicts` earlier but references the undefined variable, causing a `NameError` at runtime whenever error span collection is attempted.

**Hidden Errors**:
- NameError when collecting error spans
- This will crash the trace retrieval for any trace that needs error span identification
- Caller will receive a generic exception without understanding it's a code bug

**User Impact**:
- Calling `get_trace()` with traces containing errors will crash
- User receives cryptic "Unexpected error fetching trace" message
- No indication of the actual coding error
- Any diagnostic system depending on error span identification will fail silently

**Recommendation**:
```python
# Build a mapping of span_id to span_data for error checking
spans_by_id = {span_id: span_dicts[span_id]["data"] for span_id in span_dicts}

# Then use it in has_error()
span = spans_by_id.get(span_id, {})
```

---

### Issue 2: Silent Timestamp Parsing Failure
**Location**: `/workspace/rounds/adapters/telemetry/grafana_stack.py:191-195`
**Severity**: CRITICAL
**Category**: Silent data loss

```python
timestamp = datetime.now(timezone.utc)
if "timestamp" in log_data:
    try:
        timestamp = datetime.fromisoformat(log_data["timestamp"])
    except (ValueError, TypeError):
        pass  # Silent failure!
```

**Problem**: When timestamp parsing fails, the code silently falls back to the current time without any logging, warning, or indication that the data was corrupted. This is a CRITICAL silent failure.

**Hidden Errors**:
- ValueError: timestamp string in unexpected format
- TypeError: timestamp not a string
- Both are caught and silently ignored

**User Impact**:
- Error event timestamps are incorrect (set to "now" instead of actual occurrence time)
- Correlations with logs/traces using timestamps will fail
- Time-based analysis will be misleading
- Users won't know their data is corrupted

**Recommendation**:
```python
if "timestamp" in log_data:
    try:
        timestamp = datetime.fromisoformat(log_data["timestamp"])
    except (ValueError, TypeError) as e:
        logger.warning(
            f"Failed to parse error event timestamp, using current time: {e}",
            extra={"raw_timestamp": log_data.get("timestamp")},
        )
        # Continue with current time as fallback
```

---

### Issue 3: JSON Parsing Silently Swallowed in Jaeger
**Location**: `/workspace/rounds/adapters/telemetry/jaeger.py:199-204`
**Severity**: CRITICAL
**Category**: Silent data loss

```python
try:
    if error_message.startswith("{"):
        error_data = json.loads(error_message)
        error_message = error_data.get("message", error_message)
except (json.JSONDecodeError, ValueError):
    pass  # Silent failure!
```

**Problem**: When JSON parsing of error messages fails, the code silently passes without logging. This hides potential data corruption or unexpected message formats.

**Hidden Errors**:
- json.JSONDecodeError: message doesn't contain valid JSON
- ValueError: message is not parseable
- Both silently ignored

**User Impact**:
- Malformed error messages may be stored with corrupted content
- Users won't know their error message data is invalid
- Error analysis will be based on incomplete data

**Recommendation**:
```python
try:
    if error_message.startswith("{"):
        error_data = json.loads(error_message)
        error_message = error_data.get("message", error_message)
except (json.JSONDecodeError, ValueError) as e:
    logger.debug(
        f"Failed to parse error message as JSON, using raw message: {e}",
        extra={"raw_message": error_message[:200]},
    )
    # Continue with original error_message
```

---

### Issue 4: Broad Exception Catching in Webhook HTTP Handler
**Location**: `/workspace/rounds/adapters/webhook/http_server.py:95-100`
**Severity**: CRITICAL
**Category**: Silent failure masking / Broad exception handling

```python
try:
    # Wait for result with timeout
    future.result(timeout=30)
except Exception as e:
    logger.error(f"Error handling webhook request: {e}", exc_info=True)
    self.send_error(500, f"Internal server error: {str(e)}")
```

**Problem**: This broad `except Exception` catches ALL exceptions including:
- System-level exceptions (KeyboardInterrupt, SystemExit)
- Timeout exceptions from the future result
- Programming errors (AttributeError, TypeError, NameError)
- The error message exposed to the user is just `str(e)`, which may contain sensitive internals

**Hidden Errors**: This catch block could hide:
- threading.TimeoutError
- asyncio.TimeoutError
- KeyError from result data
- AttributeError from missing attributes
- Any other unexpected error

**User Impact**:
- Users receive raw Python exception strings (not user-friendly)
- Internal implementation details leaked to clients
- Hard to debug which component actually failed
- Timeouts get lumped in with real errors

**Recommendation**:
```python
try:
    future.result(timeout=30)
except asyncio.TimeoutError:
    logger.error(
        f"Webhook request handler timed out (30s)",
        exc_info=True,
    )
    self.send_error(504, "Request handler timed out")
except Exception as e:
    logger.error(
        f"Webhook request handler raised unexpected error: {type(e).__name__}",
        exc_info=True,
    )
    self.send_error(500, "Internal server error")
```

---

### Issue 5: Unvalidated SignatureStatus Enum Conversion
**Location**: `/workspace/rounds/adapters/webhook/receiver.py:287`
**Severity**: CRITICAL
**Category**: Input validation / Silent error

```python
status_enum = None
if status:
    status_enum = SignatureStatus(status.lower())  # Could raise ValueError!
```

**Problem**: The code calls `SignatureStatus(status.lower())` without try-catch. If the user provides an invalid status string, this raises `ValueError` which will be caught by the outer try-catch and converted to a generic error response. The user gets no feedback about which status values are valid.

**Hidden Errors**:
- ValueError when status is "invalid_status" or any non-enum value
- User doesn't know what valid values are

**User Impact**:
- Webhook call returns 500 error with no explanation
- Users don't know the valid status values
- Appears to be a server error, not a client input error

**Recommendation**:
```python
status_enum = None
if status:
    try:
        status_enum = SignatureStatus(status.lower())
    except ValueError:
        return {
            "status": "error",
            "operation": "list",
            "message": f"Invalid status '{status}'. Must be one of: {', '.join(s.value for s in SignatureStatus)}",
        }
```

---

### Issue 6: Silent Failure in Investigation Cycle
**Location**: `/workspace/rounds/core/poll_service.py:144-154`
**Severity**: CRITICAL
**Category**: Data loss / Silent failures

```python
for signature in pending:
    try:
        if self.triage.should_investigate(signature):
            diagnosis = await self.investigator.investigate(signature)
            diagnoses.append(diagnosis)
    except Exception as e:
        logger.error(
            f"Failed to investigate signature {signature.fingerprint}: {e}",
            exc_info=True,
        )
        # Continue with next signature
```

**Problem**: When investigation fails, the exception is logged but the signature is NOT marked as having failed investigation. It remains in "NEW" status indefinitely, and the caller (execute_investigation_cycle) returns a PollResult that silently omits this failure. The caller has no way to know that investigations failed.

**Hidden Errors**:
- Any investigation failure (diagnosis engine crash, telemetry error, database error)
- Signature state remains unchanged - appears still waiting for investigation
- Caller receives incomplete diagnosis list with no indication of failures

**User Impact**:
- Some signatures are never diagnosed but appear to still be pending
- System appears to be stuck investigating those signatures
- No error reported to user - just missing results
- Impossible to know which signatures failed

**Recommendation**:
```python
diagnoses = []
investigation_errors = []
for signature in pending:
    try:
        if self.triage.should_investigate(signature):
            diagnosis = await self.investigator.investigate(signature)
            diagnoses.append(diagnosis)
    except Exception as e:
        logger.error(
            f"Failed to investigate signature {signature.fingerprint}: {e}",
            exc_info=True,
        )
        investigation_errors.append({
            "signature_id": signature.id,
            "error": str(e),
            "fingerprint": signature.fingerprint,
        })

if investigation_errors:
    logger.warning(
        f"Investigation cycle had {len(investigation_errors)} failures",
        extra={"failed_signatures": investigation_errors},
    )

return diagnoses
```

---

### Issue 7: Missing Error Propagation in Telemetry Queries
**Location**: `/workspace/rounds/adapters/telemetry/signoz.py:125-130`
**Severity**: CRITICAL
**Category**: Inconsistent error handling

```python
try:
    # ... telemetry query ...
except httpx.HTTPError as e:
    logger.error(f"Failed to fetch errors from SigNoz: {e}")
    raise
except Exception as e:
    logger.error(f"Unexpected error fetching errors: {e}")
    raise
```

**Problem**: Both exception handlers re-raise, but the second one catches ALL exceptions including programming errors, system errors, etc. This is too broad and masks specific error handling needs.

**Hidden Errors**: Catches and re-raises:
- AttributeError from bad data
- KeyError from missing response fields
- TypeError from type mismatches
- IndexError from accessing list wrong
All lumped under "unexpected error"

**User Impact**:
- Caller can't distinguish between network errors and programming errors
- Both are treated as "telemetry unavailable"
- Makes debugging impossible

**Recommendation**:
```python
try:
    # ... telemetry query ...
except httpx.HTTPError as e:
    logger.error(
        f"Failed to fetch errors from SigNoz (HTTP error): {e}",
        extra={"status_code": getattr(e.response, "status_code", None)},
    )
    raise
except (json.JSONDecodeError, ValueError) as e:
    logger.error(f"Failed to parse SigNoz response: {e}")
    raise
except Exception as e:
    logger.error(f"Unexpected error fetching errors from SigNoz: {type(e).__name__}: {e}")
    raise
```

---

## IMPORTANT ISSUES (Poor Error Handling / Missing Context)

### Issue 8: Missing Context in Webhook Receiver Error Logging
**Location**: `/workspace/rounds/adapters/webhook/receiver.py:135, 167, 196, 201, 234, 264, 312`
**Severity**: IMPORTANT
**Category**: Incomplete error logging

All these error handlers log with ONLY the exception message:

```python
except Exception as e:
    logger.error(f"Failed to mute signature via webhook: {e}")
    return { "status": "error", ... }
```

**Problem**: No `exc_info=True` to capture stack traces, no extra context about what operation was attempted, no error IDs for tracking in Sentry.

**Hidden Errors**: When these exceptions occur:
- Stack trace is lost (can't see where the error originated)
- No way to track errors in monitoring systems
- Context about the operation is missing

**User Impact**:
- Developers can't debug what went wrong
- No traceability for error tracking
- Operations team can't correlate errors

**Recommendation**:
```python
except Exception as e:
    logger.error(
        f"Failed to mute signature via webhook",
        extra={
            "signature_id": signature_id,
            "reason": reason,
            "error_type": type(e).__name__,
        },
        exc_info=True,
    )
```

---

### Issue 9: Missing Error IDs in Sentry Tracking
**Location**: All adapter error handlers
**Severity**: IMPORTANT
**Category**: Missing observability

Throughout the codebase, errors are logged without error IDs for Sentry tracking. The CLAUDE.md specification calls for error IDs from `constants/errorIds.ts` to be included.

**Examples**:
- `/workspace/rounds/adapters/store/sqlite.py:386` - `logger.error(f"Failed to parse database row: {e}")`
- `/workspace/rounds/adapters/notification/github_issues.py:112` - `logger.error(f"Failed to create GitHub issue: {e.response.status_code}")`
- `/workspace/rounds/adapters/telemetry/grafana_stack.py:160` - `logger.error(f"Failed to fetch errors from Grafana Stack: {e}")`

**Problem**: Without error IDs, Sentry can't group related errors together, making trend analysis impossible.

**User Impact**:
- Can't track if a particular error is a new issue or recurring
- Can't set up Sentry alerts for specific error types
- Harder to prioritize which errors to fix

---

### Issue 10: Insufficient User Error Messages
**Location**: `/workspace/rounds/adapters/webhook/receiver.py` (all handlers)
**Severity**: IMPORTANT
**Category**: User feedback

When operations fail, users get generic error strings like `str(e)`:

```python
return {
    "status": "error",
    "operation": "mute",
    "signature_id": signature_id,
    "message": str(e),  # Raw exception message!
}
```

**Problem**: Raw Python exception strings are not user-friendly and may leak implementation details.

**Examples of poor messages**:
- `"Signature 123 not found"` (OK, but could be clearer)
- `"_get_connection() missing 1 required positional argument: 'self'`" (BAD - implementation detail)
- `"[Errno 13] Permission denied: '/data/rounds.db'"` (BAD - system detail)

**User Impact**:
- Users don't understand what went wrong
- Technical details leak to client applications
- No actionable guidance on how to fix

**Recommendation**:
```python
try:
    await self.management_port.mute_signature(signature_id, reason)
    return {"status": "success", "operation": "mute", "signature_id": signature_id}
except ValueError as e:
    logger.error(f"Signature not found for muting", extra={"signature_id": signature_id})
    return {
        "status": "error",
        "operation": "mute",
        "message": f"Signature {signature_id} not found",
    }
except Exception as e:
    logger.error(f"Failed to mute signature", extra={"signature_id": signature_id}, exc_info=True)
    return {
        "status": "error",
        "operation": "mute",
        "message": "Failed to mute signature. Please check the server logs.",
    }
```

---

### Issue 11: No Timeout Handling in Database Operations
**Location**: `/workspace/rounds/adapters/store/sqlite.py`
**Severity**: IMPORTANT
**Category**: Missing error handling

All database operations (`get_by_id`, `save`, `update`, etc.) have no timeout handling. If the database locks up, the async tasks will hang indefinitely.

**Problem**:
- Long-running transactions could block the entire poll cycle
- No way to recover from database hangs
- Database locks from concurrent access could lock up the system

**User Impact**:
- Poll cycles hang and never complete
- Investigations never finish
- System appears frozen

**Recommendation**: Consider adding timeouts to connection access:
```python
async def _get_connection(self) -> aiosqlite.Connection:
    """Get a connection from the pool or create a new one."""
    try:
        async with asyncio.timeout(5):  # 5 second timeout
            async with self._pool_lock:
                if self._pool:
                    return self._pool.pop()
                else:
                    return await aiosqlite.connect(str(self.db_path))
    except asyncio.TimeoutError:
        logger.error("Database connection pool timeout")
        raise
```

---

### Issue 12: No Validation of HTTP Response Structure
**Location**: `/workspace/rounds/adapters/telemetry/signoz.py:108-122`
**Severity**: IMPORTANT
**Category**: Missing input validation

The code assumes response.json() has a specific structure without validation:

```python
data = response.json()
errors = []
for result in data.get("result", []):  # Assumes "result" key exists
    for value in result.get("values", []):  # Assumes nested structure
```

**Problem**: If the API returns a different structure or error response, this could fail silently or parse incorrect data.

**Hidden Errors**:
- TypeError if `data.get("result")` returns non-iterable
- KeyError if structure is different
- Invalid data silently processed

**Recommendation**:
```python
data = response.json()
if "result" not in data:
    logger.warning(f"Unexpected SigNoz response structure, missing 'result' key")
    return []

errors = []
for result in data.get("result", []):
    if not isinstance(result, dict) or "values" not in result:
        logger.warning(f"Skipping malformed result in SigNoz response: {result}")
        continue

    for value in result.get("values", []):
        ...
```

---

### Issue 13: Silent Retry Failures in Batch Operations
**Location**: `/workspace/rounds/adapters/telemetry/grafana_stack.py:391-411` and `/workspace/rounds/adapters/telemetry/jaeger.py:455-475`
**Severity**: IMPORTANT
**Category**: Incomplete results without caller awareness

The `get_traces()` methods try to fetch multiple traces but silently skip failures:

```python
async def get_traces(self, trace_ids: list[str]) -> list[TraceTree]:
    traces: list[TraceTree] = []
    for trace_id in trace_ids:
        try:
            trace = await self.get_trace(trace_id)
            traces.append(trace)
        except Exception as e:
            logger.warning(f"Failed to fetch trace {trace_id}: {e}")
            continue  # Silent skip!
    return traces
```

**Problem**: The caller gets a partial list of traces with no indication of which ones failed. It's impossible to know if results are incomplete.

**User Impact**:
- Diagnostic analysis uses incomplete trace data
- User doesn't know some traces are missing
- Root cause analysis may be based on incomplete information

**Recommendation** (from SigNoz docstring):
```python
async def get_traces(self, trace_ids: list[str]) -> list[TraceTree]:
    traces = []
    failed_trace_ids = []

    for trace_id in trace_ids:
        try:
            trace = await self.get_trace(trace_id)
            traces.append(trace)
        except Exception as e:
            logger.warning(f"Failed to fetch trace {trace_id}: {e}")
            failed_trace_ids.append(trace_id)

    if failed_trace_ids:
        logger.warning(
            f"Batch trace retrieval incomplete: "
            f"retrieved {len(traces)}/{len(trace_ids)} traces. "
            f"Failed IDs: {failed_trace_ids}"
        )

    return traces
```

---

## SUGGESTIONS (Code Improvements)

### Suggestion 1: Missing exc_info in HTTP Server Error Handler
**Location**: `/workspace/rounds/adapters/webhook/http_server.py:250`
**Severity**: SUGGESTION
**Category**: Logging best practice

```python
except Exception as e:
    logger.error(f"Webhook HTTP server error: {e}", exc_info=True)  # Good!
```

This is actually correct, but compare to the handlers below where `exc_info=True` is missing.

---

### Suggestion 2: Unclear Error Messages in Diagnosis Adapter
**Location**: `/workspace/rounds/adapters/diagnosis/claude_code.py:75-80`
**Severity**: SUGGESTION
**Category**: Error message clarity

```python
except (ValueError, TimeoutError, RuntimeError) as e:
    logger.error(f"Failed to diagnose: {e}")
    raise
except Exception as e:
    logger.error(f"Unexpected error during diagnosis: {e}")
    raise
```

The first handler doesn't distinguish between different error types. A ValueError (budget exceeded) is different from a TimeoutError (Claude Code timed out).

**Recommendation**:
```python
except ValueError as e:
    logger.error(f"Diagnosis budget exceeded: {e}")
    raise
except TimeoutError as e:
    logger.error(f"Diagnosis timed out: {e}")
    raise
except RuntimeError as e:
    logger.error(f"Claude Code runtime error: {e}")
    raise
except Exception as e:
    logger.error(f"Unexpected error during diagnosis: {type(e).__name__}: {e}")
    raise
```

---

### Suggestion 3: Inconsistent Error Handling in Database
**Location**: `/workspace/rounds/adapters/store/sqlite.py:308-391`
**Severity**: SUGGESTION
**Category**: Consistency

The `_row_to_signature()` method has good error handling with specific catch blocks, but other methods don't:

```python
# Good - specific catch blocks:
try:
    diagnosis = self._deserialize_diagnosis(diagnosis_json)
except (json.JSONDecodeError, KeyError, ValueError) as e:
    logger.warning(f"Failed to parse diagnosis for signature {sig_id}: {e}...")

# But catches final exception too broadly:
except Exception as e:
    logger.error(f"Unexpected error parsing database row: {e}")
    raise ValueError(f"Row parsing failed: {e}") from e
```

The final `except Exception` should also be specific about what types of errors it's catching.

---

### Suggestion 4: Missing Logging for Successful Async Operations
**Location**: `/workspace/rounds/adapters/webhook/http_server.py:237-250`
**Severity**: SUGGESTION
**Category**: Observability

The `_run_server()` method doesn't log successful requests, only errors:

```python
while True:
    self.server.handle_request()
    await asyncio.sleep(0.01)
```

At least at DEBUG level, requests should be logged so operators can see that the server is processing requests.

**Recommendation**:
```python
logger.debug("Handled webhook request")
```

---

### Suggestion 5: Missing Connection String Validation
**Location**: `/workspace/rounds/adapters/store/sqlite.py:32-33`
**Severity**: SUGGESTION
**Category**: Input validation

The SQLite store doesn't validate the `db_path` parameter:

```python
self.db_path = Path(db_path)
self.db_path.parent.mkdir(parents=True, exist_ok=True)  # Could fail if permission denied
```

If the parent directory can't be created (permission denied, no space, etc.), the error is silently ignored with `exist_ok=True`.

**Recommendation**:
```python
try:
    self.db_path = Path(db_path)
    self.db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.debug(f"SQLite store initialized at {self.db_path}")
except (PermissionError, OSError) as e:
    logger.error(f"Failed to initialize SQLite store: {e}")
    raise
```

---

### Suggestion 6: Incomplete Documentation of Error Propagation
**Location**: `/workspace/rounds/core/ports.py` (all abstract methods)
**Severity**: SUGGESTION
**Category**: Documentation

The port interfaces document that exceptions can be raised, but don't document what to do when:

```python
@abstractmethod
async def get_recent_errors(
    self, since: datetime, services: list[str] | None = None
) -> list[ErrorEvent]:
    """
    ...
    Raises:
        Exception: If telemetry backend is unreachable or returns error.
            Caller should handle gracefully (e.g., backoff retry).
    """
```

The recommendation to "handle gracefully" is vague. It should be specific about what the caller should do.

---

## Summary by File

### Critical Issues by File:
- **jaeger.py**: 1 (undefined variable)
- **grafana_stack.py**: 1 (silent timestamp failure)
- **jaeger.py**: 1 (silent JSON parsing)
- **http_server.py**: 1 (broad exception catching)
- **receiver.py**: 1 (unvalidated enum conversion)
- **poll_service.py**: 1 (silent investigation failures)

### Important Issues by File:
- **receiver.py**: 7 error handlers missing context
- **All adapters**: Missing error ID tracking
- **All adapters**: Raw exception messages to users
- **sqlite.py**: No timeout handling
- **signoz.py**: No response structure validation
- **grafana_stack.py, jaeger.py**: Silent batch failures

---

## Recommendations for Fix Priority

**Fix First (Blocking)**:
1. Issue 1: Fix undefined `spans_by_id` variable (crashes at runtime)
2. Issue 5: Validate SignatureStatus conversion (prevents 500 errors)
3. Issue 4: Catch specific exceptions in HTTP handler (prevents service crashes)

**Fix Second (High Impact)**:
4. Issue 2: Log timestamp parsing failures (data loss prevention)
5. Issue 3: Log JSON parsing failures (data loss prevention)
6. Issue 6: Track investigation failures (visibility)
7. Issue 7: Specific exception catching in telemetry (debuggability)

**Fix Third (Important)**:
8. Issue 8: Add context to webhook error logs (debuggability)
9. Issue 9: Add error IDs for Sentry (monitoring)
10. Issue 10: User-friendly error messages (UX)

**Fix Fourth (Nice to Have)**:
11. Issue 11: Add timeout handling to database (reliability)
12. Issue 12: Validate HTTP response structure (robustness)
13. Issue 13: Document incomplete batch results (clarity)

---

## Conclusion

The codebase has **7 critical silent failure risks** that could cause data loss, crashes, or availability issues. The most urgent fixes are the undefined variable in Jaeger (runtime crash) and the unvalidated enum conversion in webhooks (user-facing error).

The secondary issues (silent timestamp/JSON failures, missing investigation tracking) are data quality risks that will cause incorrect diagnostic results.

Implementation of these recommendations will significantly improve error handling quality, debuggability, and user experience.
