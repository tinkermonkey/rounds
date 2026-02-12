# Critical Error Handling Fixes - Code Examples

This document provides exact code changes needed to fix the 7 CRITICAL issues identified in the audit.

---

## FIX #1: Undefined Variable in Jaeger Adapter

**File**: `/workspace/rounds/adapters/telemetry/jaeger.py`
**Lines**: 410-446
**Severity**: CRITICAL (Runtime crash)

### Current (Broken) Code:
```python
# Build from root
if not root_span_id and span_dicts:
    root_span_id = next(iter(span_dicts.keys()))

if not root_span_id:
    raise ValueError(f"Could not find root span for trace {trace_id}")

root_node = build_span_node(root_span_id)

# Collect error spans for the trace
error_spans: list[SpanNode] = []
for span_id, node in span_dicts.items():
    # Check if this span or any descendant is an error
    def has_error(node: SpanNode) -> bool:
        # Check current span tags
        span = spans_by_id.get(span_id, {})  # ERROR: spans_by_id is UNDEFINED!
```

### Fixed Code:
```python
# Build from root
if not root_span_id and span_dicts:
    root_span_id = next(iter(span_dicts.keys()))

if not root_span_id:
    raise ValueError(f"Could not find root span for trace {trace_id}")

root_node = build_span_node(root_span_id)

# Build a mapping of span_id to span_data for error checking
spans_by_id = {span_id: span_dicts[span_id]["data"] for span_id in span_dicts}

# Collect error spans for the trace
error_spans: list[SpanNode] = []
for span_id, node in span_dicts.items():
    # Check if this span or any descendant is an error
    def has_error(node: SpanNode) -> bool:
        # Check current span tags
        span = spans_by_id.get(span_id, {})  # Now spans_by_id is defined!
```

---

## FIX #2: Silent Timestamp Parsing Failure

**File**: `/workspace/rounds/adapters/telemetry/grafana_stack.py`
**Lines**: 189-195
**Severity**: CRITICAL (Silent data loss)

### Current (Broken) Code:
```python
# Parse timestamp
timestamp = datetime.now(timezone.utc)
if "timestamp" in log_data:
    try:
        timestamp = datetime.fromisoformat(log_data["timestamp"])
    except (ValueError, TypeError):
        pass  # Silent failure - no logging!
```

### Fixed Code:
```python
# Parse timestamp
timestamp = datetime.now(timezone.utc)
if "timestamp" in log_data:
    try:
        timestamp = datetime.fromisoformat(log_data["timestamp"])
    except (ValueError, TypeError) as e:
        logger.warning(
            f"Failed to parse error event timestamp, using current time",
            extra={
                "raw_timestamp": log_data.get("timestamp"),
                "error": str(e),
            },
        )
        # Continue with current time as fallback
```

---

## FIX #3: Silent JSON Parsing Failure

**File**: `/workspace/rounds/adapters/telemetry/jaeger.py`
**Lines**: 197-204
**Severity**: CRITICAL (Silent data loss)

### Current (Broken) Code:
```python
# Parse error message if it's JSON
try:
    if error_message.startswith("{"):
        error_data = json.loads(error_message)
        error_message = error_data.get("message", error_message)
except (json.JSONDecodeError, ValueError):
    pass  # Silent failure - no logging!
```

### Fixed Code:
```python
# Parse error message if it's JSON
try:
    if error_message.startswith("{"):
        error_data = json.loads(error_message)
        error_message = error_data.get("message", error_message)
except (json.JSONDecodeError, ValueError) as e:
    logger.debug(
        f"Failed to parse error message as JSON, using raw message",
        extra={
            "raw_message": error_message[:200],
            "error": str(e),
        },
    )
    # Continue with original error_message
```

---

## FIX #4: Broad Exception Catching in HTTP Handler

**File**: `/workspace/rounds/adapters/webhook/http_server.py`
**Lines**: 84-100
**Severity**: CRITICAL (Error masking)

### Current (Broken) Code:
```python
def _run_async(self, coro: Any) -> None:
    """Run an async coroutine from a sync context."""
    if not self.event_loop:
        self.send_error(500, "Event loop not available")
        return

    # Schedule coroutine on the event loop
    future = asyncio.run_coroutine_threadsafe(coro, self.event_loop)
    try:
        # Wait for result with timeout
        future.result(timeout=30)
    except Exception as e:  # TOO BROAD - catches everything!
        logger.error(f"Error handling webhook request: {e}", exc_info=True)
        self.send_error(500, f"Internal server error: {str(e)}")
```

### Fixed Code:
```python
def _run_async(self, coro: Any) -> None:
    """Run an async coroutine from a sync context."""
    if not self.event_loop:
        self.send_error(500, "Event loop not available")
        return

    # Schedule coroutine on the event loop
    future = asyncio.run_coroutine_threadsafe(coro, self.event_loop)
    try:
        # Wait for result with timeout
        future.result(timeout=30)
    except asyncio.TimeoutError:
        logger.error(
            f"Webhook request handler timed out",
            extra={"timeout_seconds": 30},
            exc_info=True,
        )
        self.send_error(504, "Request timed out")
    except concurrent.futures.TimeoutError:
        logger.error(
            f"Webhook request handler timed out (futures)",
            extra={"timeout_seconds": 30},
            exc_info=True,
        )
        self.send_error(504, "Request timed out")
    except Exception as e:
        logger.error(
            f"Webhook request handler raised unexpected error",
            extra={"error_type": type(e).__name__},
            exc_info=True,
        )
        self.send_error(500, "Internal server error")
```

Don't forget to add import at the top:
```python
import concurrent.futures
```

---

## FIX #5: Unvalidated SignatureStatus Conversion

**File**: `/workspace/rounds/adapters/webhook/receiver.py`
**Lines**: 272-317
**Severity**: CRITICAL (User-facing 500 error)

### Current (Broken) Code:
```python
async def handle_list_request(self, status: str | None = None) -> dict[str, Any]:
    """Handle a request to list signatures."""
    try:
        # Convert string status to enum
        from rounds.core.models import SignatureStatus

        status_enum = None
        if status:
            status_enum = SignatureStatus(status.lower())  # Could raise ValueError!

        signatures = await self.management_port.list_signatures(status_enum)
        # ... rest of method ...
    except Exception as e:
        logger.error(f"Failed to list signatures via webhook: {e}")
        return {
            "status": "error",
            "operation": "list",
            "message": str(e),  # Raw exception message!
        }
```

### Fixed Code:
```python
async def handle_list_request(self, status: str | None = None) -> dict[str, Any]:
    """Handle a request to list signatures."""
    try:
        # Convert string status to enum
        from rounds.core.models import SignatureStatus

        status_enum = None
        if status:
            try:
                status_enum = SignatureStatus(status.lower())
            except ValueError:
                valid_values = ", ".join(s.value for s in SignatureStatus)
                logger.warning(
                    f"Invalid status filter requested via webhook",
                    extra={"requested_status": status, "valid_values": valid_values},
                )
                return {
                    "status": "error",
                    "operation": "list",
                    "message": f"Invalid status '{status}'. Valid values: {valid_values}",
                }

        signatures = await self.management_port.list_signatures(status_enum)
        # ... rest of method ...
    except Exception as e:
        logger.error(
            f"Failed to list signatures via webhook",
            extra={"error_type": type(e).__name__},
            exc_info=True,
        )
        return {
            "status": "error",
            "operation": "list",
            "message": "Failed to list signatures. Please check the server logs.",
        }
```

---

## FIX #6: Silent Investigation Failures

**File**: `/workspace/rounds/core/poll_service.py`
**Lines**: 130-157
**Severity**: CRITICAL (Missing visibility)

### Current (Broken) Code:
```python
async def execute_investigation_cycle(self) -> list[Diagnosis]:
    """Investigate pending signatures. Returns diagnoses produced."""
    try:
        pending = await self.store.get_pending_investigation()
    except Exception as e:
        logger.error(f"Failed to fetch pending signatures: {e}", exc_info=True)
        return []

    # Sort by priority
    pending.sort(
        key=lambda s: self.triage.calculate_priority(s), reverse=True
    )

    diagnoses = []
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
            # BUG: Caller doesn't know this signature failed!

    return diagnoses  # Incomplete list with no indication of failures
```

### Fixed Code:
```python
async def execute_investigation_cycle(self) -> list[Diagnosis]:
    """Investigate pending signatures. Returns diagnoses produced and tracks failures."""
    try:
        pending = await self.store.get_pending_investigation()
    except Exception as e:
        logger.error(f"Failed to fetch pending signatures: {e}", exc_info=True)
        return []

    # Sort by priority
    pending.sort(
        key=lambda s: self.triage.calculate_priority(s), reverse=True
    )

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
                extra={
                    "signature_id": signature.id,
                    "fingerprint": signature.fingerprint,
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            investigation_errors.append({
                "signature_id": signature.id,
                "fingerprint": signature.fingerprint,
                "error": str(e),
            })

    # Log summary of failures if any
    if investigation_errors:
        logger.warning(
            f"Investigation cycle had {len(investigation_errors)} failures",
            extra={
                "total_failed": len(investigation_errors),
                "failed_signatures": investigation_errors[:10],  # First 10 for logging
            },
        )

    return diagnoses
```

---

## FIX #7: Inconsistent Telemetry Error Handling

**File**: `/workspace/rounds/adapters/telemetry/signoz.py`
**Lines**: 125-130
**Severity**: CRITICAL (Error masking)

### Current (Broken) Code:
```python
try:
    # Build ClickHouse query for errors
    # ... query building ...
    response = await self.client.post(
        "/api/v1/query_range",
        json={"query": query},
    )
    response.raise_for_status()

    data = response.json()
    # ... process results ...

except httpx.HTTPError as e:
    logger.error(f"Failed to fetch errors from SigNoz: {e}")
    raise
except Exception as e:  # TOO BROAD - catches everything!
    logger.error(f"Unexpected error fetching errors: {e}")
    raise
```

### Fixed Code:
```python
try:
    # Build ClickHouse query for errors
    # ... query building ...
    response = await self.client.post(
        "/api/v1/query_range",
        json={"query": query},
    )
    response.raise_for_status()

    data = response.json()
    # ... process results ...

except httpx.HTTPStatusError as e:
    logger.error(
        f"SigNoz API returned error",
        extra={
            "status_code": e.response.status_code,
            "response": e.response.text[:500],
        },
        exc_info=True,
    )
    raise
except httpx.RequestError as e:
    logger.error(
        f"Failed to connect to SigNoz",
        extra={"error_type": type(e).__name__},
        exc_info=True,
    )
    raise
except json.JSONDecodeError as e:
    logger.error(
        f"SigNoz returned invalid JSON response: {e}",
        exc_info=True,
    )
    raise
except (KeyError, ValueError) as e:
    logger.error(
        f"SigNoz response had unexpected structure: {e}",
        extra={"error_type": type(e).__name__},
        exc_info=True,
    )
    raise
except Exception as e:
    logger.error(
        f"Unexpected error fetching errors from SigNoz: {type(e).__name__}",
        extra={"error": str(e)},
        exc_info=True,
    )
    raise
```

---

## Summary

These 7 fixes address the most critical issues in the error handling. They fall into these categories:

1. **Runtime Crashes** (Fixes #1, #4): Prevent undefined variables and timeout errors from crashing the system
2. **Silent Failures** (Fixes #2, #3, #6): Ensure failures are logged and visible to operators
3. **User Errors** (Fix #5): Provide helpful error messages for invalid input
4. **Debugging** (Fix #7): Distinguish between different error types for easier troubleshooting

All fixes follow the pattern:
- Catch specific exceptions (not broad `Exception`)
- Log with context using `extra={}` and `exc_info=True`
- Provide user-friendly messages
- Continue or return gracefully instead of silent failures

Estimated implementation time: **2-3 hours** for all critical fixes.
