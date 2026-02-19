# Rounds Configuration Guide

Complete reference for all environment variables and configuration options for the Rounds continuous error diagnosis system.

## Configuration Overview

Rounds is configured entirely via environment variables. There are three ways to provide configuration:

1. **Environment file** (Recommended): Copy `.env.rounds.template` to `.env.rounds` and customize
2. **Command-line**: Export variables or use `docker-compose` `env_file` option
3. **Docker secrets** (Production): Use Docker or Kubernetes secrets for sensitive values

### Loading Configuration

**Docker Compose (with env file):**
```bash
env_file:
  - .env.rounds
```

**Docker run:**
```bash
docker run --env-file .env.rounds rounds:dist
```

**Direct environment:**
```bash
docker run -e TELEMETRY_BACKEND=signoz -e RUN_MODE=daemon rounds:dist
```

## Required Configuration

Minimum configuration needed to run Rounds:

```bash
# Telemetry source
TELEMETRY_BACKEND=signoz
SIGNOZ_API_URL=http://signoz:4418

# Run mode
RUN_MODE=daemon

# Diagnosis engine (if running diagnoses)
DIAGNOSIS_BACKEND=claude_code
ANTHROPIC_API_KEY=sk-ant-...
```

All other settings have sensible defaults.

## Telemetry Backend Configuration

### SigNoz

```bash
TELEMETRY_BACKEND=signoz
SIGNOZ_API_URL=http://signoz:4418
SIGNOZ_API_KEY=                    # Optional API key if SigNoz requires auth
```

**Example values:**
- Local: `http://localhost:4418`
- Docker network: `http://signoz:4418`
- Remote: `https://api.signoz.example.com`

**API Key:**
- Generated in SigNoz UI under Settings > API Keys
- Required if SigNoz has authentication enabled
- Leave empty for local/unauthenticated SigNoz

### Jaeger

```bash
TELEMETRY_BACKEND=jaeger
JAEGER_API_URL=http://jaeger-query:16686
```

**Example values:**
- Local: `http://localhost:16686`
- Docker network: `http://jaeger-query:16686`
- Remote: `https://jaeger.example.com`

**Note:** Jaeger API is typically unauthenticated. If using authentication, contact Jaeger documentation.

### Grafana Stack (Tempo + Prometheus + Loki)

```bash
TELEMETRY_BACKEND=grafana_stack
GRAFANA_TEMPO_URL=http://tempo:3200
GRAFANA_PROMETHEUS_URL=http://prometheus:9090
GRAFANA_LOKI_URL=http://loki:3100
GRAFANA_API_KEY=                   # Optional API key for Grafana Cloud
```

**Example values:**
- Local: `http://localhost:3200` (Tempo), `http://localhost:9090` (Prometheus), `http://localhost:3100` (Loki)
- Docker network: `http://tempo:3200`, etc.
- Grafana Cloud: `https://tempo-prod-XX-us-central1.grafana.net` (with API key)

**API Key:**
- Required for Grafana Cloud
- Generated in Grafana UI under Administration > Users and Access > Service Accounts
- Leave empty for self-hosted Grafana

## Signature Store Configuration

### SQLite (Default)

```bash
STORE_BACKEND=sqlite
STORE_SQLITE_PATH=/app/data/signatures.db
```

**Characteristics:**
- Zero-configuration, single file
- Suitable for small to medium deployments (<1000 signatures)
- Not suitable for multi-instance deployments
- Easy to backup (copy database file)

**Performance:**
- Fast for <1000 signatures
- Acceptable for <100 diagnoses/hour
- Limited by single-threaded query performance

**Backup:**
```bash
# Manual backup
cp /app/data/signatures.db /backup/signatures.db.backup

# Automated backup (Docker volume backup)
docker run --rm -v rounds-data:/data -v $(pwd):/backup \
  busybox sh -c 'cp /data/signatures.db /backup/backup-$(date +%s).db'
```

### PostgreSQL

```bash
STORE_BACKEND=postgresql
STORE_POSTGRESQL_URL=postgresql://user:password@host:port/dbname
```

**Connection string format:**
```
postgresql://[user[:password]@][netloc][:port][/dbname][?param1=value1&...]
```

**Examples:**
- Local: `postgresql://rounds:password@localhost:5432/rounds`
- Docker network: `postgresql://rounds:password@postgres:5432/rounds`
- Cloud (AWS RDS): `postgresql://user:pass@example.rds.amazonaws.com:5432/rounds`
- Connection pooling: `postgresql://user:pass@pgbouncer:6432/rounds` (add pgBouncer in front)
- SSL required: `postgresql://user:pass@host/db?sslmode=require`

**Advantages over SQLite:**
- Multi-instance deployments (shared database)
- Horizontal scaling
- Better performance for high volume (>100 diagnoses/hour)
- Built-in replication and backup tools
- Connection pooling via pgBouncer

**Initial setup:**
```bash
# Create database and user
createdb rounds
createuser rounds -P  # Prompts for password

# Or with Docker
docker exec postgres psql -U postgres -c "CREATE DATABASE rounds;"
docker exec postgres psql -U postgres -c "CREATE USER rounds WITH PASSWORD 'password';"
docker exec postgres psql -U postgres -c "ALTER ROLE rounds WITH CREATEDB;"
```

## Diagnosis Engine Configuration

### Claude Code (Recommended)

```bash
DIAGNOSIS_BACKEND=claude_code
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-opus           # Default: claude-opus, options: claude-3-5-sonnet, claude-opus
CLAUDE_CODE_BUDGET_USD=2.0         # Default: 2.0
DAILY_BUDGET_LIMIT=100.0           # Default: 100.0
```

**API Key:**
- Get from https://console.anthropic.com/account/keys
- Format: `sk-ant-...` (keep secret!)
- Ensure account has API credits

**Models:**
- `claude-opus` (default) - Most capable, higher cost (~$15/1M tokens)
- `claude-3-5-sonnet` - Good balance, lower cost (~$3/1M tokens)
- `claude-3-haiku` - Fastest, lowest cost (~$0.80/1M tokens)

**Per-Diagnosis Budget:**
- `CLAUDE_CODE_BUDGET_USD` limits cost per diagnosis
- Default 2.0 (up to $2 per diagnosis)
- Prevents runaway costs from complex errors
- Rounds will stop analysis if budget exceeded

**Daily Budget Limit:**
- `DAILY_BUDGET_LIMIT` is hard cap across all diagnoses
- Default 100.0 (up to $100 per day)
- Critical for cost control
- Diagnoses are skipped if daily limit reached

**Cost Estimation:**
```
Typical diagnosis: ~$0.50 - $2.00
Complex diagnosis: ~$2.00 - $5.00
Daily budget 100.0: ~50-100 diagnoses/day depending on complexity
```

### OpenAI

```bash
DIAGNOSIS_BACKEND=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4                 # Default: gpt-4, options: gpt-4-turbo, gpt-3.5-turbo
OPENAI_BUDGET_USD=2.0              # Default: 2.0
DAILY_BUDGET_LIMIT=100.0           # Default: 100.0
```

**API Key:**
- Get from https://platform.openai.com/account/api-keys
- Format: `sk-...` (keep secret!)
- Ensure account has API credits and is not in free trial

**Models:**
- `gpt-4` (default) - Most capable, higher cost (~$30/1M tokens)
- `gpt-4-turbo` - Better value, lower cost (~$10/1M tokens)
- `gpt-3.5-turbo` - Fastest, lowest cost (~$0.50/1M tokens)

**Budgeting:** Same as Claude Code (per-diagnosis and daily limits)

## Notification Backend Configuration

### Stdout (Default)

```bash
NOTIFICATION_BACKEND=stdout
```

**Output:**
- Logs all diagnoses to container stdout
- Visible with `docker logs rounds`
- Useful for development and testing

**Format:**
```
[INFO] Diagnosis created for signature sig-12345:
Root cause: Database connection timeout
Severity: HIGH
Confidence: MEDIUM
...
```

### Markdown Files

```bash
NOTIFICATION_BACKEND=markdown
NOTIFICATION_OUTPUT_DIR=/app/reports
```

**Output format:**
- Files organized by date: `YYYY-MM-DD/signature-id.md`
- Example: `2025-02-19/sig-12345.md`
- Readable from host via Docker volume mount

**File contents:**
- Full diagnosis report in Markdown
- Includes error details, root cause analysis, suggested fix
- Timestamp and metadata

**Access from host:**
```bash
# Mount reports in docker-compose.yml
volumes:
  - ./reports:/app/reports:rw

# View on host
ls -la ./reports/2025-02-19/
cat ./reports/2025-02-19/sig-12345.md
```

### GitHub Issues

```bash
NOTIFICATION_BACKEND=github_issue
GITHUB_TOKEN=ghp_...
GITHUB_REPO=myorg/myrepo
```

**GitHub Token:**
- Personal access token (Settings > Developer settings > Personal access tokens)
- Required permissions: `repo` (full control of private repos) or `public_repo` (public only)
- Format: `ghp_...` (keep secret!)
- Can be scope-limited for CI/CD safety

**Repository:**
- Format: `owner/repository`
- Must have write access
- Public or private repos supported

**Output:**
- Creates GitHub issue per diagnosed signature
- Issue title: `[Diagnosis] Error type: Service`
- Issue body: Full diagnosis report with links
- Labels: `bug`, `rounds-diagnosis`
- Can be assigned to team members

**Example:**
```bash
GITHUB_TOKEN=ghp_16c7e42f292c6912e7710c838347Ae178B4a
GITHUB_REPO=anthropics/rounds
```

## Polling Configuration

### Poll Interval

```bash
POLL_INTERVAL_SECONDS=60           # Default: 60
```

**Guidance:**
- `30-45 seconds`: Fast feedback, high resource usage
- `60 seconds` (default): Balanced for most deployments
- `120-300 seconds`: Low resource usage, slower diagnosis

**Impact:**
- CPU/Memory: More frequent = higher usage
- Error detection latency: Less frequent = higher latency
- API calls: More frequent = more API calls to telemetry backend

### Batch Size

```bash
POLL_BATCH_SIZE=100                # Default: 100
```

**Guidance:**
- Small deployments (< 10 errors/hour): 50-100
- Medium deployments (10-100 errors/hour): 100-250
- Large deployments (> 100 errors/hour): 250-500

**Impact:**
- Memory usage: Larger batch = more errors in memory
- Processing time per cycle: Larger batch = longer processing
- Database load: Larger batch = more database writes

### Error Lookback Window

```bash
ERROR_LOOKBACK_MINUTES=15          # Default: 15
```

**Guidance:**
- Frequent polling (30-45s interval): 10-15 minutes
- Normal polling (60-120s interval): 15-30 minutes
- Infrequent polling (300s+ interval): 30-60 minutes

**Impact:**
- Narrow window: May miss intermittent errors
- Wide window: May re-process old errors
- Interaction with `POLL_INTERVAL_SECONDS`

## Codebase Configuration

```bash
CODEBASE_PATH=/workspace/target    # Path to target codebase for diagnosis context
```

**Usage:**
- Rounds uses this to analyze source code during diagnosis
- Must be mounted as read-only volume
- Used by Claude Code CLI to understand codebase structure

**Docker example:**
```yaml
volumes:
  - /path/to/target/codebase:/workspace/target:ro
```

## Logging Configuration

### Log Level

```bash
LOG_LEVEL=INFO                     # Default: INFO
```

**Options:**
- `DEBUG`: Very verbose, all internal operations
- `INFO`: Normal operation, key milestones
- `WARNING`: Issues that might need attention
- `ERROR`: Errors and failures
- `CRITICAL`: Critical failures only

**Guidance:**
- Development: `DEBUG`
- Production: `INFO`
- Troubleshooting: `DEBUG`

### Log Format

```bash
LOG_FORMAT=text                    # Default: text, options: text, json
```

**Text:**
```
[2025-02-19 10:30:00] INFO     Poll cycle started for service=web-api
[2025-02-19 10:30:01] INFO     Found 5 new errors
[2025-02-19 10:30:05] INFO     Diagnosis complete for sig-12345
```

**JSON (for log aggregation):**
```json
{"timestamp": "2025-02-19T10:30:00Z", "level": "INFO", "message": "Poll cycle started", "service": "web-api"}
{"timestamp": "2025-02-19T10:30:01Z", "level": "INFO", "message": "Found 5 new errors"}
```

## Run Modes

### Daemon Mode (Production)

```bash
RUN_MODE=daemon
```

- Continuous polling and diagnosis
- Runs indefinitely until stopped
- Recommended for production
- See [DEPLOY.md](../DEPLOY.md) for details

### CLI Mode (Interactive)

```bash
RUN_MODE=cli
```

- Interactive command-line interface
- Manual investigation and management
- Non-production use only
- See [DEPLOY.md](../DEPLOY.md) for available commands

### Webhook Mode (Event-Driven)

```bash
RUN_MODE=webhook
WEBHOOK_HOST=0.0.0.0               # Default: 0.0.0.0
WEBHOOK_PORT=8080                  # Default: 8080
WEBHOOK_API_KEY=your-secret-key    # Optional
WEBHOOK_REQUIRE_AUTH=true           # Default: false
```

- HTTP server for on-demand diagnosis
- Listens for incoming webhook requests
- See [docs/WEBHOOK.md](./WEBHOOK.md) for API details

## Advanced Configuration

### Claude Code CLI Version

```bash
CLAUDE_CODE_VERSION=                # Default: latest
```

- Specific version to install (e.g., `1.2.3`)
- Leave empty for latest version (recommended)
- Useful for pinning to known-stable version

### Debug Mode

```bash
DEBUG=false                         # Default: false
```

- Enable extra debug output
- Increases logging verbosity
- For troubleshooting only

## Configuration Examples

### Development Environment

```bash
# .env.rounds for local development

RUN_MODE=cli

TELEMETRY_BACKEND=signoz
SIGNOZ_API_URL=http://localhost:3301

STORE_BACKEND=sqlite
STORE_SQLITE_PATH=./rounds.db

DIAGNOSIS_BACKEND=claude_code
ANTHROPIC_API_KEY=sk-ant-dev-key
CLAUDE_MODEL=claude-3-5-sonnet
CLAUDE_CODE_BUDGET_USD=1.0

NOTIFICATION_BACKEND=stdout

LOG_LEVEL=DEBUG
LOG_FORMAT=text

CODEBASE_PATH=./target-project
POLL_INTERVAL_SECONDS=30
ERROR_LOOKBACK_MINUTES=10
```

### Small Production (SQLite)

```bash
# .env.rounds for small production (< 10 signatures/hour)

RUN_MODE=daemon

TELEMETRY_BACKEND=signoz
SIGNOZ_API_URL=http://signoz:4418
SIGNOZ_API_KEY=${SIGNOZ_API_KEY}  # Injected at runtime

STORE_BACKEND=sqlite
STORE_SQLITE_PATH=/app/data/signatures.db

DIAGNOSIS_BACKEND=claude_code
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
CLAUDE_MODEL=claude-3-5-sonnet
CLAUDE_CODE_BUDGET_USD=2.0
DAILY_BUDGET_LIMIT=50.0

NOTIFICATION_BACKEND=markdown
NOTIFICATION_OUTPUT_DIR=/app/reports

LOG_LEVEL=INFO
LOG_FORMAT=json

POLL_INTERVAL_SECONDS=120
POLL_BATCH_SIZE=50
ERROR_LOOKBACK_MINUTES=30
```

### Large Production (PostgreSQL + Multi-Instance)

```bash
# .env.rounds for large production (> 100 signatures/hour)

RUN_MODE=daemon

TELEMETRY_BACKEND=signoz
SIGNOZ_API_URL=http://signoz:4418
SIGNOZ_API_KEY=${SIGNOZ_API_KEY}

STORE_BACKEND=postgresql
STORE_POSTGRESQL_URL=postgresql://${DB_USER}:${DB_PASSWORD}@db.internal:5432/rounds?sslmode=require

DIAGNOSIS_BACKEND=openai  # Cheaper for high volume
OPENAI_API_KEY=${OPENAI_API_KEY}
OPENAI_MODEL=gpt-4-turbo
OPENAI_BUDGET_USD=5.0
DAILY_BUDGET_LIMIT=500.0

NOTIFICATION_BACKEND=github_issue
GITHUB_TOKEN=${GITHUB_TOKEN}
GITHUB_REPO=myorg/platform-issues

LOG_LEVEL=INFO
LOG_FORMAT=json

POLL_INTERVAL_SECONDS=30
POLL_BATCH_SIZE=500
ERROR_LOOKBACK_MINUTES=5
```

### Event-Driven Webhook Mode

```bash
# .env.rounds for webhook mode

RUN_MODE=webhook
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8080
WEBHOOK_API_KEY=${WEBHOOK_SECRET}
WEBHOOK_REQUIRE_AUTH=true

TELEMETRY_BACKEND=signoz
SIGNOZ_API_URL=http://signoz:4418

STORE_BACKEND=postgresql
STORE_POSTGRESQL_URL=postgresql://rounds:${DB_PASSWORD}@db:5432/rounds

DIAGNOSIS_BACKEND=claude_code
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}

NOTIFICATION_BACKEND=github_issue
GITHUB_TOKEN=${GITHUB_TOKEN}
GITHUB_REPO=myorg/issues

LOG_LEVEL=INFO
```

## Environment Variable Validation

Rounds validates configuration on startup:

```bash
# Example error messages
ERROR: Missing required configuration: TELEMETRY_BACKEND
ERROR: Invalid TELEMETRY_BACKEND value: "invalid" (expected: signoz, jaeger, grafana_stack)
ERROR: Invalid STORE_BACKEND value: "mysql" (expected: sqlite, postgresql)
ERROR: ANTHROPIC_API_KEY is required when DIAGNOSIS_BACKEND=claude_code
ERROR: Invalid POLL_INTERVAL_SECONDS: "abc" (expected integer)
```

## Security Best Practices

### Secret Management

```bash
# WRONG: Hardcoded in .env.rounds (not committed)
ANTHROPIC_API_KEY=sk-ant-abc123...

# RIGHT: Injected at runtime (never committed)
docker run --env ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY ...

# RIGHT: Docker secrets (Swarm)
docker secret create anthropic-key <(echo -n $ANTHROPIC_API_KEY)

# RIGHT: Kubernetes secrets
kubectl create secret generic rounds-secrets \
  --from-literal=ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
```

### Principle of Least Privilege

```bash
# GitHub token: Use "public_repo" scope only if public repos
GITHUB_TOKEN=ghp_...  # Scope: repo or public_repo

# Anthropic API key: Rotate quarterly
ANTHROPIC_API_KEY=sk-ant-...  # Rotate if compromised

# Database password: Strong random (32+ chars)
STORE_POSTGRESQL_URL=postgresql://rounds:$(openssl rand -base64 32)@db:5432/rounds
```

### Network Security

```bash
# SigNoz API key: Use if SigNoz has authentication
SIGNOZ_API_KEY=${SIGNOZ_API_KEY}  # Injected from secrets

# Webhook API key: Required in production
WEBHOOK_REQUIRE_AUTH=true
WEBHOOK_API_KEY=$(openssl rand -base64 32)
```

## Troubleshooting Configuration

### Configuration Not Applied

**Symptom:** Changes to `.env.rounds` not reflected

**Solution:**
```bash
# Rebuild/restart container to reload environment
docker-compose down
docker-compose up -d
```

### Invalid Configuration Value

**Symptom:** Error on startup about invalid value

**Solution:**
1. Check spelling and exact values
2. Verify environment variable is exported: `echo $VARIABLE_NAME`
3. Review examples in `.env.rounds.template`

### API Authentication Failures

**Symptom:** "Unauthorized" or "Invalid API key" errors

**Solution:**
1. Verify API key is correct and not expired
2. Check key has necessary permissions
3. Review logs: `docker logs rounds`

## See Also

- [DEPLOY.md](../DEPLOY.md) - Deployment scenarios
- [DOCKER.md](./DOCKER.md) - Docker image details
- [.env.rounds.template](../.env.rounds.template) - Full template with defaults
- [CLAUDE.md](../CLAUDE.md) - Project architecture
