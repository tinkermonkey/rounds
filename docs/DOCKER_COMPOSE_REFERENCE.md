# Docker Compose Templates Reference

Quick reference guide for all available Docker Compose configurations in Rounds.

## Available Templates

### Base Configuration: `docker-compose.rounds.yml`

Production-ready base configuration with SQLite storage.

**Best for:** Small deployments, quick testing, single-instance production

**Features:**
- Non-root user security
- Resource limits (1.0 CPU, 512MB memory)
- JSON file logging with rotation
- Persistent SQLite database
- Health checks

**Quick start:**
```bash
docker-compose -f docker-compose.rounds.yml up -d

# View logs
docker-compose logs -f rounds

# Stop
docker-compose down
```

**Customization:**
```bash
# Mount target codebase
- /path/to/target/codebase:/workspace/target:ro

# Change memory limits
deploy:
  resources:
    limits:
      memory: 1G
```

---

### Development Override: `docker-compose.rounds.dev.yml`

Extends base config for local development with live code editing.

**Best for:** Local development, debugging, testing changes

**Features:**
- Interactive bash shell
- Live source code mounting (hot reload)
- Disabled health checks (prevent constant restarts)
- Port 8080 exposed for webhook testing
- No auto-restart

**Usage:**
```bash
# Start with development overrides
docker-compose -f docker-compose.rounds.yml \
               -f docker-compose.rounds.dev.yml up -d rounds

# Or run interactively
docker-compose -f docker-compose.rounds.yml \
               -f docker-compose.rounds.dev.yml run --rm rounds bash
```

**Mount host source for editing:**
```bash
volumes:
  - ./rounds:/workspace/rounds:rw  # Live editing enabled
```

---

### PostgreSQL Setup: `docker-compose.postgres.yml`

Production setup with PostgreSQL database for multi-instance deployments.

**Best for:** Production, high volume (>100 errors/hour), multiple instances

**Features:**
- PostgreSQL 15 Alpine (lightweight)
- Multi-instance Rounds support (rounds-1, rounds-2)
- pgAdmin optional web UI for database management
- Persistent database volume
- Health checks on database

**What it includes:**
- PostgreSQL service (5432)
- Rounds instance 1
- Rounds instance 2 (optional, for load balancing)
- pgAdmin (optional, port 5050)

**Usage:**
```bash
# Single instance
docker-compose -f docker-compose.postgres.yml up -d rounds-1

# Multiple instances (load balanced)
docker-compose -f docker-compose.postgres.yml up -d

# Check status
docker-compose ps
```

**Configuration:**
```bash
# Update .env.rounds with PostgreSQL backend
STORE_BACKEND=postgresql
STORE_POSTGRESQL_URL=postgresql://rounds:password@postgres:5432/rounds
```

**Security notes:**
- Change database password: Edit `POSTGRES_PASSWORD` in docker-compose file
- Remove pgAdmin for production (not needed, security risk)
- Use SSL for connections: Update connection string with `?sslmode=require`

**Scaling beyond 2 instances:**
```yaml
# Add more rounds services by copying rounds-1/rounds-2 pattern
rounds-3:
  image: rounds:dist
  environment:
    STORE_POSTGRESQL_URL: postgresql://rounds:password@postgres:5432/rounds
    INSTANCE_ID: rounds-3
  # ... rest of config
```

---

### Full Stack: `docker-compose.full-stack.yml`

Complete observability stack with SigNoz backend and Rounds diagnosis.

**Best for:** Demonstration, development, proof-of-concept deployments

**What it includes:**
- ClickHouse (time-series database)
- SigNoz Query Service (trace API)
- SigNoz Frontend (web UI)
- OTEL Collector (receives telemetry)
- Rounds (diagnosis service)

**Access points:**
- SigNoz UI: http://localhost:3301
- OTEL Collector: localhost:4317 (gRPC), localhost:4318 (HTTP)
- Rounds: Docker container

**Usage:**
```bash
# Start entire stack
docker-compose -f docker-compose.full-stack.yml up -d

# View logs
docker-compose logs -f

# Access SigNoz
open http://localhost:3301

# Send test traces to OTEL Collector
# ... (requires test application or trace sender)
```

**Customization:**
```bash
# Configure OTEL Collector:
# Edit config/otel-collector-config.yml before starting

# Adjust ClickHouse settings:
# Increase retention: POSTGRES_INITDB_ARGS with retention settings
```

**Resource requirements:**
- ~8GB memory total for all services
- 2+ CPU cores recommended
- ~10GB disk space for test data

---

### Webhook Mode: `docker-compose.webhook.yml`

HTTP server configuration for event-driven diagnosis.

**Best for:** Integration with alert systems (PagerDuty, Datadog, Slack)

**Features:**
- HTTP server on port 8080
- Optional API key authentication
- HMAC signature verification (planned)
- Webhook API endpoints

**Usage:**
```bash
# Start base config with webhook override
docker-compose -f docker-compose.rounds.yml \
               -f docker-compose.webhook.yml up -d

# Test webhook server
curl http://localhost:8080/health

# Send diagnosis request
curl -X POST http://localhost:8080/diagnose \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secret-key" \
  -d '{
    "trace_id": "test-123",
    "service": "api",
    "error_type": "TimeoutError",
    "timestamp": "'$(date -u +'%Y-%m-%dT%H:%M:%SZ')'"
  }'
```

**Configuration:**
```bash
# In .env.rounds
RUN_MODE=webhook
WEBHOOK_PORT=8080
WEBHOOK_API_KEY=your-secret-key
WEBHOOK_REQUIRE_AUTH=true
```

**API Endpoints:**
- `POST /diagnose` - Trigger diagnosis
- `GET /health` - Health check
- `GET /signatures` - List signatures

See [DEPLOY.md](../DEPLOY.md) webhook section for full API details.

---

## Composition Patterns

### Pattern 1: Simple Single-Instance (Development or Small Production)

```bash
# Use only base config
docker-compose -f docker-compose.rounds.yml up -d
```

**When to use:**
- Local development
- Small deployments (<10 errors/hour)
- CI/CD testing
- Quick proof-of-concept

**Configuration:**
```bash
STORE_BACKEND=sqlite
TELEMETRY_BACKEND=signoz
DIAGNOSIS_BACKEND=claude_code
```

---

### Pattern 2: Development with Live Code Editing

```bash
# Use base + development overrides
docker-compose -f docker-compose.rounds.yml \
               -f docker-compose.rounds.dev.yml up -d
```

**When to use:**
- Local development
- Testing adapter implementations
- Debugging diagnosis logic
- Interactive debugging

**Benefits:**
- Edit code on host, changes reflected immediately
- Interactive bash shell access
- Full logging for debugging

---

### Pattern 3: Production with PostgreSQL (Recommended)

```bash
# Use PostgreSQL configuration
docker-compose -f docker-compose.postgres.yml up -d
```

**When to use:**
- Production deployments
- High volume (>100 errors/hour)
- Multiple instances needed
- Future vector search requirements

**Configuration:**
```bash
# .env.rounds
STORE_BACKEND=postgresql
STORE_POSTGRESQL_URL=postgresql://rounds:password@postgres:5432/rounds
DIAGNOSIS_BACKEND=claude_code
```

**Scaling:**
- Multiple Rounds instances share PostgreSQL
- Load balancer can distribute requests (for webhook mode)
- Database handles concurrent access

---

### Pattern 4: Full Stack Demonstration

```bash
# Use full stack with all components
docker-compose -f docker-compose.full-stack.yml up -d
```

**When to use:**
- Internal demonstrations
- Development environment
- Learning how components integrate
- Testing end-to-end workflows

**Access:**
- SigNoz UI: localhost:3301
- Rounds via CLI or logs

---

### Pattern 5: Event-Driven (Webhook Integration)

```bash
# Use base + webhook overrides
docker-compose -f docker-compose.rounds.yml \
               -f docker-compose.webhook.yml up -d
```

**When to use:**
- Integration with PagerDuty incidents
- Datadog monitor webhooks
- Slack bot triggers
- Custom alert systems

**Configuration:**
```bash
# .env.rounds
RUN_MODE=webhook
WEBHOOK_PORT=8080
WEBHOOK_API_KEY=your-secret
WEBHOOK_REQUIRE_AUTH=true
```

---

## Command Reference

### Start Services

```bash
# Single instance with SQLite
docker-compose -f docker-compose.rounds.yml up -d

# Development with live editing
docker-compose -f docker-compose.rounds.yml \
               -f docker-compose.rounds.dev.yml up -d rounds

# Multi-instance with PostgreSQL
docker-compose -f docker-compose.postgres.yml up -d

# Full stack with SigNoz
docker-compose -f docker-compose.full-stack.yml up -d

# Webhook mode
docker-compose -f docker-compose.rounds.yml \
               -f docker-compose.webhook.yml up -d
```

### View Logs

```bash
# Real-time logs
docker-compose logs -f rounds

# Last 50 lines
docker-compose logs --tail=50 rounds

# Logs from specific time
docker-compose logs --since 30m rounds

# Specific service (multi-service setup)
docker-compose logs -f postgres
```

### Execute Commands

```bash
# Interactive bash
docker-compose run --rm rounds bash

# Single command
docker-compose exec rounds printenv TELEMETRY_BACKEND

# Database query
docker-compose exec postgres psql -U rounds -c "SELECT COUNT(*) FROM signatures;"
```

### Manage Services

```bash
# Stop services
docker-compose stop

# Stop and remove containers
docker-compose down

# Remove volumes (careful - data loss!)
docker-compose down -v

# Restart single service
docker-compose restart rounds

# View status
docker-compose ps

# View resource usage
docker stats
```

---

## Environment File per Composition

Each composition can use the same `.env.rounds` file. Important variables:

```bash
# Required for all
TELEMETRY_BACKEND=signoz
SIGNOZ_API_URL=http://signoz:4418

# SQLite-only (docker-compose.rounds.yml)
STORE_BACKEND=sqlite
STORE_SQLITE_PATH=/app/data/signatures.db

# PostgreSQL-only (docker-compose.postgres.yml)
STORE_BACKEND=postgresql
STORE_POSTGRESQL_URL=postgresql://rounds:password@postgres:5432/rounds

# Required for diagnosis
DIAGNOSIS_BACKEND=claude_code
ANTHROPIC_API_KEY=sk-ant-...

# Webhook-only (docker-compose.webhook.yml)
RUN_MODE=webhook
WEBHOOK_PORT=8080
WEBHOOK_API_KEY=secret
```

---

## Production Checklist

Before deploying to production:

- [ ] Use `docker-compose.postgres.yml` (not SQLite)
- [ ] Change all passwords (database, webhook API key)
- [ ] Use reverse proxy with HTTPS/TLS
- [ ] Enable `WEBHOOK_REQUIRE_AUTH=true`
- [ ] Set appropriate resource limits
- [ ] Configure backup strategy
- [ ] Set up monitoring and alerting
- [ ] Review security settings in [DEPLOY.md](../DEPLOY.md)
- [ ] Test disaster recovery (backup/restore)
- [ ] Configure log aggregation
- [ ] Set up health checks monitoring

---

## Troubleshooting Compositions

### Services can't reach each other

```bash
# Check network
docker network ls
docker network inspect <network-name>

# Verify both services are on same network
docker inspect rounds | grep NetworkMode
docker inspect postgres | grep NetworkMode
```

### Port conflicts

```bash
# Check port usage
lsof -i :8080
lsof -i :5432

# Change port in docker-compose override
# or kill existing process
```

### Data not persisting

```bash
# Check volume mounts
docker inspect rounds | grep -A 10 Mounts

# Verify volume exists
docker volume ls | grep rounds

# Check disk space
df -h
```

---

## See Also

- [DEPLOY.md](../DEPLOY.md) - Full deployment guide
- [DOCKER.md](./DOCKER.md) - Docker image details
- [CONFIGURATION.md](./CONFIGURATION.md) - Environment variables
- Docker Compose docs: https://docs.docker.com/compose/
