# Error Handling Fixes and Implementation Guide

This document provides concrete code solutions for the medium-severity issues identified in the audit.

---

## Issue 2.1: Grafana Stack Silent Empty Result (PRIORITY 1)

**File**: `/workspace/rounds/adapters/telemetry/grafana_stack.py`
**Lines**: 413-460

### Current Code (VIOLATES PORT CONTRACT)

```python
async def get_correlated_logs(
    self, trace_ids: list[str], window_minutes: int = 5
) -> list[LogEntry]:
    """Retrieve logs correlated with the given traces."""
    logs: list[LogEntry] = []

    try:
        # ... query logic ...
        return logs

    except Exception as e:
        logger.warning(f"Failed to fetch correlated logs: {e}")

    return logs  # SILENT RETURN - caller can't tell if error occurred
```

### Fixed Code

```python
async def get_correlated_logs(
    self, trace_ids: list[str], window_minutes: int = 5
) -> list[LogEntry]:
    """Retrieve logs correlated with the given traces.

    Returns:
        List of LogEntry objects correlated with the traces.
        Empty list if no logs found.

    Raises:
        httpx.HTTPError: If Loki API is unreachable or returns error.
        Exception: For unexpected errors during log retrieval.
    """
    logs: list[LogEntry] = []

    try:
        if not trace_ids:
            return []

        # Build LogQL query to correlate logs with traces
        # Escape trace IDs for LogQL queries
        trace_filter = "|".join(f'"{tid}"' for tid in trace_ids)
        query = f'trace_id=~{{{trace_filter}}}'

        response = await self.loki_client.get(
            "/loki/api/v1/query",
            params={"query": query},
        )

        if response.status_code == 200:
            data = response.json()
            streams = data.get("data", {}).get("result", [])

            for stream in streams:
                for timestamp, log_line in stream.get("values", []):
                    log_entry = LogEntry(
                        timestamp=datetime.fromtimestamp(int(timestamp) / 1e9, tz=timezone.utc),
                        severity=Severity.INFO,
                        body=log_line,
                        attributes={},
                        trace_id=None,
                        span_id=None,
                    )
                    logs.append(log_entry)

        return logs

    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch correlated logs: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error fetching logs: {e}")
        raise
```

### Why This Matters

- **Before**: Exception occurs → returns `[]` → caller sees empty logs → thinks no logs exist → diagnosis has incomplete context
- **After**: Exception occurs → exception propagates → caller logs it properly → knows investigation context is incomplete

---

## Issue 1.1: Claude Code Generic Exception Handler

**File**: `/workspace/rounds/adapters/diagnosis/claude_code.py`
**Lines**: 75-80

### Current Code

```python
except (ValueError, TimeoutError, RuntimeError) as e:
    logger.error(f"Failed to diagnose: {e}")
    raise
except Exception as e:
    logger.error(f"Unexpected error during diagnosis: {e}")
    raise
```

### Problem

The second catch block is too broad. Could hide:
- `AttributeError` if context fields are missing
- `asyncio.InvalidStateError` if event loop has issues
- `TypeError` from helper methods
- Any other bug in the adapter

### Fixed Code

```python
except (ValueError, TimeoutError, RuntimeError) as e:
    # Expected diagnosis failures - already logged in inner functions
    logger.error(f"Failed to diagnose: {e}")
    raise
except asyncio.CancelledError:
    # Task was cancelled - let it propagate
    raise
except asyncio.InvalidStateError as e:
    # Event loop issues
    logger.error(
        f"Diagnosis failed due to event loop error: {e}",
        exc_info=True
    )
    raise RuntimeError(
        f"Diagnosis adapter failed: event loop is in invalid state"
    ) from e
except (AttributeError, KeyError, TypeError) as e:
    # Likely bugs in this adapter (missing context fields, wrong response shape, etc.)
    logger.error(
        f"Diagnosis adapter bug (invalid input or response shape): {e}",
        exc_info=True
    )
    raise RuntimeError(
        f"Diagnosis adapter failed due to data structure error: {e}"
    ) from e
except Exception as e:
    # Anything else - still log but be clear it's unexpected
    logger.error(
        f"Unexpected error in diagnosis adapter: {e}",
        exc_info=True
    )
    raise RuntimeError(
        f"Diagnosis adapter failed unexpectedly: {e}"
    ) from e
```

### Why This Matters

When an unexpected error occurs, developers can now see:
1. What the error was (e.g., "AttributeError: 'InvestigationContext' has no attribute 'recent_events'")
2. That it's a bug in the adapter (not a diagnosis failure)
3. Full traceback with `exc_info=True`

---

## Issue 1.2: Claude Code Timeout Context

**File**: `/workspace/rounds/adapters/diagnosis/claude_code.py`
**Lines**: 204-205

### Current Code

```python
except subprocess.TimeoutExpired:
    raise TimeoutError("Claude Code CLI timed out after 60 seconds")
```

### Fixed Code

Add configurable timeout and include context in error message:

```python
# In __init__
def __init__(
    self,
    model: str = "claude-opus",
    budget_usd: float = 2.0,
    cli_timeout_seconds: int = 60,  # NEW
):
    self.model = model
    self.budget_usd = budget_usd
    self.cli_timeout_seconds = cli_timeout_seconds

# In _invoke_claude_code
async def _invoke_claude_code(self, prompt: str) -> dict[str, Any]:
    """..."""
    try:
        loop = asyncio.get_running_loop()

        def _run_claude_code() -> str:
            """Synchronous wrapper for subprocess call."""
            try:
                result = subprocess.run(
                    ["claude", "-p", prompt, "--output-format", "json"],
                    capture_output=True,
                    text=True,
                    timeout=self.cli_timeout_seconds,  # Use instance variable
                )

                if result.returncode != 0:
                    error_output = result.stderr or result.stdout
                    raise RuntimeError(f"Claude Code CLI failed: {error_output}")

                return result.stdout.strip()

            except subprocess.TimeoutExpired:
                # Include context about what was being diagnosed
                prompt_len = len(prompt)
                raise TimeoutError(
                    f"Claude Code CLI timed out after {self.cli_timeout_seconds}s. "
                    f"Prompt size: {prompt_len} chars. "
                    f"Consider reducing ERROR_LOOKBACK_MINUTES or increasing "
                    f"CLAUDE_CLI_TIMEOUT_SECONDS environment variable."
                )

        output = await loop.run_in_executor(None, _run_claude_code)
        # ... rest of method ...
```

### Why This Matters

Operators can now see:
1. How long the timeout was (not hardcoded)
2. How much context was being processed (prompt size)
3. Actionable steps: reduce lookback or increase timeout

---

## Issue 1.3: Claude Code JSON Parsing Context

**File**: `/workspace/rounds/adapters/diagnosis/claude_code.py`
**Lines**: 209-221

### Current Code

```python
# Parse the JSON output
lines = output.split("\n")
for line in lines:
    if line.startswith("{"):
        parsed: dict[str, Any] = json.loads(line)
        return parsed

# No valid JSON found - raise exception instead of returning synthetic data
raise ValueError(
    f"Claude Code CLI did not return valid JSON. Output: {output[:200]}"
)
```

### Problem

- Doesn't show which fields are missing from the JSON
- Doesn't indicate if JSON itself is malformed vs. wrong structure
- Doesn't validate response has required fields

### Fixed Code

```python
async def _invoke_claude_code(self, prompt: str) -> dict[str, Any]:
    """Invoke Claude Code CLI with the investigation prompt asynchronously.

    Raises:
        ValueError: If JSON parsing fails, required fields missing, or invalid format.
        TimeoutError: If CLI invocation times out.
        RuntimeError: If CLI returns non-zero exit code.
    """
    try:
        # ... subprocess execution ...
        output = await loop.run_in_executor(None, _run_claude_code)

        # Parse the JSON output
        lines = output.split("\n")
        json_lines = [line for line in lines if line.strip().startswith("{")]

        if not json_lines:
            # No JSON blocks found at all
            raise ValueError(
                f"Claude Code CLI returned no JSON blocks. "
                f"Output ({len(output)} chars): {output[:500]}"
            )

        # Try to parse each JSON line
        last_error = None
        for i, line in enumerate(json_lines):
            try:
                parsed: dict[str, Any] = json.loads(line)

                # Validate required fields
                required_fields = {
                    "root_cause",
                    "evidence",
                    "suggested_fix",
                    "confidence",
                }
                missing = required_fields - set(parsed.keys())

                if missing:
                    logger.debug(
                        f"JSON block {i} missing fields: {missing}. "
                        f"Response: {line[:200]}"
                    )
                    continue

                # All required fields present - return this response
                return parsed

            except json.JSONDecodeError as e:
                logger.debug(f"JSON decode error at line {i}: {e}")
                last_error = e
                continue

        # If we get here, no valid JSON with all required fields was found
        if last_error:
            raise ValueError(
                f"Claude Code CLI returned JSON but could not parse: {last_error}. "
                f"Output: {output[:300]}"
            ) from last_error
        else:
            raise ValueError(
                f"Claude Code CLI returned JSON blocks but none had required fields. "
                f"Required: {required_fields}. "
                f"Output: {output[:300]}"
            )

    except TimeoutError as e:
        logger.error(f"Claude Code CLI timeout: {e}")
        raise TimeoutError(str(e)) from e
    except RuntimeError as e:
        logger.error(f"Claude Code CLI error: {e}")
        raise RuntimeError(str(e)) from e
    except ValueError as e:
        logger.error(f"Failed to parse Claude Code response: {e}")
        raise ValueError(str(e)) from e
    except Exception as e:
        logger.error(f"Failed to invoke Claude Code: {e}", exc_info=True)
        raise
```

### Why This Matters

Now when JSON parsing fails, developers see:
1. Whether no JSON blocks were found (formatting issue)
2. Whether JSON was malformed (decode error)
3. Which required fields are missing (schema mismatch)
4. Full output for inspection (up to 300 chars)

---

## Issue 2.2: Clarify SigNoz Trace ID Validation

**File**: `/workspace/rounds/adapters/telemetry/signoz.py`
**Lines**: 276-281

### Current Code

```python
valid_trace_ids = [
    tid for tid in trace_ids if self._is_valid_trace_id(tid)
]
if not valid_trace_ids:
    logger.warning("No valid trace IDs provided")
    return []
```

### Recommendation

This is acceptable, but clarify the scenario:

```python
valid_trace_ids = [
    tid for tid in trace_ids if self._is_valid_trace_id(tid)
]

if not valid_trace_ids:
    # All provided trace IDs were invalid format
    # This could indicate:
    # 1. Fingerprinting bug (invalid trace IDs stored in database)
    # 2. Trace ID format changed upstream
    # 3. Corrupted signature data
    if trace_ids:  # Only log if IDs were provided
        logger.warning(
            f"All provided trace IDs failed validation. "
            f"Expected 32-char hex strings (OpenTelemetry format). "
            f"Provided: {trace_ids[:3]}... "
            f"(showing first 3 of {len(trace_ids)})"
        )
    return []
```

### Why This Matters

- Helps operators distinguish between "no logs found" and "invalid trace IDs"
- Shows example of invalid IDs for debugging
- Documents what valid trace IDs should look like

---

## Issue 4.1: Notification Fallback (Optional Enhancement)

**File**: `/workspace/rounds/core/investigator.py`
**Lines**: 128-136

### Option 1: Maintain Status Quo (Acceptable)

Keep current implementation - diagnosis is persisted, notification failure is logged. Developers check system regularly.

### Option 2: Simple Fallback (Recommended for Production)

```python
# In Investigator.__init__
def __init__(
    self,
    telemetry: TelemetryPort,
    store: SignatureStorePort,
    diagnosis_engine: DiagnosisPort,
    notification: NotificationPort,
    fallback_notification: NotificationPort | None = None,  # NEW
    triage: TriageEngine,
    codebase_path: str,
):
    self.notification = notification
    self.fallback_notification = fallback_notification

# In investigate() method
try:
    if self.triage.should_notify(signature, diagnosis, original_status=original_status):
        await self.notification.report(signature, diagnosis)
except Exception as e:
    logger.error(
        f"Primary notification failed for signature {signature.fingerprint}: {e}",
        exc_info=True,
    )

    # Try fallback notification if available
    if self.fallback_notification:
        try:
            await self.fallback_notification.report(signature, diagnosis)
            logger.info(
                f"Notification delivered via fallback channel for "
                f"signature {signature.fingerprint}"
            )
        except Exception as fallback_error:
            logger.error(
                f"Fallback notification also failed: {fallback_error}"
            )
    # Diagnosis is already persisted, so we don't re-raise
```

### Why This Matters

- GitHub notification fails? Fall back to stdout or email
- Ensures diagnosis is communicated even if primary channel is down
- Maintains original behavior if no fallback configured

---

## Issue 5.1: Daemon Backoff Strategy (Optional Enhancement)

**File**: `/workspace/rounds/adapters/scheduler/daemon.py`
**Lines**: 95-133

### Current Code

```python
while self.running:
    cycle_number += 1

    try:
        logger.debug(f"Starting poll cycle #{cycle_number}")
        result = await self.poll_port.execute_poll_cycle()
        # ... log result ...
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Error in poll cycle #{cycle_number}: {e}", exc_info=True)

    if self.running:
        await asyncio.sleep(self.poll_interval_seconds)
```

### Problem

- Repeats at fixed interval even if backend is down
- No way to detect repeated failures
- Cascading failure risk if backend is recovering

### Enhanced Code (with Backoff)

```python
async def _run_loop(self) -> None:
    """Main daemon loop with backoff on repeated failures."""
    cycle_number = 0
    loop = asyncio.get_running_loop()
    failure_count = 0
    max_failures_before_backoff = 3
    max_backoff_seconds = 3600  # 1 hour max

    while self.running:
        cycle_number += 1

        try:
            logger.debug(f"Starting poll cycle #{cycle_number}")

            start_time = loop.time()

            # Execute poll cycle
            result = await self.poll_port.execute_poll_cycle()

            elapsed = loop.time() - start_time

            logger.info(
                f"Poll cycle #{cycle_number} completed in {elapsed:.2f}s: "
                f"{result.errors_found} errors, "
                f"{result.new_signatures} new, "
                f"{result.updated_signatures} updated, "
                f"{result.investigations_queued} investigations queued"
            )

            # Reset failure count on success
            if failure_count > 0:
                logger.info(f"Poll cycle recovered. Resetting failure count.")
                failure_count = 0

        except asyncio.CancelledError:
            raise
        except Exception as e:
            failure_count += 1
            logger.error(
                f"Error in poll cycle #{cycle_number} "
                f"(failure #{failure_count}): {e}",
                exc_info=True
            )

        # Calculate sleep interval based on failure count
        sleep_seconds = self.poll_interval_seconds

        if failure_count >= max_failures_before_backoff:
            # Apply exponential backoff
            backoff_factor = 2 ** (failure_count - max_failures_before_backoff)
            sleep_seconds = min(
                self.poll_interval_seconds * backoff_factor,
                max_backoff_seconds
            )

            if failure_count == max_failures_before_backoff:
                # First time hitting backoff - log it
                logger.warning(
                    f"Repeated failures detected ({failure_count} consecutive). "
                    f"Applying exponential backoff. Next attempt in {sleep_seconds}s."
                )
            elif failure_count % 10 == 0:
                # Periodically log if still failing after many attempts
                logger.warning(
                    f"Still experiencing failures after {failure_count} attempts. "
                    f"Current backoff: {sleep_seconds}s. "
                    f"Check telemetry backend health."
                )

        # Wait before next cycle
        if self.running:
            try:
                await asyncio.sleep(sleep_seconds)
            except asyncio.CancelledError:
                raise
```

### Why This Matters

- **Before**: Backend down → hammers it every 60 seconds for hours
- **After**: Backend down → tries 3 times, then backs off to 5 min, 10 min, 20 min... up to 1 hour
- Reduces load on recovering backend
- Clearer logging about what's happening

---

## Testing Recommendations

### For Issue 2.1 (Grafana Stack Logs)

```python
@pytest.mark.asyncio
async def test_get_correlated_logs_raises_on_http_error():
    """Verify logs retrieval raises HTTPError instead of returning empty list."""
    adapter = GrafanaStackTelemetryAdapter(
        tempo_url="http://localhost:3200",
        loki_url="http://localhost:3100"
    )

    # Mock Loki client to raise HTTPError
    with patch.object(adapter.loki_client, 'get') as mock_get:
        mock_get.side_effect = httpx.ConnectError("Connection refused")

        # Should raise, not return empty list
        with pytest.raises(httpx.ConnectError):
            await adapter.get_correlated_logs(["trace123"])
```

### For Issue 1.1 (Claude Code Exception Handling)

```python
@pytest.mark.asyncio
async def test_diagnose_raises_on_invalid_context():
    """Verify AttributeError in context processing is surfaced clearly."""
    adapter = ClaudeCodeDiagnosisAdapter()

    # Create context with missing field
    context = MagicMock(spec=InvestigationContext)
    del context.recent_events  # Simulate missing field

    with pytest.raises(RuntimeError, match="bug.*data structure"):
        await adapter.diagnose(context)
```

---

## Summary Table

| Issue | File | Fix Complexity | Risk | Recommendation |
|-------|------|---|---|---|
| 2.1 | grafana_stack.py | Low | Low | **MUST FIX before merge** |
| 1.1 | claude_code.py | Medium | Low | Fix in next sprint |
| 1.2 | claude_code.py | Medium | Low | Fix in next sprint |
| 1.3 | claude_code.py | Medium | Low | Fix in next sprint |
| 2.2 | signoz.py | Low | Very Low | Clarify in next sprint |
| 4.1 | investigator.py | Medium | Low | Optional enhancement |
| 5.1 | daemon.py | Medium | Low | Optional enhancement |

