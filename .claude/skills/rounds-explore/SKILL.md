---
name: rounds-explore
description: Exploratory querying of telemetry data — search logs by keyword/metadata, search spans by metadata, and build full transaction trees from trace IDs using the rounds adapter interfaces
user_invocable: true
args: [query?]
---

# Rounds: Explore Telemetry

Explore OTEL data held in the configured telemetry backend (SigNoz by default) using
the rounds adapter interfaces. No LLM budget is consumed — these are direct queries.

## Usage

```
/rounds-explore [optional natural-language description of what to look for]
```

If `$ARGUMENTS` is provided, use it to guide which queries to run first (e.g. "look for
database timeout errors in the last 2 hours" or "show me what the checkout service has
been doing"). If no arguments are given, start with a broad recent-activity scan.

---

## Available Python Commands

All three commands run via `python -m rounds.main cli-run <command> '<json>'` from
`/workspace/rounds`.

### 1. Search Logs — keyword and metadata filters

```bash
cd /workspace/rounds && python -m rounds.main cli-run search-logs '{
  "query": "timeout",
  "since_minutes": 120,
  "services": ["checkout-service"],
  "limit": 50
}'
```

**Parameters:**
- `query` (string, default `""`) — keyword to match in log bodies; empty string returns all logs
- `since_minutes` (int, default `60`) — lookback window from now
- `until_minutes` (int, optional) — upper bound in minutes ago (creates a closed time window)
- `services` (list of strings, optional) — filter by service name
- `limit` (int, default `50`) — max results

**Response shape:**
```json
{
  "status": "success",
  "operation": "search-logs",
  "query": "timeout",
  "count": 3,
  "logs": [
    {
      "timestamp": "2024-01-15T10:30:00+00:00",
      "severity": "ERROR",
      "body": "Connection timeout after 30s",
      "trace_id": "abc123...",
      "span_id": "def456...",
      "attributes": { "service.name": "checkout-service", "http.status_code": "504" }
    }
  ]
}
```

**Log exploration patterns:**

Search for a specific error keyword:
```bash
python -m rounds.main cli-run search-logs '{"query": "NullPointerException", "since_minutes": 60}'
```

Search for database-related logs across all services:
```bash
python -m rounds.main cli-run search-logs '{"query": "database", "since_minutes": 240, "limit": 100}'
```

Look at all recent logs from one service (no keyword filter):
```bash
python -m rounds.main cli-run search-logs '{"query": "", "since_minutes": 30, "services": ["api-gateway"], "limit": 100}'
```

Narrow a time window (between 2 and 1 hours ago):
```bash
python -m rounds.main cli-run search-logs '{"query": "error", "since_minutes": 120, "until_minutes": 60}'
```

---

### 2. Search Spans — metadata filters

```bash
cd /workspace/rounds && python -m rounds.main cli-run search-spans '{
  "since_minutes": 60,
  "services": ["payment-service"],
  "has_error": true,
  "limit": 50
}'
```

**Parameters:**
- `since_minutes` (int, default `60`) — lookback window from now
- `until_minutes` (int, optional) — upper bound in minutes ago from now
- `services` (list of strings, optional) — filter by service name
- `operation` (string, optional) — operation name substring filter (e.g. `"POST /checkout"`)
- `has_error` (bool, optional) — `true` for error spans only, `false` for healthy spans, omit for all
- `limit` (int, default `50`) — max results

**Response shape:**
```json
{
  "status": "success",
  "operation": "search-spans",
  "count": 2,
  "spans": [
    {
      "trace_id": "abc123def456...",
      "span_id": "789xyz...",
      "service": "payment-service",
      "operation": "POST /charge",
      "duration_ms": 1234.5,
      "has_error": true,
      "timestamp": "2024-01-15T10:30:00+00:00",
      "attributes": { "http.status_code": "500", "rpc.method": "Charge" }
    }
  ]
}
```

**Span exploration patterns:**

Find all error spans in the last hour:
```bash
python -m rounds.main cli-run search-spans '{"since_minutes": 60, "has_error": true, "limit": 100}'
```

Find slow operations matching a name pattern:
```bash
python -m rounds.main cli-run search-spans '{"since_minutes": 30, "operation": "checkout", "limit": 50}'
```

Survey recent activity in a service (all spans, no error filter):
```bash
python -m rounds.main cli-run search-spans '{"since_minutes": 15, "services": ["inventory-service"], "limit": 100}'
```

The `trace_id` values in results can be passed directly to `get-trace-tree` or
`investigate-trace` to drill deeper.

---

### 3. Get Trace Tree — full transaction hierarchy

```bash
cd /workspace/rounds && python -m rounds.main cli-run get-trace-tree '{
  "trace_id": "abc123def456789012345678901234ab"
}'
```

**Parameters:**
- `trace_id` (string, required) — 32-character hex OpenTelemetry trace ID

**Response shape:**
```json
{
  "status": "success",
  "operation": "get-trace-tree",
  "trace_id": "abc123...",
  "error_span_count": 1,
  "tree": {
    "span_id": "root-span-id",
    "service": "api-gateway",
    "operation": "POST /checkout",
    "duration_ms": 1500.0,
    "status": "error",
    "attributes": { "http.method": "POST", "http.url": "/checkout" },
    "events": [],
    "children": [
      {
        "span_id": "child-span-id",
        "service": "payment-service",
        "operation": "ProcessPayment",
        "duration_ms": 800.0,
        "status": "ok",
        "attributes": {},
        "events": [],
        "children": []
      }
    ]
  }
}
```

**Tree exploration patterns:**

Get the full transaction tree for a trace ID found via search-spans:
```bash
python -m rounds.main cli-run get-trace-tree '{"trace_id": "abc123def456789012345678901234ab"}'
```

The tree is returned as a nested structure. Walk it to:
- Identify which service originated the request (root span)
- See the full call chain (children recursively)
- Find all error spans (`"status": "error"`) and their positions in the tree
- Measure per-span durations to spot bottlenecks
- Inspect span attributes for HTTP status codes, DB queries, RPC methods

After reviewing the tree, follow up with `get-correlated-logs` or trigger LLM analysis:
```bash
# LLM-powered code-flow explanation (uses diagnosis budget):
python -m rounds.main cli-run investigate-trace '{"trace_id": "abc123..."}'
```

---

## Typical Exploratory Workflow

1. **Broad scan** — find recent error spans:
   ```bash
   python -m rounds.main cli-run search-spans '{"since_minutes": 60, "has_error": true, "limit": 50}'
   ```

2. **Narrow by service or operation** — pick an interesting service from step 1:
   ```bash
   python -m rounds.main cli-run search-spans '{"since_minutes": 60, "services": ["payment-service"], "has_error": true}'
   ```

3. **Look for correlated log messages** — search logs around the same time:
   ```bash
   python -m rounds.main cli-run search-logs '{"query": "payment", "since_minutes": 60, "services": ["payment-service"]}'
   ```

4. **Build the full transaction tree** — take a `trace_id` from step 2:
   ```bash
   python -m rounds.main cli-run get-trace-tree '{"trace_id": "<trace_id_from_step_2>"}'
   ```

5. **Correlate logs with that trace** — use the existing correlated-logs path:
   ```bash
   python -m rounds.main cli-run search-logs '{"query": "", "since_minutes": 10, "services": ["payment-service"]}'
   ```

6. **Trigger LLM analysis** — once you have a trace ID worth diagnosing:
   ```bash
   python -m rounds.main cli-run investigate-trace '{"trace_id": "<trace_id>"}'
   ```

---

## Implementation Notes

- All three commands call into `ManagementService` → `TelemetryPort` (SigNoz adapter by default)
- `search_logs` uses SigNoz `/api/v3/query_range` with `dataSource=logs` and a `body contains` filter
- `search_spans` uses `/api/v3/query_range` with `dataSource=traces` and flexible AND-combined filters
- `get_trace_tree` calls `GET /api/v1/traces/{id}` and builds the full `SpanNode` hierarchy
- No LLM calls are made; these are pure telemetry queries
- Source: `rounds/adapters/telemetry/signoz.py` — `search_logs`, `search_spans` methods
- Source: `rounds/adapters/cli/commands.py` — `search_logs`, `search_spans`, `get_trace_tree` handlers
