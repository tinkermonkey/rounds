# Rounds Deployment Guide

## Overview

This guide covers deploying the Rounds continuous error diagnosis system in various environments. Rounds is designed to run as a containerized service alongside your OpenTelemetry infrastructure.

## Quick Start

### Prerequisites

- Docker and Docker Compose installed
- A running OTLP backend (SigNoz, Jaeger, or Grafana Stack)
- API credentials for your telemetry backend
- (Optional) Anthropic API key for Claude Code diagnosis or OpenAI API key

### Basic Deployment in 5 Minutes

Run from your target project's root directory (the application being monitored):

```bash
# 1. Install Rounds configuration files to your project
curl -fsSL https://raw.githubusercontent.com/your-org/rounds/main/scripts/install-compose.sh | bash

# 2. Edit .env.rounds with your settings (see docs/QUICK_START_TARGET_PROJECT.md)

# 3. Start the service
docker-compose -f docker-compose.rounds.yml up -d
```

4. Verify it's running:
```bash
docker-compose logs -f rounds
```

## Deployment Modes

Rounds operates in three distinct modes, selectable via `RUN_MODE` environment variable:

### Daemon Mode (Production)

Continuously monitors your telemetry backend for errors, fingerprints them, and diagnoses root causes. Best for production deployments.

```bash
RUN_MODE=daemon
```

**Behavior:**
- Polls telemetry backend every `POLL_INTERVAL_SECONDS` (default: 60 seconds)
- Creates error signatures from recent errors
- Diagnoses new signatures using configured LLM backend
- Persists findings to signature store
- Reports via configured notification backend
- Runs indefinitely until stopped

**Resource Requirements:**
- CPU: 0.25 reserved, 1.0 limit (adjustable in docker-compose.yml)
- Memory: 128 MB reserved, 512 MB limit
- Storage: SQLite database grows ~1-2 MB per 100 diagnosed errors

### CLI Mode (Interactive Management)

Interactive command-line interface for manual investigation, signature management, and system inspection.

```bash
RUN_MODE=cli
```

**Usage:**
```bash
docker-compose -f docker-compose.rounds.yml -f docker-compose.rounds.dev.yml run --rm rounds
# Inside container:
> show-signatures
> diagnose-signature <id>
> reset-signature <id>
```

**Common Commands:**
- `show-signatures` - List all known error signatures
- `show-signature <id>` - Display details of a specific signature
- `diagnose-signature <id>` - Manually trigger diagnosis for a signature
- `reset-signature <id>` - Reset a signature's diagnosis
- `export-signatures` - Export signatures to JSON
- `help` - List all available commands

### Webhook Mode (Event-Driven)

HTTP server that listens for external triggers (e.g., from alert systems). Useful for investigating specific errors on-demand.

```bash
RUN_MODE=webhook
WEBHOOK_PORT=8080
WEBHOOK_API_KEY=your-secret-key  # Recommended for production
```

**API Endpoint:**
```bash
POST /diagnose
Content-Type: application/json
X-API-Key: your-secret-key  # If WEBHOOK_REQUIRE_AUTH=true

{
  "trace_id": "abc123...",
  "service": "payment-service",
  "error_type": "DatabaseTimeoutError",
  "timestamp": "2025-02-19T10:30:00Z"
}
```

## Configuration

All configuration is environment-based. Copy `.env.rounds.template` to `.env.rounds` and customize:

### Telemetry Backend

Choose your observability platform:

**SigNoz (Default)**
```bash
TELEMETRY_BACKEND=signoz
SIGNOZ_API_URL=http://signoz:4418
SIGNOZ_API_KEY=your-api-key  # Optional
```

**Jaeger**
```bash
TELEMETRY_BACKEND=jaeger
JAEGER_API_URL=http://jaeger-query:16686
```

**Grafana Stack (Tempo + Loki + Prometheus)**
```bash
TELEMETRY_BACKEND=grafana_stack
GRAFANA_TEMPO_URL=http://tempo:3200
GRAFANA_LOKI_URL=http://loki:3100
GRAFANA_PROMETHEUS_URL=http://prometheus:9090
GRAFANA_API_KEY=your-api-key  # Optional
```

### Signature Store

Choose where to persist diagnosed signatures:

**SQLite (Default, Single-Node)**
```bash
STORE_BACKEND=sqlite
STORE_SQLITE_PATH=/app/data/signatures.db
```
- Zero configuration, single file
- Suitable for small to medium deployments (<1000 signatures)
- Automatic backup via volume mount

**PostgreSQL (Production Multi-Node)**
```bash
STORE_BACKEND=postgresql
STORE_POSTGRESQL_URL=postgresql://user:password@postgres:5432/rounds
```
- Required for multi-instance deployments
- Supports distributed tracing with vector similarity search (planned)
- See "Production Setup with PostgreSQL" section below

### Diagnosis Engine

Choose your LLM provider:

**Claude Code (Recommended)**
```bash
DIAGNOSIS_BACKEND=claude_code
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-opus
CLAUDE_CODE_BUDGET_USD=2.0
DAILY_BUDGET_LIMIT=100.0
```

**OpenAI**
```bash
DIAGNOSIS_BACKEND=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4
OPENAI_BUDGET_USD=2.0
DAILY_BUDGET_LIMIT=100.0
```

### Notification Backend

Configure how diagnoses are reported:

**Stdout (Default)**
```bash
NOTIFICATION_BACKEND=stdout
```
- Logs all diagnoses to container stdout
- Useful for development and simple deployments

**Markdown Files**
```bash
NOTIFICATION_BACKEND=markdown
NOTIFICATION_OUTPUT_DIR=/app/reports
```
- Writes diagnosis reports to individual markdown files
- Directory structure: `/reports/YYYY-MM-DD/HH-MM-SS_service_ErrorType_sigID.md`
- Mount `./.rounds/reports` volume to access on host

**GitHub Issues**
```bash
NOTIFICATION_BACKEND=github_issue
GITHUB_TOKEN=ghp_...
GITHUB_REPO=myorg/myrepo
```
- Creates GitHub issues for new diagnoses
- Links back to telemetry data in issue description
- Enables team collaboration on diagnosis results

### Polling Configuration

Fine-tune the polling loop behavior:

```bash
# How often to check for new errors (seconds)
POLL_INTERVAL_SECONDS=60

# How many events to process per poll cycle
POLL_BATCH_SIZE=100

# How far back to look for errors (minutes)
ERROR_LOOKBACK_MINUTES=15
```

**Tuning Guidance:**
- **Fast feedback**: Reduce `POLL_INTERVAL_SECONDS` to 30-45 seconds
- **Low resource usage**: Increase to 120-300 seconds
- **High error volume**: Increase `POLL_BATCH_SIZE` to 250-500
- **Broad lookback**: Increase `ERROR_LOOKBACK_MINUTES` to 30-60 for less frequent polling

## Docker Compose Deployments

### Development Deployment

For local development with live code editing:

```bash
docker-compose -f docker-compose.rounds.yml \
               -f docker-compose.rounds.dev.yml up -d rounds
```

This enables:
- Interactive bash shell access
- Live source code editing (mounted from host)
- Debug-level logging
- Port 8080 exposed for webhook testing

### Production Deployment

Standard production setup with persistent storage:

```bash
docker-compose -f docker-compose.rounds.yml up -d
```

Features:
- Non-root user (nurse) for security
- Resource limits and reservations
- Auto-restart on failure
- JSON-file logging with rotation
- Health checks (if configured in Dockerfile)

### Multi-Component Deployment with SigNoz

Run Rounds alongside SigNoz on the same Docker network:

```yaml
# docker-compose.full-stack.yml
version: '3.8'

services:
  # SigNoz components (simplified)
  signoz-otel-collector:
    image: signoz/signoz-otel-collector:latest
    ports:
      - "4317:4317"  # OTLP gRPC
      - "4318:4318"  # OTLP HTTP
    networks:
      - rounds-network

  signoz-query-service:
    image: signoz/query-service:latest
    environment:
      CLICKHOUSE_URL: http://clickhouse:9000
    ports:
      - "8080:8080"
    depends_on:
      - clickhouse
    networks:
      - rounds-network

  # Rounds diagnosis service
  rounds:
    image: rounds:dist
    environment:
      TELEMETRY_BACKEND: signoz
      SIGNOZ_API_URL: http://signoz-query-service:8080
      RUN_MODE: daemon
    env_file:
      - .env.rounds
    depends_on:
      - signoz-query-service
    networks:
      - rounds-network
    volumes:
      - rounds-data:/app/data
      - ./.rounds/reports:/app/reports

networks:
  rounds-network:
    driver: bridge

volumes:
  rounds-data:
```

## Production Setup with PostgreSQL

For multi-instance deployments or advanced vector search:

1. Set up PostgreSQL database:
```bash
docker run -d \
  --name rounds-db \
  -e POSTGRES_DB=rounds \
  -e POSTGRES_USER=rounds \
  -e POSTGRES_PASSWORD=secure-password \
  -v rounds-db-data:/var/lib/postgresql/data \
  postgres:15-alpine
```

2. Update environment configuration:
```bash
STORE_BACKEND=postgresql
STORE_POSTGRESQL_URL=postgresql://rounds:secure-password@rounds-db:5432/rounds
```

3. Deploy multiple Rounds instances sharing the database:
```yaml
services:
  rounds-1:
    image: rounds:dist
    environment:
      STORE_BACKEND: postgresql
      STORE_POSTGRESQL_URL: postgresql://rounds:password@db:5432/rounds
    depends_on:
      - postgres

  rounds-2:
    image: rounds:dist
    environment:
      STORE_BACKEND: postgresql
      STORE_POSTGRESQL_URL: postgresql://rounds:password@db:5432/rounds
    depends_on:
      - postgres

  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: rounds
      POSTGRES_USER: rounds
      POSTGRES_PASSWORD: secure-password
    volumes:
      - db-data:/var/lib/postgresql/data

volumes:
  db-data:
```

## Kubernetes Deployment (Optional)

For Kubernetes-based infrastructure, see [docs/KUBERNETES.md](docs/KUBERNETES.md).

## Security Considerations

### Production Best Practices

1. **API Keys**: Always use environment variables or secrets management systems
   - Never commit `.env.rounds` to version control
   - Use Docker secrets or Kubernetes secrets in production

2. **Network Security**
   - Run Rounds service on a private network (not exposed to public internet)
   - Use network policies to restrict telemetry backend access
   - Enable webhook API key authentication: `WEBHOOK_REQUIRE_AUTH=true`

3. **Data Protection**
   - Encrypt database volumes at rest
   - Enable SSL/TLS for PostgreSQL connections
   - Regular backups of SQLite or PostgreSQL databases

4. **Access Control**
   - CLI mode should only be accessible to authorized personnel
   - GitHub token should be read-limited and rotated regularly
   - Container should run as non-root user (default: `nurse`)

### Minimal Secure Configuration

```bash
# .env.rounds
TELEMETRY_BACKEND=signoz
SIGNOZ_API_URL=http://signoz:4418
SIGNOZ_API_KEY=${SIGNOZ_API_KEY}  # Injected at runtime

STORE_BACKEND=postgresql
STORE_POSTGRESQL_URL=${POSTGRES_CONNECTION_STRING}  # Injected at runtime

DIAGNOSIS_BACKEND=claude_code
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}  # Injected at runtime

RUN_MODE=daemon
NOTIFICATION_BACKEND=github_issue
GITHUB_TOKEN=${GITHUB_TOKEN}  # Injected at runtime
GITHUB_REPO=myorg/myrepo
```

## Troubleshooting

### Container Won't Start

**Check logs:**
```bash
docker-compose logs rounds
```

**Common issues:**
- Missing environment variables: Verify `.env.rounds` exists and is properly formatted
- Network connectivity: Ensure telemetry backend is reachable (check `SIGNOZ_API_URL`, etc.)
- Authentication: Verify API keys are correct and have necessary permissions
- File permissions: Ensure `/app/data` and `/app/reports` directories are writable

### Diagnosis Timeouts

**Symptom:** Logs show "Claude Code diagnosis timeout" or similar

**Solution:**
- Increase timeout in code if needed (default: 5 minutes)
- Check Claude Code CLI is up to date: Auto-updates on container start
- Verify Anthropic API key has sufficient quota

### High Memory Usage

**Check current memory:**
```bash
docker stats rounds
```

**Solutions:**
- Reduce `POLL_BATCH_SIZE` (fewer events per cycle)
- Increase `POLL_INTERVAL_SECONDS` (less frequent polling)
- Scale up memory limit in docker-compose.yml

### Database Errors

**SQLite Locked:**
```
sqlite3.OperationalError: database is locked
```
- Only one instance should use SQLite
- Use PostgreSQL for multi-instance deployments

**PostgreSQL Connection Failed:**
- Verify connection string: `STORE_POSTGRESQL_URL`
- Check database exists and user has permissions
- Verify network connectivity

## Monitoring and Logging

### Viewing Logs

```bash
# Real-time logs
docker-compose logs -f rounds

# Last 100 lines
docker-compose logs --tail=100 rounds

# Logs from specific time
docker-compose logs --since 30m rounds
```

### Health Checks

The Rounds container includes a health check (if enabled in Dockerfile):

```bash
docker-compose ps  # Shows health status

# Check manually
docker exec rounds /docker/healthcheck.sh
```

### Metrics to Monitor

- **Error signature creation rate**: New signatures per hour
- **Diagnosis latency**: Time from error detection to diagnosis
- **LLM API costs**: Daily spending against `DAILY_BUDGET_LIMIT`
- **Database size**: SQLite or PostgreSQL storage usage
- **Container resource usage**: CPU and memory trends

### Integration with Monitoring Systems

Export logs to your monitoring system:
- ELK Stack: Configure Filebeat to tail JSON logs
- Datadog: Use container monitoring integration
- Grafana Loki: Push logs via Promtail

Example Promtail config:
```yaml
scrape_configs:
  - job_name: rounds
    docker_sd_configs:
      - host: unix:///var/run/docker.sock
    relabel_configs:
      - source_labels: [__meta_docker_container_name]
        regex: '(rounds.*)'
        action: keep
```

## Scaling and Performance

### Horizontal Scaling with PostgreSQL

When diagnosed signatures exceed SQLite capacity (1000+ active), scale to PostgreSQL:

```bash
# Migrate from SQLite to PostgreSQL
docker-compose exec rounds python -m rounds.migrate_store sqlite postgresql

# Deploy multiple instances
docker-compose up -d --scale rounds=3
```

### Performance Tuning

**For high error volume (>1000 errors/hour):**
```bash
POLL_BATCH_SIZE=500
POLL_INTERVAL_SECONDS=30
ERROR_LOOKBACK_MINUTES=5
STORE_BACKEND=postgresql
```

**For cost-sensitive deployments:**
```bash
POLL_INTERVAL_SECONDS=300
POLL_BATCH_SIZE=50
DIAGNOSIS_BACKEND=openai  # Potentially cheaper than Claude
DAILY_BUDGET_LIMIT=10.0
```

## Backup and Recovery

### SQLite Backups

Backup is automatic via Docker volume:
```bash
# Manual backup
docker-compose exec rounds cp /app/data/signatures.db /app/data/signatures.db.backup

# Restore from backup
docker-compose exec rounds cp /app/data/signatures.db.backup /app/data/signatures.db
docker-compose restart rounds
```

### PostgreSQL Backups

```bash
# Create dump
docker-compose exec postgres pg_dump -U rounds rounds > backup.sql

# Restore from dump
docker-compose exec -T postgres psql -U rounds rounds < backup.sql
```

## Upgrading Rounds

### Zero-Downtime Upgrade (Daemon Mode)

1. Pull latest image:
```bash
docker pull rounds:dist
```

2. Restart service (data is persisted):
```bash
docker-compose up -d  # Pulls latest image and restarts
```

The service resumes from where it left off. No signature data is lost.

### Database Migrations

If upgrading includes database schema changes:

1. Check for migrations:
```bash
docker-compose exec rounds python -m rounds.migrate_schema
```

2. Apply automatically on startup:
```bash
# Latest versions run migrations automatically
docker-compose up -d
```

## Support and Troubleshooting

For issues, bug reports, or feature requests:
- GitHub Issues: https://github.com/anthropics/rounds/issues
- Documentation: https://rounds.dev/docs

## See Also

- [DOCKER.md](docs/DOCKER.md) - Detailed Docker image specifications and customization
- [CONFIGURATION.md](docs/CONFIGURATION.md) - Complete environment variable reference
- [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) - Detailed troubleshooting guide
- [CLAUDE.md](CLAUDE.md) - Project architecture and coding standards
