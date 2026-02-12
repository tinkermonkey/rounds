# Test Coverage Analysis - Quick Reference Guide

**File Under Review**: `/workspace/rounds/tests/test_new_implementations.py`
**Analysis Date**: February 12, 2026
**Test Status**: 26/26 tests passing
**Overall Coverage Quality**: 3.5/5 stars (Baseline coverage - happy paths only)

---

## Summary by Component

### 1. ManagementService
- **Tests**: 6/10 adequate
- **Critical Issue**: ⚠️ Timezone bug (datetime.utcnow() instead of datetime.now(tz=timezone.utc))
- **Missing Tests**: Database errors, state transitions, tag field
- **Add**: 5-7 tests

### 2. CLICommandHandler
- **Tests**: 7/10 adequate
- **Critical Issue**: ⚠️ Non-ValueError exceptions not caught
- **Missing Tests**: Large datasets, verbose logging, missing arguments
- **Add**: 4-5 tests

### 3. MarkdownNotificationAdapter
- **Tests**: 5/10 minimal
- **Critical Issue**: ⚠️ Concurrent write race condition NOT verified
- **Missing Tests**: Disk full, permission denied, file corruption scenarios
- **Add**: 8-10 tests

### 4. GitHubIssueNotificationAdapter
- **Tests**: 4/10 minimal
- **Critical Issue**: ⚠️ HTTP error codes (401, 403, 404, 422, 5xx) untested
- **Missing Tests**: Client lifecycle, auth failures, large data
- **Add**: 12-15 tests

### 5. JaegerTelemetryAdapter
- **Tests**: 1/10 critical gap
- **Critical Issue**: ⚠️ ZERO real functionality tested (456 lines untested)
- **Missing Tests**: API integration, parsing, tree building, error detection
- **Add**: 20+ tests

### 6. GrafanaStackTelemetryAdapter
- **Tests**: 1/10 critical gap
- **Critical Issue**: ⚠️ ZERO real functionality tested (462 lines untested)
- **Missing Tests**: Loki/Tempo/Prometheus integration, OTEL parsing
- **Add**: 20+ tests

---

## Critical Issues (Must Fix Immediately)

### Priority 1: Telemetry Adapters
**Status**: UNTESTED
**Lines of Code**: 918 total
**Impact**: Entire diagnostic pipeline depends on these
**Action**: Add mock-based integration tests for both adapters
**Effort**: 15-20 hours

### Priority 2: ManagementService Timezone Bug
**Status**: BUG EXISTS (not tested)
**File**: `/workspace/rounds/core/management_service.py` lines 52, 84, 116
**Impact**: Timestamps unreliable
**Action**: Fix code + add test to prevent regression
**Effort**: 1 hour

### Priority 3: GitHub Error Handling
**Status**: Untested
**Impact**: Silent failures when creating issues
**Action**: Add tests for all HTTP error codes
**Effort**: 4-5 hours

### Priority 4: Markdown Concurrency
**Status**: Code looks correct but NOT VERIFIED
**Impact**: Audit trail could be corrupted
**Action**: Add concurrent write test
**Effort**: 2 hours

### Priority 5: CLI Exception Handling
**Status**: Incomplete (only ValueError caught)
**Impact**: CLI crashes on unexpected errors
**Action**: Expand error handling + tests
**Effort**: 3 hours

---

## Test Gap Analysis by Severity

### CRITICAL (9-10/10) - Fix NOW
- [ ] Telemetry adapters: Zero real tests (918 lines untested)
- [ ] MarkdownNotificationAdapter: Concurrent write safety not verified
- [ ] ManagementService: Timezone bug (datetime.utcnow() bug)
- [ ] GitHubIssueNotificationAdapter: HTTP error handling (401, 403, 404)

### HIGH (7-8/10) - Fix This Sprint
- [ ] CLICommandHandler: Non-ValueError exceptions not caught
- [ ] Jaeger: Stack frame parsing untested
- [ ] Grafana: Tempo/Loki format parsing untested
- [ ] GitHub: Client lifecycle not verified
- [ ] Markdown: Write error scenarios (disk full, permissions)

### MEDIUM (5-6/10) - Fix Next Sprint
- [ ] ManagementService: State transitions not validated
- [ ] CLICommandHandler: Large dataset handling
- [ ] GitHub: Markdown injection in user data
- [ ] ManagementService: Tags field not tested

---

## Quick Implementation Guide

### Test Addition Checklist

For **each missing test**, ensure:

1. **Name is descriptive**
   ```python
   # BAD
   async def test_error(self): pass

   # GOOD
   async def test_report_github_api_unauthorized_401(self):
   ```

2. **Docstring explains "why" not "what"**
   ```python
   # BAD
   """Test handling 401."""

   # GOOD
   """Test that invalid GitHub token (401) is detected as auth failure.

   CRITICALITY: 9/10
   This distinguishes auth failure (don't retry) from transient error (retry).
   """
   ```

3. **Test verifies behavior, not implementation**
   ```python
   # BAD - Tests implementation detail
   assert logger.error.called

   # GOOD - Tests behavior
   assert result["status"] == "error"
   assert "unauthorized" in result["message"].lower()
   ```

4. **Mock is minimal and realistic**
   ```python
   # BAD - Too complex
   mock = MagicMock(spec=Everything)

   # GOOD - Only mock what's needed
   mock_response.status_code = 401
   mock_response.json.return_value = {"message": "Bad credentials"}
   ```

5. **Test handles both success and failure**
   ```python
   # Verify operation succeeds in happy path
   # Verify behavior changes in error scenarios
   # Verify no silent failures
   ```

---

## Files to Review/Modify

### Must Fix Code (1 file)
- `/workspace/rounds/core/management_service.py` - Fix timezone bug (3 lines)

### Must Add Tests To (1 file)
- `/workspace/rounds/tests/test_new_implementations.py` - Add 60-70 tests

### Reference Files (read-only)
- `/workspace/rounds/adapters/telemetry/jaeger.py` - Understand parsing logic
- `/workspace/rounds/adapters/telemetry/grafana_stack.py` - Understand parsing logic
- `/workspace/rounds/adapters/notification/github_issues.py` - Understand error paths
- `/workspace/rounds/core/ports.py` - Understand interface contracts

---

## Test Template

```python
@pytest.mark.asyncio
async def test_<component>_<scenario>_<expected_outcome>(
    self, <fixtures>, <mocks>
) -> None:
    """Test <what happens>.

    CRITICALITY: X/10
    ISSUE: <Why this matters>
    """
    # Arrange: Set up test data/mocks
    test_data = ...
    mock.side_effect = ...

    # Act: Perform the action
    result = await function(test_data)

    # Assert: Verify behavior
    assert result["status"] == "expected"
    assert "key" in result
    # Verify no side effects occurred
```

---

## Effort Estimation Summary

| Phase | Component | Tests | Hours | Priority |
|-------|-----------|-------|-------|----------|
| **1** | ManagementService (fix bug) | 5 | 2 | CRITICAL |
| **1** | CLICommandHandler | 5 | 4 | HIGH |
| **1** | MarkdownNotificationAdapter | 4 | 3 | CRITICAL |
| **1** | GitHub Adapter | 8 | 6 | CRITICAL |
| **Total Sprint 1** | | 22 | 15 | |
| | | | | |
| **2** | Jaeger Adapter | 15 | 10 | CRITICAL |
| **2** | Grafana Adapter | 15 | 10 | CRITICAL |
| **2** | ManagementService (state transitions) | 4 | 2 | HIGH |
| **Total Sprint 2** | | 34 | 22 | |
| | | | | |
| **3** | Advanced scenarios | 20 | 12 | MEDIUM |
| **Total Sprint 3** | | 20 | 12 | |
| | | | | |
| **TOTAL** | All improvements | 76 | 49 | |

**Estimated 2-week sprints: 3 sprints (6 weeks total)**

---

## Success Criteria

### Before (Current State)
- 26 tests
- ~50% code coverage
- 5 critical issues
- 0% telemetry adapter coverage
- Silent failure risks

### After (Target State)
- 90+ tests
- ~85% code coverage
- 0 critical issues
- 60% telemetry adapter coverage
- All error paths tested
- All edge cases covered
- Regressions prevented

---

## Key Resources

1. **TEST_COVERAGE_ANALYSIS.md** - Detailed analysis of each component
2. **CRITICAL_ISSUES_SUMMARY.md** - Top 6 production risks
3. **RECOMMENDED_TESTS.py** - 50+ ready-to-use test implementations
4. **This file** - Quick reference for decision-making

---

## Questions to Answer

**Q: Where should I start?**
A: Start with ManagementService timezone fix (1 hour, unblocks other work).

**Q: Which tests are most important?**
A: Telemetry adapters (918 untested lines). Then GitHub error handling. Then CLI exceptions.

**Q: Can I run tests to verify my additions?**
A: Yes: `pytest rounds/tests/test_new_implementations.py -v`

**Q: Do I need to add new test classes?**
A: No, add to existing classes in the same file for consistency.

**Q: What about mocking external services?**
A: Use `unittest.mock.patch` to mock httpx.AsyncClient responses (don't hit real APIs).

**Q: How do I know if my test is good?**
A: Good test:
- Has descriptive name
- Has docstring explaining WHY
- Tests behavior, not implementation
- Could catch a real regression
- Doesn't mock more than necessary

---

## Next Steps

1. Read CRITICAL_ISSUES_SUMMARY.md (5 minutes)
2. Read TEST_COVERAGE_ANALYSIS.md section for your component (15 minutes)
3. Copy relevant tests from RECOMMENDED_TESTS.py (30 minutes)
4. Run tests: `pytest rounds/tests/test_new_implementations.py -v` (5 minutes)
5. Add more edge case tests as you learn the code (2-4 hours per component)

**Total time to improve coverage to 80%: 6-8 weeks (assuming 50% time allocation)**

---

## Common Mistakes to Avoid

1. ❌ Testing implementation details instead of behavior
2. ❌ Not handling both success and failure cases
3. ❌ Creating tests that only pass when code is correct (not regression detection)
4. ❌ Over-mocking (mocking things that should be real)
5. ❌ Not testing edge cases (empty lists, None values, very large data)
6. ❌ Async tests without @pytest.mark.asyncio decorator
7. ❌ Not cleaning up resources (files, connections, locks)
8. ❌ Not verifying exception type is correct (just catching Exception)

---

## Review Checklist Before Submitting

- [ ] All 26 original tests still pass
- [ ] New tests follow naming convention
- [ ] New tests have docstrings explaining criticality
- [ ] No over-mocking (mock only external services)
- [ ] All async tests have @pytest.mark.asyncio
- [ ] Edge cases covered (empty, None, very large, negative)
- [ ] Error scenarios tested
- [ ] Fixtures properly scoped (function, class, or session)
- [ ] No hardcoded paths (use temp files for file tests)
- [ ] Code changes documented (if any)

---

## References

- Pytest docs: https://docs.pytest.org/
- Async testing: https://pytest-asyncio.readthedocs.io/
- Mock/patch: https://docs.python.org/3/library/unittest.mock.html
- This project's ports: `/workspace/rounds/core/ports.py`
- This project's models: `/workspace/rounds/core/models.py`

---

*Analysis completed: 2026-02-12*
*Analyst: Test Coverage Review System*
*Confidence Level: HIGH (deep code analysis + structural review)*
