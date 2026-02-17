---
name: rounds-daemon
description: Start the rounds daemon with telemetry polling and diagnosis
user_invocable: true
args: [--config path]
generated: true
generation_timestamp: 2026-02-13T22:11:25.960920Z
generation_version: "2.0"
source_project: rounds
source_codebase_hash: a44338f108beaf54
---

# Rounds Daemon

Quick-reference skill for starting the **rounds** continuous error diagnosis daemon with telemetry polling and automated diagnosis.

## Usage

```bash
/rounds-daemon [--config path]
```

## Purpose

Starts the rounds daemon in **daemon mode**, which:
- Continuously polls configured telemetry backends (SigNoz, Jaeger, or Grafana Stack) for new error events
- Fingerprints errors into unique signatures using the Levenshtein distance algorithm
- Automatically triggers LLM-based root cause diagnosis for new error signatures
- Respects daily budget limits to prevent runaway costs
- Persists signatures and diagnoses to SQLite storage
- Sends notifications via configured backends (stdout, markdown, GitHub issues)

This is the primary run mode for production deployment of the rounds system, implementing the full error diagnosis pipeline described in the hexagonal architecture.

## Implementation

**Entry Point:** `rounds/main.py` (composition root)

**Command:**
```bash
cd /home/austinsand/workspace/orchestrator/rounds
RUN_MODE=daemon python -m rounds.main
```

**With Custom Configuration:**
```bash
# Export environment variables from custom config
export $(cat /path/to/custom.env | xargs)
RUN_MODE=daemon python -m rounds.main
```

**Key Components Activated:**

1. **Composition Root** (`rounds/main.py`): Wires all adapters and services
2. **Poll Service** (`rounds/core/poll_service.py`): Orchestrates polling loop
3. **Telemetry Adapter** (`rounds/adapters/telemetry/`): Queries configured backend
4. **Fingerprint Service** (`rounds/core/fingerprint.py`): Generates signatures
5. **Investigator** (`rounds/core/investigator.py`): Orchestrates diagnosis
6. **Store Adapter** (`rounds/adapters/store/sqlite.py`): Persists signatures
7. **Diagnosis Adapter** (`rounds/adapters/diagnosis/claude_code.py`): LLM analysis
8. **Notification Adapter** (`rounds/adapters/notification/`): Reports findings

**Environment Variables (from `rounds/config.py`):**

Core settings:
- `RUN_MODE=daemon` (required)
- `TELEMETRY_BACKEND=signoz|jaeger|grafana_stack`
- `STORE_BACKEND=sqlite` (default)
- `DIAGNOSIS_BACKEND=claude_code` (default)
- `NOTIFICATION_BACKEND=stdout|markdown|github_issue`

Polling configuration:
- `POLL_INTERVAL_SECONDS=60` (default)
- `ERROR_LOOKBACK_MINUTES=5` (default)
- `POLL_BATCH_SIZE=100` (default)

Budget controls:
- `CLAUDE_CODE_BUDGET_USD=5.00` (per-diagnosis limit)
- `DAILY_BUDGET_LIMIT=100.00` (daily spending cap)

Backend-specific:
- `SIGNOZ_API_URL`, `SIGNOZ_API_KEY`
- `JAEGER_API_URL`
- `GRAFANA_STACK_URL`, `GRAFANA_API_KEY`
- `STORE_SQLITE_PATH=./signatures.db`
- `NOTIFICATION_OUTPUT_DIR=./diagnoses`
- `GITHUB_TOKEN`, `GITHUB_REPO`

## Examples

### Example 1: Start daemon with SigNoz telemetry

```bash
# Set up environment
export TELEMETRY_BACKEND=signoz
export SIGNOZ_API_URL=https://signoz.example.com
export SIGNOZ_API_KEY=your-api-key-here
export POLL_INTERVAL_SECONDS=30
export NOTIFICATION_BACKEND=markdown
export NOTIFICATION_OUTPUT_DIR=/var/log/rounds/diagnoses

# Start daemon
cd /home/austinsand/workspace/orchestrator/rounds
RUN_MODE=daemon python -m rounds.main
```

**Expected behavior:**
- Polls SigNoz every 30 seconds for errors in the last 5 minutes
- Fingerprints new errors and stores signatures in `./signatures.db`
- Triggers Claude Code diagnosis for new signatures
- Writes markdown reports to `/var/log/rounds/diagnoses/`

### Example 2: Start daemon with GitHub issue notifications

```bash
# Set up environment
export TELEMETRY_BACKEND=jaeger
export JAEGER_API_URL=http://localhost:16686
export NOTIFICATION_BACKEND=github_issue
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
export GITHUB_REPO=myorg/myapp
export DAILY_BUDGET_LIMIT=50.00

# Start daemon
cd /home/austinsand/workspace/orchestrator/rounds
RUN_MODE=daemon python -m rounds.main
```

**Expected behavior:**
- Polls Jaeger for errors
- Creates GitHub issues for new diagnoses in `myorg/myapp`
- Stops diagnosing new errors once daily budget reaches $50

### Example 3: Development mode with stdout notifications

```bash
# Minimal config for local development
export TELEMETRY_BACKEND=signoz
export SIGNOZ_API_URL=http://localhost:3301
export NOTIFICATION_BACKEND=stdout
export POLL_INTERVAL_SECONDS=10
export ERROR_LOOKBACK_MINUTES=1

# Start daemon
cd /home/austinsand/workspace/orchestrator/rounds
RUN_MODE=daemon python -m rounds.main
```

**Expected behavior:**
- Fast polling (every 10 seconds)
- Short lookback window (1 minute)
- Prints diagnoses to stdout for immediate visibility
- Useful for testing telemetry integration

### Example 4: Using dotenv file for configuration

```bash
# Create .env file
cat > /tmp/rounds.env << EOF
RUN_MODE=daemon
TELEMETRY_BACKEND=grafana_stack
GRAFANA_STACK_URL=https://grafana.example.com
GRAFANA_API_KEY=eyJrIjoiXXXXXX
POLL_INTERVAL_SECONDS=120
STORE_SQLITE_PATH=/var/lib/rounds/signatures.db
NOTIFICATION_BACKEND=markdown
NOTIFICATION_OUTPUT_DIR=/var/log/rounds
DAILY_BUDGET_LIMIT=200.00
EOF

# Load and run
export $(cat /tmp/rounds.env | xargs)
cd /home/austinsand/workspace/orchestrator/rounds
python -m rounds.main
```

## Process Management

**Systemd Service Example:**

```ini
[Unit]
Description=Rounds Error Diagnosis Daemon
After=network.target

[Service]
Type=simple
User=rounds
WorkingDirectory=/home/austinsand/workspace/orchestrator/rounds
EnvironmentFile=/etc/rounds/daemon.env
ExecStart=/usr/bin/python3 -m rounds.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Docker Compose Example:**

```yaml
services:
  rounds-daemon:
    build: /home/austinsand/workspace/orchestrator/rounds
    environment:
      - RUN_MODE=daemon
      - TELEMETRY_BACKEND=signoz
      - SIGNOZ_API_URL=${SIGNOZ_API_URL}
      - SIGNOZ_API_KEY=${SIGNOZ_API_KEY}
    volumes:
      - ./data/signatures.db:/app/signatures.db
      - ./data/diagnoses:/app/diagnoses
    restart: unless-stopped
```

## Monitoring

**Check daemon status:**
```bash
# View recent poll cycles
tail -f /var/log/rounds/daemon.log | grep "poll_service"

# Monitor signature growth
sqlite3 signatures.db "SELECT COUNT(*) FROM signatures"

# Check daily budget usage
grep "budget" /var/log/rounds/daemon.log | tail -20
```

## Troubleshooting

**Daemon won't start:**
- Verify `RUN_MODE=daemon` is set
- Check telemetry backend connectivity: `curl $SIGNOZ_API_URL/api/v1/version`
- Ensure SQLite path is writable: `touch $STORE_SQLITE_PATH`

**No diagnoses generated:**
- Verify errors exist in telemetry backend
- Check budget limits haven't been exceeded
- Review fingerprint service logs for signature creation

**High costs:**
- Reduce `POLL_BATCH_SIZE` to diagnose fewer errors per cycle
- Increase `POLL_INTERVAL_SECONDS` to poll less frequently
- Lower `DAILY_BUDGET_LIMIT` for tighter cost control

## Related Skills

- `/rounds-test` - Run pytest test suite
- `/rounds-check` - Run mypy + ruff checks
- `/rounds-architecture` - Display architecture overview

---

*This skill was automatically generated from the rounds project architecture.*
