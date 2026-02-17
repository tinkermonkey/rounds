---
name: rounds-budget
description: Show current LLM usage budget and spending across all diagnosis runs
user_invocable: true
args:
generated: true
generation_timestamp: 2026-02-13T22:12:19.643452Z
generation_version: "2.0"
source_project: rounds
source_codebase_hash: a44338f108beaf54
---

# Budget Tracking and Spending Report

Quick-reference skill for viewing **LLM diagnosis spending** in the rounds error diagnosis system.

## Usage

```bash
/rounds-budget
```

## Purpose

Display current budget consumption and spending statistics for LLM-powered error diagnosis across the rounds system. This skill helps monitor:

1. **Daily spending limits** - Track usage against the configured `DAILY_BUDGET_LIMIT` (default: $100.00 USD)
2. **Per-diagnosis costs** - View individual diagnosis costs stored in `signatures.db`
3. **Total accumulated spending** - Calculate sum of all diagnosis costs from the SQLite store
4. **Budget configuration** - Show current settings from `rounds/config.py`:
   - `claude_code_budget_usd` (default: $2.00 per diagnosis)
   - `openai_budget_usd` (default: $2.00 per diagnosis)
   - `daily_budget_limit` (default: $100.00)

The daemon scheduler (`rounds/adapters/scheduler/daemon.py:209-231`) tracks daily spending in-memory and enforces limits during poll cycles.

## Implementation

### 1. Check Current Configuration

Read budget settings from environment or defaults:

```bash
# View current budget configuration
grep -E "budget|BUDGET" .env 2>/dev/null || echo "Using defaults from config.py"

# View configuration schema
grep -A 5 "Budget controls" rounds/config.py
```

### 2. Query Database for Historical Spending

The SQLite database stores all diagnosis costs in the `diagnosis_json` field of the `signatures` table. Extract and sum costs:

```bash
# Connect to SQLite and query diagnosis costs
sqlite3 ./data/signatures.db "
SELECT
    COUNT(*) as total_diagnoses,
    SUM(json_extract(diagnosis_json, '$.cost_usd')) as total_spent_usd,
    AVG(json_extract(diagnosis_json, '$.cost_usd')) as avg_cost_per_diagnosis,
    MAX(json_extract(diagnosis_json, '$.cost_usd')) as max_single_diagnosis,
    MIN(json_extract(diagnosis_json, '$.cost_usd')) as min_single_diagnosis
FROM signatures
WHERE diagnosis_json IS NOT NULL;
"
```

### 3. View Spending by Service

Break down diagnosis costs by service to identify high-cost areas:

```bash
sqlite3 ./data/signatures.db "
SELECT
    service,
    COUNT(*) as diagnoses_count,
    SUM(json_extract(diagnosis_json, '$.cost_usd')) as service_total_usd,
    AVG(json_extract(diagnosis_json, '$.cost_usd')) as avg_cost_usd
FROM signatures
WHERE diagnosis_json IS NOT NULL
GROUP BY service
ORDER BY service_total_usd DESC;
"
```

### 4. View Recent Diagnoses with Costs

Show the most recent diagnosed errors and their costs:

```bash
sqlite3 ./data/signatures.db "
SELECT
    substr(id, 1, 8) as sig_id,
    service,
    error_type,
    json_extract(diagnosis_json, '$.model') as model,
    json_extract(diagnosis_json, '$.cost_usd') as cost_usd,
    json_extract(diagnosis_json, '$.confidence') as confidence,
    datetime(json_extract(diagnosis_json, '$.diagnosed_at')) as diagnosed_at
FROM signatures
WHERE diagnosis_json IS NOT NULL
ORDER BY json_extract(diagnosis_json, '$.diagnosed_at') DESC
LIMIT 10;
"
```

### 5. Calculate Daily Spending (In-Memory)

The daemon scheduler tracks daily spending in-memory (`_daily_cost_usd` in `rounds/adapters/scheduler/daemon.py:38-40`). This resets at midnight UTC. To view current daily spending while daemon is running, you would need to:

1. Add a CLI command to expose the scheduler's `_daily_cost_usd` value
2. Or query recent diagnoses from today:

```bash
sqlite3 ./data/signatures.db "
SELECT
    COUNT(*) as today_diagnoses,
    SUM(json_extract(diagnosis_json, '$.cost_usd')) as today_spending_usd
FROM signatures
WHERE diagnosis_json IS NOT NULL
  AND date(json_extract(diagnosis_json, '$.diagnosed_at')) = date('now');
"
```

### 6. Budget Enforcement Check

The daemon (`rounds/adapters/scheduler/daemon.py:187-207`) checks budget limits before each investigation cycle:

- If `daily_budget_limit` is set (e.g., `DAILY_BUDGET_LIMIT=100.0`), spending is tracked
- When `_daily_cost_usd >= budget_limit`, investigation cycles are skipped
- Budget resets at midnight UTC

To verify budget enforcement:

```bash
# Check if daemon would skip investigations
# (requires reading daemon logs or adding status endpoint)
grep -E "budget.*exceeded|Daily budget limit" logs/rounds-daemon.log 2>/dev/null || echo "No budget warnings found"
```

## Examples

### Example 1: Quick Budget Summary

```bash
/rounds-budget
# Output shows:
# - Total diagnoses: 42
# - Total spent: $84.50 USD
# - Avg cost: $2.01 USD
# - Budget limit: $100.00 USD
# - Remaining today: $15.50 USD
```

### Example 2: Detailed Service Breakdown

After running the skill, you'll see:

```
Service Spending Report:
------------------------
payment-api:     $32.40 (16 diagnoses, avg $2.03)
user-service:    $28.14 (14 diagnoses, avg $2.01)
notification-svc: $24.00 (12 diagnoses, avg $2.00)

Total: $84.54 across 42 diagnoses
Daily Limit: $100.00 (15.46% remaining)
```

### Example 3: Check if Budget is Exceeded

```bash
/rounds-budget
# If daily spending exceeds limit:
# ⚠️  BUDGET EXCEEDED: $103.50 / $100.00 (103.5%)
# Investigation cycles are currently paused.
```

### Example 4: Historical Trend Analysis

```bash
# After running /rounds-budget, follow up with date-based query:
sqlite3 ./data/signatures.db "
SELECT
    date(json_extract(diagnosis_json, '$.diagnosed_at')) as diagnosis_date,
    COUNT(*) as count,
    SUM(json_extract(diagnosis_json, '$.cost_usd')) as daily_total_usd
FROM signatures
WHERE diagnosis_json IS NOT NULL
GROUP BY diagnosis_date
ORDER BY diagnosis_date DESC
LIMIT 7;
"
# Shows spending trends over the last 7 days
```

## Key Files Referenced

- `rounds/config.py:82-101` - Budget configuration (per-diagnosis and daily limits)
- `rounds/adapters/scheduler/daemon.py:209-231` - Daily budget tracking and enforcement
- `rounds/core/investigator.py:18-22` - BudgetTracker protocol interface
- `rounds/adapters/store/sqlite.py:97,425,440` - Diagnosis cost storage in `diagnosis_json`
- `rounds/core/models.py:107` - Diagnosis.cost_usd field
- `./data/signatures.db` - SQLite database storing all diagnosis costs

## Architecture Context

Budget tracking in rounds follows the **hexagonal architecture** pattern:

1. **Core Domain** (`rounds/core/investigator.py:18-22`) defines the `BudgetTracker` protocol
2. **Adapter Implementation** (`rounds/adapters/scheduler/daemon.py`) implements budget enforcement
3. **Persistence** (`rounds/adapters/store/sqlite.py`) stores historical costs in JSON
4. **Configuration** (`rounds/config.py`) provides budget limits from environment

The daemon scheduler serves dual purposes:
- Orchestrates poll/investigation cycles
- Tracks daily spending and enforces limits (implements `BudgetTracker` protocol)

---

*This skill was automatically generated from the rounds codebase architecture.*
