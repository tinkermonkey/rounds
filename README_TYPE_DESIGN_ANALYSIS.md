# Type Design Analysis for Rounds Project

## Quick Start

**Read this first**: [ANALYSIS_OVERVIEW.md](ANALYSIS_OVERVIEW.md) (5-10 minutes)

**Then pick your path**:
- **I want concrete code examples**: [TYPE_DESIGN_IMPROVEMENTS.md](TYPE_DESIGN_IMPROVEMENTS.md)
- **I want detailed analysis**: [TYPE_DESIGN_ANALYSIS.md](TYPE_DESIGN_ANALYSIS.md)
- **I want visual diagrams**: [TYPE_DESIGN_VISUAL_GUIDE.md](TYPE_DESIGN_VISUAL_GUIDE.md)
- **I want executive summary**: [TYPE_DESIGN_SUMMARY.md](TYPE_DESIGN_SUMMARY.md)

---

## Analysis Overview

| Document | Purpose | Audience | Time | Size |
|----------|---------|----------|------|------|
| ANALYSIS_OVERVIEW.md | High-level summary | Everyone | 5-10 min | 12 KB |
| TYPE_DESIGN_SUMMARY.md | Executive summary with key findings | Leads, Architects | 10-15 min | 8 KB |
| TYPE_DESIGN_ANALYSIS.md | Deep detailed analysis with all concerns | Developers, Architects | 30-40 min | 32 KB |
| TYPE_DESIGN_IMPROVEMENTS.md | Ready-to-use code examples and patterns | Developers | 20-30 min | 23 KB |
| TYPE_DESIGN_VISUAL_GUIDE.md | Visual diagrams, matrices, and graphs | Visual learners | 15-20 min | 30 KB |

---

## Key Findings

### Overall Assessment: 7.5/10

**Strengths**:
- Frozen dataclasses prevent mutation
- Enum types ensure valid string values
- Clean port abstraction
- Complete type annotations
- Construction-time validation

**Critical Issues** (Fix First):
1. **Diagnosis has no validation** - cost_usd could be negative, evidence could be empty
2. **Enum parsing is scattered** - Confidence and SignatureStatus parsing duplicated in adapters
3. **State transitions are implicit** - Signature status can be mutated to invalid states

**Medium Issues**:
4. Generic exceptions in ports
5. ErrorEvent missing field validation
6. State transition rules distributed across services

---

## Improvement Roadmap

### Phase 1: Critical (2 hours)
- Add Diagnosis.__post_init__() validation
- Create ModelParsers class
- Add Signature.set_status() helper
- **Gain**: +0.5 points (7.5 → 8.0)

### Phase 2: High Priority (1 hour)
- Define specific exception types
- Add ErrorEvent validation
- **Gain**: +0.3 points (8.0 → 8.3)

### Phase 3: Medium Priority (1 hour)
- Create SignatureStatusTransition class
- **Gain**: +0.1 points (8.3 → 8.4)

### Phase 4: Polish (1 hour)
- Add type aliases
- Additional helpers
- **Gain**: +0.1 points (8.4 → 8.5)

**Total Effort**: ~5 hours | **Code Changes**: ~300 lines | **Breaking Changes**: 0

---

## Critical Issue Details

### Issue 1: Diagnosis Validation Gap
**File**: `/workspace/rounds/core/models.py:80-90`

**Problem**: Diagnosis is frozen but has no validation in __post_init__:
```python
cost_usd = -5.0          # INVALID - not caught
evidence = ()            # INVALID - not caught
root_cause = ""          # INVALID - not caught
```

**Impact**: Invalid diagnosis objects persist in system, breaking cost tracking and notifications.

**Fix**: Add validation (~15 lines, 15 minutes)
```python
def __post_init__(self) -> None:
    if self.cost_usd < 0:
        raise ValueError(f"cost_usd must be non-negative, got {self.cost_usd}")
    if not self.evidence:
        raise ValueError("evidence tuple cannot be empty")
    if not self.root_cause or not self.root_cause.strip():
        raise ValueError("root_cause cannot be empty")
    # ... more validation
```

---

### Issue 2: Scattered Enum Parsing
**Locations**:
- `/workspace/rounds/adapters/store/sqlite.py:415`
- `/workspace/rounds/adapters/diagnosis/claude_code.py:266`
- `/workspace/rounds/adapters/store/sqlite.py:380`

**Problem**: Each adapter implements its own enum parsing with try/except:
```python
# SQLite adapter
confidence=Confidence(data["confidence"]),  # Direct, needs try/except

# Claude Code adapter
confidence = Confidence(confidence_str.lower())  # Different pattern, different error handling
```

**Impact**: Duplicated logic, inconsistent error handling, hard to maintain.

**Fix**: Create ModelParsers class (~30 lines, 30 minutes)
```python
class ModelParsers:
    @staticmethod
    def parse_confidence(value: str) -> Confidence:
        try:
            return Confidence(value.lower())
        except ValueError:
            raise ValueError(f"Invalid confidence '{value}'...") from None
```

---

### Issue 3: Unvalidated State Mutations
**File**: `/workspace/rounds/core/investigator.py:89`

**Problem**: Signature status can be mutated directly without validation:
```python
signature.status = SignatureStatus.INVESTIGATING  # Type says OK, but no transition validation!
```

Valid transitions are implicit in service code:
- NEW → INVESTIGATING → DIAGNOSED/RESOLVED/MUTED (forward only)
- Can retriage: DIAGNOSED → NEW
- But type allows invalid: MUTED → INVESTIGATING

**Impact**: Type checker approves invalid state transitions, bugs slip through.

**Fix**: Add state helper methods (~70 lines, 1 hour)
```python
def set_status(self, new_status: SignatureStatus) -> None:
    """Update status with transition validation."""
    SignatureStatusTransition.validate_or_raise(self.status, new_status)
    self.status = new_status

# Use: signature.set_status(SignatureStatus.INVESTIGATING)
```

---

## Files Affected

### Require Changes:
- `/workspace/rounds/core/models.py` - Add validation and helpers
- `/workspace/rounds/core/ports.py` - Add exception types
- `/workspace/rounds/adapters/store/sqlite.py` - Use centralized parsing
- `/workspace/rounds/adapters/diagnosis/claude_code.py` - Use centralized parsing
- `/workspace/rounds/core/investigator.py` - Use state helpers
- `/workspace/rounds/core/triage.py` - Use state helpers

### Require Tests:
- `/workspace/rounds/tests/core/test_models.py` - Add validation tests
- `/workspace/rounds/tests/core/test_services.py` - May need updates

---

## How to Use This Analysis

### For Development Team:
1. Read [ANALYSIS_OVERVIEW.md](ANALYSIS_OVERVIEW.md) (5 min)
2. Review [TYPE_DESIGN_IMPROVEMENTS.md](TYPE_DESIGN_IMPROVEMENTS.md) code examples (15 min)
3. Create tickets for Phase 1 improvements
4. Implement over 2-3 development days

### For Code Review:
1. Read [TYPE_DESIGN_SUMMARY.md](TYPE_DESIGN_SUMMARY.md) for context
2. Reference [TYPE_DESIGN_ANALYSIS.md](TYPE_DESIGN_ANALYSIS.md) for detailed concerns
3. Use [TYPE_DESIGN_IMPROVEMENTS.md](TYPE_DESIGN_IMPROVEMENTS.md) to check implementations

### For Architecture Review:
1. Review [ANALYSIS_OVERVIEW.md](ANALYSIS_OVERVIEW.md)
2. Study [TYPE_DESIGN_VISUAL_GUIDE.md](TYPE_DESIGN_VISUAL_GUIDE.md) diagrams
3. Reference [TYPE_DESIGN_ANALYSIS.md](TYPE_DESIGN_ANALYSIS.md) for comprehensive details

---

## Success Criteria

After implementing all improvements, the project will have:

✓ **No deserialization vulnerabilities** - All enum parsing centralized with validation
✓ **No invalid model instances** - All invariants enforced at construction time
✓ **No invalid state transitions** - State machine explicitly validated
✓ **Specific exception types** - Error handling is precise and testable
✓ **No code duplication** - Parsing logic in one place
✓ **Better test coverage** - Validation edge cases covered
✓ **Higher type quality score** - 7.5 → 8.5/10

---

## Document Index

### Detailed Analysis
- [TYPE_DESIGN_ANALYSIS.md](TYPE_DESIGN_ANALYSIS.md) - Complete analysis with line numbers, specific code locations, all concerns identified, and detailed recommendations

### Implementation Guide
- [TYPE_DESIGN_IMPROVEMENTS.md](TYPE_DESIGN_IMPROVEMENTS.md) - Ready-to-use code snippets for all improvements, testing examples, migration path, and verification checklist

### Visual Reference
- [TYPE_DESIGN_VISUAL_GUIDE.md](TYPE_DESIGN_VISUAL_GUIDE.md) - Type quality scores, dependency graphs, invariant matrices, before/after comparisons, risk analysis, and implementation effort estimates

### Executive Summary
- [TYPE_DESIGN_SUMMARY.md](TYPE_DESIGN_SUMMARY.md) - Key findings, code snippets, issue locations, recommended fixes with priority order, effort estimates

### Overview
- [ANALYSIS_OVERVIEW.md](ANALYSIS_OVERVIEW.md) - High-level summary, roadmap, metrics, risk analysis, implementation strategy, file references

---

## Quick Facts

| Metric | Value |
|--------|-------|
| Overall Type Quality | 7.5/10 |
| Critical Issues Found | 3 |
| Medium-Risk Issues | 3 |
| Code Quality Score | 8/10 |
| Test Coverage | Good |
| Breaking Changes Required | 0 |
| Implementation Effort | ~5 hours |
| Code Changes | ~300 lines |
| Files to Modify | 8 |
| New Tests Required | ~100 lines |
| Days to Implement | 3-5 |

---

## Contacts & Questions

For questions about this analysis, refer to the specific document:
- **Why this design choice?** → [TYPE_DESIGN_ANALYSIS.md](TYPE_DESIGN_ANALYSIS.md) (Sections: Concerns, Recommended Improvements)
- **How do I fix this?** → [TYPE_DESIGN_IMPROVEMENTS.md](TYPE_DESIGN_IMPROVEMENTS.md)
- **Show me a diagram** → [TYPE_DESIGN_VISUAL_GUIDE.md](TYPE_DESIGN_VISUAL_GUIDE.md)
- **What's the priority?** → [TYPE_DESIGN_SUMMARY.md](TYPE_DESIGN_SUMMARY.md) (Recommended Fixes section)

---

## Last Updated

Analysis completed: 2025-02-12
Rounds Branch: feature/issue-1-sketch-out-the-project-archite
Analysis Tool: Claude Haiku 4.5 Type Design Expert

---

## Next Steps

1. **Share with team** - Send ANALYSIS_OVERVIEW.md and TYPE_DESIGN_IMPROVEMENTS.md
2. **Schedule review** - 30-minute architecture review
3. **Create tickets** - Phase 1 improvements (Diagnosis, ModelParsers, State helpers)
4. **Implement** - Over 2-3 development days
5. **Review & merge** - One improvement at a time
6. **Test** - Run full test suite after each change
7. **Verify quality** - Confirm no new issues introduced

All documents are in `/workspace/` directory and ready to use.
