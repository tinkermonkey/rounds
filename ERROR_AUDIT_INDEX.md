# Error Handling Audit - Complete Documentation Index

## Quick Start

Start here for a rapid overview:
- **ERROR_HANDLING_SUMMARY.txt** - One-page executive summary with key findings

## Complete Analysis

For thorough understanding:
1. **ERROR_HANDLING_AUDIT.md** - Detailed analysis of all 9 issues
   - Executive summary
   - Issue descriptions with severity levels
   - Why each is problematic
   - Hidden errors that could be masked
   - User impact analysis
   - Specific recommendations

## Implementation Guide

To fix the issues:
- **ERROR_HANDLING_FIXES.md** - Ready-to-use code fixes
  - Option A/B solutions for each issue
  - Full code examples
  - Implementation checklist
  - Testing recommendations

## Reference Materials

For context and cross-reference:
- **ISSUE_MATRIX.txt** - Visual priority matrix
  - All 9 issues organized by severity
  - File locations and line numbers
  - Quick fix priority order
  - Testing strategy outline

---

## Issue Summaries

### By Severity

#### CRITICAL (Fix Before Merge) - 3 Issues

1. **CLI Commands: Exception Swallowing** (6 locations)
   - `/workspace/rounds/adapters/cli/commands.py`
   - Lines: 50-78, 100-128, 146-171, 189-222, 288-337, 355-390
   - Methods: mute_signature, resolve_signature, retriage_signature, get_signature_details, list_signatures, reinvestigate_signature
   - Problem: Exceptions caught but converted to dict responses, preventing error propagation

2. **Daemon: Silent Investigation Cycle Failures**
   - `/workspace/rounds/adapters/scheduler/daemon.py`
   - Lines: 137-149
   - Problem: Diagnosis failures silently logged, daemon continues without awareness

3. **Claude Code: Unsafe JSON Parsing**
   - `/workspace/rounds/adapters/diagnosis/claude_code.py`
   - Lines: 210-221
   - Problem: Output truncated, parsing silently skips lines, errors hidden from logs

#### HIGH (Should Fix Before Merge) - 4 Issues

4. **Claude Code: Exception Re-wrapping**
   - `/workspace/rounds/adapters/diagnosis/claude_code.py`
   - Lines: 223-234
   - Problem: Redundant exception wrapping loses original context

5. **Telemetry: Silent Stack Parsing Failures**
   - `/workspace/rounds/adapters/telemetry/grafana_stack.py`
   - Lines: 256-258
   - Problem: Stack frames lost silently at debug level only

6. **Store: Inconsistent Exception Handling**
   - `/workspace/rounds/adapters/store/sqlite.py`
   - Lines: 385-390
   - Problem: Overly broad Exception catch masks logic errors

7. **Notifications: Missing Error Context**
   - `/workspace/rounds/adapters/notification/markdown.py`
   - Lines: 52-57, 72-77
   - Problem: File I/O errors lack diagnostic context

#### MEDIUM (Before Production) - 2 Issues

8. **Daemon: Budget Exceeded Case**
   - `/workspace/rounds/adapters/scheduler/daemon.py`
   - Lines: 112-119
   - Problem: Orphaned signatures not explicitly handled in logs

9. **CLI: Unsupported Format Logging**
   - `/workspace/rounds/adapters/cli/commands.py`
   - Lines: 208-213, 324-329
   - Problem: Invalid CLI input not logged

### By File

- **cli/commands.py** - 7 issues (6 critical, 1 medium)
- **scheduler/daemon.py** - 2 issues (1 critical, 1 medium)
- **diagnosis/claude_code.py** - 2 issues (1 critical, 1 high)
- **store/sqlite.py** - 1 issue (high)
- **notification/markdown.py** - 1 issue (high)
- **telemetry/grafana_stack.py** - 1 issue (high)

---

## Key Themes

### Silent Failures
- Exceptions swallowed without propagation
- Parse failures return empty results without indication
- Diagnosis failures continue daemon operation
- No visibility into actual vs. expected failures

### Inadequate Context
- Output truncated (200 chars)
- Missing operation type in error messages
- Missing file state information (errno)
- Exception context lost in re-wrapping
- Parse attempts not logged

### Inconsistent Patterns
- Some errors logged as ERROR, others as INFO/DEBUG
- Different handling for similar error types
- Some operations documented as raising exceptions but don't
- Broad exception catching vs. specific exceptions

---

## Implementation Priority

Recommended fix order by impact and complexity:

1. **CLI commands** (HIGH IMPACT, LOW COMPLEXITY)
   - 6 identical patterns
   - Straightforward fix (remove or propagate exceptions)
   - Estimated time: 30 minutes

2. **Daemon investigation cycle** (HIGH IMPACT, MEDIUM COMPLEXITY)
   - Core feature failure handling
   - Needs error type distinction
   - Estimated time: 1 hour

3. **Claude Code JSON parsing** (HIGH IMPACT, MEDIUM COMPLEXITY)
   - Safety issue
   - Full logging/context restoration
   - Estimated time: 45 minutes

4. **Telemetry, Store, Notification** (MEDIUM IMPACT, LOW COMPLEXITY)
   - Can be done in parallel
   - Logging improvements
   - Estimated time: 2 hours combined

5. **Medium severity items** (LOW IMPACT, LOW COMPLEXITY)
   - Nice-to-have improvements
   - Estimated time: 30 minutes

**Total estimated fix time: 4-5 hours**

---

## Testing Checklist

For each fix, add tests verifying:

- [ ] Error conditions trigger the error path
- [ ] Exceptions are raised (not swallowed)
- [ ] Errors are logged at correct level
- [ ] Error context is complete in logs
- [ ] Error messages are actionable
- [ ] Partial failures handled correctly
- [ ] Recovery paths work (retries, fallbacks)
- [ ] Monitoring/alerting integration works

---

## Use Cases Covered

These fixes ensure proper handling of:

1. **Database errors** - Corrupted rows, missing signatures, connection failures
2. **API failures** - GitHub API, Claude Code CLI, telemetry services
3. **Parsing failures** - JSON, stack traces, diagnosis results
4. **Resource errors** - Disk full, permission denied, budget exceeded
5. **Network errors** - Timeouts, connection refused, partial responses
6. **Logic errors** - Unexpected exception types from bugs
7. **Invalid input** - Unsupported formats, malformed data
8. **Transient errors** - Should retry vs. fail-fast

---

## Project Impact

These are critical for a **continuous error diagnosis system** because:

1. The system's core function is diagnosing errors - it cannot afford silent failures
2. An undiagnosed diagnosis failure is invisible and permanent
3. Administrators rely on clear error messages to debug system issues
4. Monitoring systems need proper exception signals to alert
5. Debugging production issues requires complete error context

---

## Questions & Answers

**Q: Why is exception swallowing critical?**
A: CLI handlers return error dicts instead of raising. Callers at higher levels (webhook, CLI) can't tell if an operation actually failed or if it was successful but returned an error dict. This breaks error handling chains and prevents proper retries.

**Q: Why is investigation cycle failure critical?**
A: Signatures get queued for diagnosis, but if diagnosis fails, there's no indication. The daemon logs "error" but continues normally. Administrators won't know that diagnosis isn't happening until they check logs.

**Q: Why is JSON parsing unsafe?**
A: The output is truncated to 200 chars (losing debugging info), the loop skips lines silently, and if parsing fails, there's no context about what was attempted. When Claude Code returns unusual formatting, you can't see what it actually returned.

**Q: Are these bugs or design issues?**
A: Mostly design issues. The code was written to catch exceptions and convert them to error responses (for graceful degradation), but for a reliability-focused system, this masks real failures. The fixes ensure failures are visible while maintaining graceful degradation where appropriate.

---

## Document Legend

- **AUDIT.md** = Detailed findings (long, thorough, reference)
- **FIXES.md** = Implementation guide (code examples, how-to)
- **SUMMARY.txt** = One-page overview (quick reference)
- **MATRIX.txt** = Visual issue reference (scanning)
- **INDEX.md** = This file (navigation, context)

---

## Further Reading

See CLAUDE.md for project conventions on:
- Error handling standards
- Logging functions (logForDebugging, logError, logEvent)
- Testing patterns
- Configuration management

---

Generated: 2026-02-12
Audit Scope: Feature branch `feature/issue-1-sketch-out-the-project-archite`
Total Issues: 9 (3 CRITICAL, 4 HIGH, 2 MEDIUM)
