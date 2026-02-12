# Error Handling Fixes - Implementation Guide

This document provides concrete code fixes for the critical error handling issues identified in the audit.

---

## Fix 1: CLI Command Handlers - Remove Silent Exception Swallowing

**File**: `/workspace/rounds/adapters/cli/commands.py`

**Current Problem**: Exception handlers catch ValueError and return error dict, preventing error propagation to caller.

**Option A: Propagate Exceptions (Recommended)**

This approach lets the caller (CLI/webhook handler) decide how to respond:

```python
async def mute_signature(
    self, signature_id: str, reason: str | None = None, verbose: bool = False
) -> dict[str, Any]:
    """Mute a signature via CLI.

    Args:
        signature_id: UUID of the signature to mute.
        reason: Optional reason for muting.
        verbose: If True, print additional information.

    Returns:
        Dictionary with status and message on success.

    Raises:
        ValueError: If signature doesn't exist or operation fails.
    """
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
    # Let ValueError from management port propagate
```

**Option B: Catch and Re-raise with Context (If Graceful Degradation Needed)**

If you need to catch for logging before propagation:

```python
async def mute_signature(
    self, signature_id: str, reason: str | None = None, verbose: bool = False
) -> dict[str, Any]:
    """Mute a signature via CLI.

    Raises:
        ValueError: If signature doesn't exist or operation fails.
    """
    try:
        await self.management.mute_signature(signature_id, reason)
    except ValueError as e:
        # Log with full context
        logger.error(
            f"Failed to mute signature {signature_id}: {e}",
            extra={
                "signature_id": signature_id,
                "reason": reason,
                "error_type": "signature_not_found" if "not found" in str(e).lower() else "unknown"
            },
            exc_info=True
        )
        # Re-raise for caller to handle
        raise

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
```

Apply this fix to all 6 methods:
- `mute_signature()`
- `resolve_signature()`
- `retriage_signature()`
- `get_signature_details()`
- `list_signatures()`
- `reinvestigate_signature()`

---

## Fix 2: Claude Code Adapter - Safe JSON Parsing with Full Error Context

**File**: `/workspace/rounds/adapters/diagnosis/claude_code.py` lines 210-221

**Current Problem**: Loop silently skips lines, no logging of attempts, output truncated to 200 chars.

**Improved Version**:

```python
async def _invoke_claude_code(self, prompt: str) -> dict[str, Any]:
    """Invoke Claude Code CLI with the investigation prompt asynchronously.

    Raises:
        ValueError: If JSON parsing fails or no valid JSON found in output.
        TimeoutError: If CLI invocation times out.
        RuntimeError: If CLI returns non-zero exit code.
    """
    try:
        # Invoke Claude Code CLI in an executor to avoid blocking the event loop
        loop = asyncio.get_running_loop()

        def _run_claude_code() -> str:
            """Synchronous wrapper for subprocess call."""
            try:
                result = subprocess.run(
                    ["claude", "-p", prompt, "--output-format", "json"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

                if result.returncode != 0:
                    error_output = result.stderr or result.stdout
                    raise RuntimeError(f"Claude Code CLI failed: {error_output}")

                return result.stdout.strip()

            except subprocess.TimeoutExpired:
                raise TimeoutError("Claude Code CLI timed out after 60 seconds")

        # Run in executor to avoid blocking
        output = await loop.run_in_executor(None, _run_claude_code)

        # Parse the JSON output with detailed error handling
        lines = output.split("\n")
        parse_attempts = []

        for line_num, line in enumerate(lines):
            if not line.startswith("{"):
                continue  # Skip non-JSON lines

            try:
                parsed: dict[str, Any] = json.loads(line)

                # Validate parsed content has expected fields
                required_fields = {"root_cause", "evidence", "suggested_fix", "confidence"}
                missing_fields = required_fields - set(parsed.keys())
                if missing_fields:
                    logger.warning(
                        f"Parsed JSON missing required fields on line {line_num}",
                        extra={"missing_fields": list(missing_fields)},
                    )
                    continue  # Try next line

                logger.debug(f"Successfully parsed JSON from line {line_num}")
                return parsed

            except json.JSONDecodeError as e:
                parse_attempts.append({
                    "line_num": line_num,
                    "error": str(e),
                    "line_preview": line[:100]
                })
                logger.debug(
                    f"Failed to parse line {line_num} as JSON: {e}",
                    extra={"line_preview": line[:100]}
                )

        # No valid JSON found
        error_details = (
            f"Claude Code CLI did not return valid JSON. "
            f"Output has {len(lines)} lines. "
            f"Attempted to parse {len(parse_attempts)} JSON-like lines, all failed."
        )

        if parse_attempts:
            error_details += f" First error: {parse_attempts[0]['error']}"

        error_details += f" Full output (first 500 chars): {output[:500]}"

        logger.error(error_details, extra={"parse_attempts": len(parse_attempts)})
        raise ValueError(error_details)

    except TimeoutError as e:
        logger.error(f"Claude Code CLI timeout: {e}", exc_info=True)
        raise
    except RuntimeError as e:
        logger.error(f"Claude Code CLI error: {e}", exc_info=True)
        raise
    except ValueError as e:
        logger.error(f"Failed to parse Claude Code response: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Failed to invoke Claude Code: {e}", exc_info=True)
        raise
```

---

## Fix 3: Daemon Scheduler - Distinguish Error Types in Investigation Cycle

**File**: `/workspace/rounds/adapters/scheduler/daemon.py` lines 137-149

**Current Problem**: All exceptions treated the same, no retry logic, no error categorization.

**Improved Version**:

```python
# Execute investigation cycle for pending diagnoses
if result.investigations_queued > 0:
    logger.debug(f"Starting investigation cycle #{cycle_number}")
    try:
        diagnoses = await self.poll_port.execute_investigation_cycle()
        logger.info(
            f"Investigation cycle #{cycle_number} completed: "
            f"{len(diagnoses)} diagnoses produced",
            extra={
                "cycle": cycle_number,
                "diagnoses_count": len(diagnoses),
            }
        )

    except ValueError as e:
        # Expected errors: budget exceeded, validation failures
        error_str = str(e).lower()

        if "budget" in error_str or "cost" in error_str:
            # Budget exceeded is expected and should be info level
            logger.info(
                f"Investigation cycle #{cycle_number} skipped: {e}",
                extra={
                    "cycle": cycle_number,
                    "reason": "budget_exceeded",
                }
            )
        else:
            # Other validation errors are unexpected
            logger.error(
                f"Validation error in investigation cycle #{cycle_number}: {e}",
                extra={
                    "cycle": cycle_number,
                    "error_type": "validation_error",
                },
                exc_info=True,
            )

    except (TimeoutError, RuntimeError) as e:
        # Transient errors - should retry next cycle
        error_type = type(e).__name__
        logger.warning(
            f"Transient error in investigation cycle #{cycle_number}: {error_type}: {e}. "
            f"Will retry next cycle.",
            extra={
                "cycle": cycle_number,
                "error_type": error_type,
                "retryable": True,
            },
            exc_info=True,
        )
        # Don't propagate - continue to next cycle for retry

    except Exception as e:
        # Completely unexpected errors
        logger.error(
            f"Unexpected error in investigation cycle #{cycle_number}: {e}",
            extra={
                "cycle": cycle_number,
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        # Decide: should we stop the daemon or continue?
        # For now continue but alert to monitoring
        # Could add: raise or self._alert_critical_error(e)
```

Also update the poll cycle error handler:

```python
except asyncio.CancelledError:
    raise
except Exception as e:
    logger.error(
        f"Error in poll cycle #{cycle_number}: {e}",
        extra={
            "cycle": cycle_number,
            "error_type": type(e).__name__,
        },
        exc_info=True,
    )
    # For poll errors, we want to continue and retry
    # This is where errors_found=0 would be reported
```

---

## Fix 4: Telemetry Adapter - Proper Stack Frame Parsing Error Handling

**File**: `/workspace/rounds/adapters/telemetry/grafana_stack.py` lines 256-258

**Current Problem**: Exception swallowed at debug level, returns empty list silently.

**Improved Version**:

```python
@staticmethod
def _parse_stack_frames(stack_str: str) -> list[StackFrame]:
    """Parse stack frames from a stack trace string.

    Args:
        stack_str: Stack trace as a string.

    Returns:
        List of StackFrame objects. May be empty if parsing fails.
    """
    frames: list[StackFrame] = []

    try:
        lines = stack_str.split("\n")
        for line in lines:
            line = line.strip()
            if not line or line.startswith("Traceback"):
                continue

            # Parse frame line: "File "path.py", line N, in function"
            if "File" in line and "in " in line:
                try:
                    parts = line.split(", ")
                    if len(parts) >= 2:
                        filename = (
                            parts[0]
                            .replace('File "', "")
                            .replace('"', "")
                        )
                        function = (
                            parts[-1]
                            .replace("in ", "")
                            .strip()
                        )
                        module = filename.replace(".py", "").replace("/", ".")

                        frame = StackFrame(
                            module=module,
                            function=function,
                            filename=filename,
                            lineno=None,
                        )
                        frames.append(frame)

                except (ValueError, IndexError) as e:
                    # Log individual frame parsing errors at debug level
                    logger.debug(
                        f"Failed to parse individual stack frame: {e}",
                        extra={"line": line[:100], "error": str(e)}
                    )
                    # Continue parsing remaining frames

    except Exception as e:
        # Top-level parsing failure
        logger.warning(
            f"Failed to parse stack frames from stack trace: {e}",
            extra={
                "stack_length": len(stack_str),
                "error_type": type(e).__name__,
                "frames_parsed_before_error": len(frames),
            },
            exc_info=True,
        )
        # Log suggests partial parsing happened
        # Return what we have (partial frames are better than none)

    return frames
```

---

## Fix 5: Store Adapter - Consistent Exception Handling

**File**: `/workspace/rounds/adapters/store/sqlite.py` lines 385-390

**Current Problem**: Overly broad catch block, inconsistent handling.

**Improved Version**:

```python
def _row_to_signature(self, row: tuple[Any, ...]) -> Signature:
    """Convert a database row to a Signature object.

    Raises:
        ValueError: If row is malformed or contains invalid data.
    """
    try:
        if not row or len(row) != 12:
            raise ValueError(f"Invalid row length: expected 12, got {len(row) if row else 0}")

        (
            sig_id,
            fingerprint,
            error_type,
            service,
            message_template,
            stack_hash,
            first_seen,
            last_seen,
            occurrence_count,
            status,
            diagnosis_json,
            tags_json,
        ) = row

        # Validate required fields
        if not sig_id or not fingerprint:
            raise ValueError("Missing required fields: id or fingerprint")

        # Parse dates
        try:
            first_seen_dt = datetime.fromisoformat(first_seen)
            last_seen_dt = datetime.fromisoformat(last_seen)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid date format: {e}") from e

        # Validate occurrence_count
        if not isinstance(occurrence_count, int) or occurrence_count < 0:
            raise ValueError(f"Invalid occurrence_count: {occurrence_count}")

        # Parse diagnosis (optional, failures don't stop the row)
        diagnosis = None
        if diagnosis_json:
            try:
                diagnosis = self._deserialize_diagnosis(diagnosis_json)
            except json.JSONDecodeError as e:
                logger.warning(
                    f"Failed to parse diagnosis JSON for signature {sig_id}: {e}",
                    extra={
                        "signature_id": sig_id,
                        "diagnosis_json": diagnosis_json[:100],
                    }
                )
                diagnosis = None
            except (KeyError, ValueError) as e:
                logger.warning(
                    f"Failed to deserialize diagnosis for signature {sig_id}: {e}",
                    extra={"signature_id": sig_id},
                )
                diagnosis = None

        # Parse tags (optional, failures don't stop the row)
        try:
            tags = frozenset(json.loads(tags_json))
        except json.JSONDecodeError as e:
            logger.warning(
                f"Failed to parse tags JSON for signature {sig_id}: {e}",
                extra={"signature_id": sig_id, "tags_json": tags_json[:100]},
            )
            tags = frozenset()
        except TypeError as e:
            logger.warning(
                f"Tags JSON is not a list for signature {sig_id}: {e}",
                extra={"signature_id": sig_id},
            )
            tags = frozenset()

        return Signature(
            id=sig_id,
            fingerprint=fingerprint,
            error_type=error_type,
            service=service,
            message_template=message_template,
            stack_hash=stack_hash,
            first_seen=first_seen_dt,
            last_seen=last_seen_dt,
            occurrence_count=occurrence_count,
            status=SignatureStatus(status),
            diagnosis=diagnosis,
            tags=tags,
        )

    except ValueError as e:
        # Validation errors are expected and should propagate
        logger.error(
            f"Failed to parse database row: {e}",
            extra={"row": row},
            exc_info=True,
        )
        raise
    except Exception as e:
        # Unexpected errors (KeyError in unpacking, etc.)
        logger.error(
            f"Unexpected error parsing database row: {type(e).__name__}: {e}",
            extra={"row": row, "error_type": type(e).__name__},
            exc_info=True,
        )
        raise ValueError(f"Row parsing failed: {e}") from e
```

---

## Fix 6: Notification Adapters - Add Error Context for File I/O

**File**: `/workspace/rounds/adapters/notification/markdown.py` lines 52-57, 72-77

**Current Problem**: Missing context about what was being written, why it failed.

**Improved Version**:

```python
async def report(
    self, signature: Signature, diagnosis: Diagnosis
) -> None:
    """Report a diagnosed signature to markdown file.

    Raises:
        IOError: If file write fails.
    """
    # Format the report entry
    entry = self._format_report_entry(signature, diagnosis)

    # Append to file
    async with self._lock:
        try:
            await asyncio.to_thread(self._write_to_file, entry)

            logger.info(
                f"Appended diagnosis report to {self.report_path}",
                extra={
                    "signature_id": signature.id,
                    "fingerprint": signature.fingerprint,
                    "operation": "report",
                },
            )

        except IOError as e:
            logger.error(
                f"Failed to write diagnosis report to {self.report_path}: {e}",
                extra={
                    "path": str(self.report_path),
                    "signature_id": signature.id,
                    "error_code": getattr(e, 'errno', None),
                    "operation": "report",
                    "is_permission_denied": e.errno == 13 if hasattr(e, 'errno') else False,
                    "is_disk_full": e.errno == 28 if hasattr(e, 'errno') else False,
                },
                exc_info=True,
            )
            raise IOError(
                f"Failed to persist diagnosis report to {self.report_path}: {e}. "
                f"This prevents audit trail creation for signature {signature.id}."
            ) from e

async def report_summary(self, stats: dict[str, Any]) -> None:
    """Periodic summary report appended to markdown file.

    Raises:
        IOError: If file write fails.
    """
    summary = self._format_summary(stats)

    async with self._lock:
        try:
            await asyncio.to_thread(self._write_to_file, summary)

            logger.info(
                f"Appended summary report to {self.report_path}",
                extra={
                    "stats": stats,
                    "operation": "summary",
                    "total_signatures": stats.get('total_signatures', 0),
                },
            )

        except IOError as e:
            logger.error(
                f"Failed to write summary report to {self.report_path}: {e}",
                extra={
                    "path": str(self.report_path),
                    "error_code": getattr(e, 'errno', None),
                    "operation": "summary",
                },
                exc_info=True,
            )
            raise IOError(
                f"Failed to persist summary report to {self.report_path}: {e}. "
                f"This prevents audit trail updates."
            ) from e
```

---

## Implementation Checklist

When implementing these fixes, ensure:

- [ ] All catch blocks log with `exc_info=True` to preserve tracebacks
- [ ] Error messages include enough context to debug the issue
- [ ] Errors that should propagate are not silently swallowed
- [ ] Debug-level logs are used for expected failures (not INFO/ERROR)
- [ ] ERROR level is used for unexpected failures that need investigation
- [ ] WARNING level is used for transient errors that will retry
- [ ] All exceptions include error IDs for Sentry tracking (if applicable)
- [ ] Tests verify exception propagation
- [ ] Tests verify error logging at appropriate levels

---

## Testing These Fixes

Add tests like:

```python
# Test CLI command propagates exceptions
async def test_mute_signature_propagates_not_found():
    mgmt = FakeManagementPort()
    mgmt.should_raise = ValueError("Signature not found")
    handler = CLICommandHandler(mgmt)

    with pytest.raises(ValueError, match="Signature not found"):
        await handler.mute_signature("nonexistent")

# Test Claude Code handles partial JSON
async def test_claude_code_handles_partial_json_output():
    adapter = ClaudeCodeDiagnosisAdapter()
    # Mock _invoke_claude_code to return partial JSON

    # Should handle gracefully or raise with full context

# Test daemon continues on transient errors
async def test_daemon_retries_on_timeout():
    poll = FakePollPort()
    poll.timeout_on_first_call = True
    daemon = DaemonScheduler(poll)

    # Verify daemon logs warning but continues
    # Verify next cycle retries

# Test notification error includes context
async def test_markdown_notification_reports_disk_full_error():
    notif = MarkdownNotificationAdapter("/nonexistent/path/report.md")

    with pytest.raises(IOError):
        await notif.report(signature, diagnosis)

    # Verify error log includes path, operation, errno
```

---

## Priority Implementation Order

1. **CLI commands** - Most critical, blocks error propagation
2. **Daemon investigation cycle** - Core feature fails silently
3. **Claude Code adapter** - JSON parsing with better logging
4. **Telemetry stack parsing** - Improves investigation context
5. **Store adapter** - Consistency improvements
6. **Notification errors** - Better debugging

All of these should be fixed before merge for a production error diagnosis system.
