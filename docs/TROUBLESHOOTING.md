# Rounds Troubleshooting Guide

Common issues and solutions for running Rounds in development and production environments.

## Quick Diagnostics

Run this command to check Rounds health:

```bash
# Check if container is running
docker ps | grep rounds

# View recent logs
docker logs --tail=50 rounds

# Check configuration
docker exec rounds env | grep -E "TELEMETRY|STORE|DIAGNOSIS|RUN_MODE"

# Test connectivity to telemetry backend
docker exec rounds curl -I http://signoz:4418/api/v1/get_trace?traceId=test

# Test database connectivity
docker exec rounds sqlite3 /app/data/signatures.db "SELECT COUNT(*) FROM signatures;"
```

## Container Issues

### Container Won't Start

**Symptom:** `docker-compose up` fails or container exits immediately

**Step 1: Check logs**
```bash
docker logs rounds
```

**Common errors:**

#### Missing Environment Variables
```
Error: TELEMETRY_BACKEND is required
```

**Solution:**
```bash
# Verify .env.rounds exists
ls -la .env.rounds

# Ensure it has required variables
grep "TELEMETRY_BACKEND" .env.rounds

# If missing, copy template and customize
cp .env.rounds.template .env.rounds
# Then edit .env.rounds with your values
```

#### File Not Found Errors
```
Error: /docker/entrypoint.sh: No such file or directory
```

**Solution:**
- This indicates the Docker image wasn't built correctly
- Rebuild the image: `docker build -f Dockerfile.dist -t rounds:dist .`
- Or pull from registry: `docker pull rounds:dist`

#### Port Already in Use
```
Error: Address already in use
```

**Solution:**
```bash
# For webhook mode, change port in docker-compose.yml
ports:
  - "8081:8080"  # Changed from 8080:8080

# Or kill existing process
lsof -i :8080
kill -9 <PID>
```

#### Out of Disk Space
```
Error: No space left on device
```

**Solution:**
```bash
# Check disk usage
df -h

# Clean up Docker
docker system prune --all --volumes

# Reduce database size (if using SQLite)
docker exec rounds sqlite3 /app/data/signatures.db "VACUUM;"

# Or switch to PostgreSQL for production
```

### Container Exits with Code 1

**Symptom:** `docker-compose logs` shows exit code 1

**Debugging:**
```bash
# Run container interactively to see full error
docker run -it --rm \
  --env-file .env.rounds \
  rounds:dist bash

# Then run the same command as container would
python -m rounds.main
```

### Container Stuck in Restart Loop

**Symptom:** Container keeps restarting

**Check restart policy:**
```bash
docker inspect rounds --format='{{.RestartPolicy}}'
# Output: map[MaximumRetryCount:0 Name:unless-stopped]

# View restart count
docker inspect rounds --format='{{.RestartCount}}'
```

**Solution:**
```bash
# Stop container
docker-compose down

# View logs from last run
docker logs --tail=100 rounds

# Fix the underlying issue, then restart
docker-compose up -d

# Monitor for crashes
docker logs -f rounds
```

## Network and Connectivity Issues

### Can't Reach Telemetry Backend

**Symptom:**
```
Error: Cannot reach http://signoz:4418
Connection refused
```

**Diagnostic commands:**
```bash
# Check if service is running
docker ps | grep signoz

# Test connectivity from rounds container
docker exec rounds curl -v http://signoz:4418

# Check DNS resolution
docker exec rounds getent hosts signoz

# Check network
docker network ls
docker network inspect rounds-network  # Verify both services are on same network
```

**Common causes and solutions:**

**Cause 1: Service not running**
```bash
# Start telemetry backend
docker-compose up -d signoz

# Wait for it to be ready
docker logs -f signoz
```

**Cause 2: Wrong service name**
- Verify service name in docker-compose.yml
- Update `SIGNOZ_API_URL` to match service name
- Example: `SIGNOZ_API_URL=http://signoz-query:8080` (not just `signoz`)

**Cause 3: Different Docker networks**
```bash
# Verify both services are on same network
docker inspect rounds | grep NetworkMode
docker inspect signoz | grep NetworkMode

# Both should be on same network (e.g., "rounds-network")
```

**Cause 4: Docker host networking**
```bash
# If using host network, use localhost instead of service name
SIGNOZ_API_URL=http://localhost:4418

# Or use host.docker.internal (works on Docker Desktop)
SIGNOZ_API_URL=http://host.docker.internal:4418
```

### Database Connection Refused

**Symptom:**
```
Error: Cannot connect to database
postgresql://localhost:5432/rounds: Connection refused
```

**Solution:**
```bash
# Check if database is running
docker ps | grep postgres

# Check connection string
echo $STORE_POSTGRESQL_URL

# Test connection
docker exec postgres psql -U rounds -c "SELECT version();"

# Check database exists
docker exec postgres psql -U postgres -l | grep rounds

# Create database if missing
docker exec postgres createdb -U postgres rounds
```

### Health Check Failing

**Symptom:**
```
rounds  Unhealthy
```

**View health check status:**
```bash
docker inspect rounds --format='{{.State.Health.Status}}'

# View health check logs
docker inspect rounds --format='{{json .State.Health.Log}}' | jq '.[-5:]'
```

**Solutions:**
```bash
# Manually run health check
docker exec rounds /docker/healthcheck.sh

# Check if Python process is running
docker exec rounds ps aux | grep python

# Increase health check timeout
# In docker-compose.yml:
healthcheck:
  test: ["CMD-SHELL", "/docker/healthcheck.sh"]
  interval: 30s
  timeout: 15s      # Increased from 10s
  retries: 5
```

## Diagnosis Engine Issues

### Claude Code Authentication Failed

**Symptom:**
```
Error: Claude Code CLI authentication failed
```

**Causes and solutions:**

**Cause 1: Missing or invalid API key**
```bash
# Verify API key is set
docker exec rounds printenv ANTHROPIC_API_KEY | grep sk-ant

# Verify it's not empty or truncated
# Check .env.rounds
grep ANTHROPIC_API_KEY .env.rounds
```

**Cause 2: API key doesn't have credits**
```bash
# Check Anthropic account at https://console.anthropic.com/account/keys
# Ensure account has API credits
# Free trial is not eligible for API access
```

**Cause 3: API key expired**
```bash
# Generate new API key from https://console.anthropic.com/account/keys
# Update in .env.rounds
# Restart container: docker-compose restart rounds
```

### Diagnosis Timeouts

**Symptom:**
```
Timeout waiting for Claude Code diagnosis
```

**Causes:**
1. Claude Code CLI taking too long
2. Network issues reaching Anthropic API
3. Large codebase slowing down analysis

**Solutions:**
```bash
# Increase timeout (in code, not environment variable)
# Contact support if timeouts persist

# Check logs for more details
docker logs rounds | grep -i "timeout\|claude"

# Verify network connectivity to Anthropic
docker exec rounds curl -I https://api.anthropic.com/

# Reduce codebase size provided to Claude (advanced)
# Exclude vendor/node_modules from CODEBASE_PATH
```

### Budget Exceeded

**Symptom:**
```
Daily budget exceeded: $100.00
Skipping diagnosis
```

**Solution:**
```bash
# Check current spending
docker logs rounds | grep -i "budget\|cost"

# Increase daily budget (if acceptable)
DAILY_BUDGET_LIMIT=200.0

# Or use cheaper model
CLAUDE_MODEL=claude-3-5-sonnet  # Cheaper than claude-opus

# Or switch to OpenAI
DIAGNOSIS_BACKEND=openai
OPENAI_MODEL=gpt-3.5-turbo  # Much cheaper
```

## Performance Issues

### High Memory Usage

**Symptom:**
```bash
docker stats rounds
# MEMORY: 450MiB / 512MiB (very high)
```

**Diagnostic:**
```bash
# Monitor memory over time
docker stats --no-stream rounds

# Check for memory leaks (should stabilize)
watch -n 5 'docker stats --no-stream rounds'

# Look for large queries
docker logs rounds | grep "ERROR\|WARNING"
```

**Solutions:**

**Solution 1: Reduce batch size**
```bash
# Fewer errors processed per cycle = lower peak memory
POLL_BATCH_SIZE=50    # Reduced from 100

# Restart
docker-compose restart rounds
```

**Solution 2: Increase polling interval**
```bash
# Less frequent polling = more time to clean up between cycles
POLL_INTERVAL_SECONDS=120    # Increased from 60
```

**Solution 3: Increase container limit**
```yaml
# docker-compose.yml
deploy:
  resources:
    limits:
      memory: 1G    # Increased from 512M
```

### High CPU Usage

**Symptom:**
```bash
docker stats rounds
# CPU: 85% (consistently high)
```

**Causes:**
1. Processing large batch of errors
2. Claude Code analysis running
3. Database query bottleneck

**Solutions:**

**Solution 1: Reduce batch size**
```bash
POLL_BATCH_SIZE=50
```

**Solution 2: Increase polling interval**
```bash
POLL_INTERVAL_SECONDS=120
```

**Solution 3: Check for slow queries**
```bash
# Enable query logging (if using PostgreSQL)
docker exec postgres psql -U rounds -c "SET log_statement = 'all';"

# Or check SQLite performance
docker exec rounds sqlite3 /app/data/signatures.db ".timer on" "SELECT COUNT(*) FROM signatures WHERE status='new';"
```

### Slow Diagnosis

**Symptom:** Diagnosis takes 5+ minutes

**Check Claude Code performance:**
```bash
# View diagnosis logs with timestamps
docker logs rounds | grep -E "Starting diagnosis|Diagnosis complete" | head -20

# Check if network is slow
docker exec rounds curl -w "@-" -o /dev/null https://api.anthropic.com/ <<'EOF'
time_namelookup:  %{time_namelookup}\n
time_connect:     %{time_connect}\n
time_total:       %{time_total}\n
EOF
```

**Solutions:**
1. Use faster model: `CLAUDE_MODEL=claude-3-5-sonnet`
2. Reduce codebase size provided to Claude
3. Check network connectivity
4. Contact Anthropic support if API is slow

## Database Issues

### SQLite Locked Error

**Symptom:**
```
sqlite3.OperationalError: database is locked
```

**Cause:** Multiple processes trying to write to SQLite simultaneously

**Solution:**
```bash
# Only one Rounds instance can use SQLite
# For multiple instances, migrate to PostgreSQL:

# Export data from SQLite (if important)
docker exec rounds sqlite3 /app/data/signatures.db ".dump" > backup.sql

# Switch to PostgreSQL in .env.rounds
STORE_BACKEND=postgresql
STORE_POSTGRESQL_URL=postgresql://rounds:password@postgres:5432/rounds

# Restart
docker-compose down
docker-compose up -d
```

### PostgreSQL Connection Pool Exhausted

**Symptom:**
```
FATAL: remaining connection slots are reserved for non-replication superuser connections
```

**Causes:**
1. Too many Rounds instances
2. Connections not being closed properly
3. Database max_connections too low

**Solutions:**

**Solution 1: Reduce max connections from Rounds**
```bash
# Edit docker-compose.yml
environment:
  STORE_POSTGRESQL_URL: postgresql://rounds:pass@db:5432/rounds?application_name=rounds&statement_timeout=5000
```

**Solution 2: Increase database limit**
```bash
# Edit PostgreSQL config
docker exec postgres psql -U postgres -c "ALTER SYSTEM SET max_connections = 200;"
docker exec postgres pg_ctl reload
```

**Solution 3: Add connection pooling**
```bash
# Deploy PgBouncer between Rounds and PostgreSQL
# Update connection string to point to PgBouncer
STORE_POSTGRESQL_URL=postgresql://rounds:pass@pgbouncer:6432/rounds
```

### Database Corruption

**Symptom:**
```
sqlite3.DatabaseError: database disk image is malformed
```

**Solution:**

**For SQLite:**
```bash
# Backup current database
cp /app/data/signatures.db /app/data/signatures.db.corrupt

# Try to repair
docker exec rounds sqlite3 /app/data/signatures.db "PRAGMA integrity_check;"

# If repair fails, restore from backup
# Or delete and start fresh (signatures will be re-diagnosed):
rm /app/data/signatures.db
docker-compose restart rounds
```

**For PostgreSQL:**
```bash
# Check for corruption
docker exec postgres psql -U rounds -d rounds -c "REINDEX DATABASE rounds;"

# If still broken, restore from backup:
# See DEPLOY.md for backup/restore procedures
```

## Notification Issues

### Reports Not Generated

**Symptom:** No files in `./reports` directory

**Check configuration:**
```bash
# Verify notification backend is enabled
docker exec rounds printenv NOTIFICATION_BACKEND
# Should output: markdown

# Check output directory
docker exec rounds ls -la /app/reports/

# Check for errors in logs
docker logs rounds | grep -i "notification\|report"
```

**Solution:**
```bash
# Ensure markdown backend is configured
NOTIFICATION_BACKEND=markdown
NOTIFICATION_OUTPUT_DIR=/app/reports

# Ensure volume is mounted in docker-compose.yml
volumes:
  - ./reports:/app/reports:rw

# Restart
docker-compose restart rounds

# Check permissions
ls -la ./reports/
```

### GitHub Issues Not Created

**Symptom:** No issues appear in GitHub repo

**Check configuration:**
```bash
# Verify GitHub backend is enabled
docker exec rounds printenv NOTIFICATION_BACKEND
# Should output: github_issue

# Verify token and repo
docker exec rounds printenv | grep GITHUB
```

**Causes and solutions:**

**Cause 1: Invalid token**
```bash
# Verify token has repo access
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user

# If fails, generate new token from GitHub Settings > Developer settings > Personal access tokens

# Ensure token has 'repo' scope
```

**Cause 2: Wrong repository format**
```bash
# Must be owner/repo
GITHUB_REPO=anthropics/rounds  # Correct

# Test API access
curl -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/anthropics/rounds
```

**Cause 3: No permission to create issues**
```bash
# Ensure token has write access to repo
# Check repo settings > Collaborators & teams

# Or use org-level token with issue creation permission
```

## Logging and Debugging

### Enable Debug Logging

```bash
# Set debug log level
LOG_LEVEL=DEBUG

# Also enable debug mode
DEBUG=true

# Use JSON format for structured logging
LOG_FORMAT=json

# Restart
docker-compose restart rounds

# View debug logs
docker logs -f rounds | head -100
```

### Collect Logs for Support

```bash
# Save logs to file
docker logs rounds > rounds-logs.txt 2>&1

# Include configuration (without secrets)
docker exec rounds env | grep -v "_KEY\|_PASSWORD\|_TOKEN" > config.txt

# Include container info
docker ps --all --format "table {{.Names}}\t{{.Status}}" > containers.txt

# Create support bundle
tar czf rounds-debug-bundle.tar.gz rounds-logs.txt config.txt containers.txt
```

### View Container Processes

```bash
# Show running processes in container
docker exec rounds ps aux

# Show system resource usage
docker stats rounds

# Show open file handles
docker exec rounds lsof | head -50
```

## Production Troubleshooting

### Monitor Container Health

```bash
# Set up continuous health monitoring
watch -n 5 'docker ps | grep rounds && docker stats --no-stream rounds'

# Or use external monitoring
docker stats --no-stream rounds > /tmp/rounds-stats.log

# Analyze trends
tail -100 /tmp/rounds-stats.log | awk '{print $3}' | sort -n
```

### Backup and Recovery

**Check backup status:**
```bash
# For SQLite
ls -lah ./data/signatures.db

# For PostgreSQL
docker exec postgres pg_dump -U rounds rounds | wc -l  # Number of SQL statements

# Verify backup integrity
tar tzf backup-rounds.tar.gz | head
```

**Restore from backup:**
```bash
# Stop Rounds first
docker-compose stop rounds

# For SQLite: restore from backup
cp /backup/signatures.db.backup /path/to/data/signatures.db

# For PostgreSQL: restore from SQL dump
docker exec -T postgres psql -U rounds rounds < backup.sql

# Verify integrity
docker exec postgres psql -U rounds -c "SELECT COUNT(*) FROM signatures;"

# Restart
docker-compose start rounds
```

### Audit Configuration

```bash
# Export current running configuration
docker exec rounds env | grep -E "^[A-Z_]+=" > current-config.txt

# Compare with template
diff .env.rounds.template current-config.txt | less
```

## Getting Help

If the issue isn't resolved:

1. **Collect diagnostics:**
   ```bash
   docker logs rounds > logs.txt
   docker exec rounds env | grep -v "_KEY" > config.txt
   docker ps -a > containers.txt
   ```

2. **Review documentation:**
   - [DEPLOY.md](../DEPLOY.md) - Deployment guide
   - [CONFIGURATION.md](./CONFIGURATION.md) - Config reference
   - [DOCKER.md](./DOCKER.md) - Docker details

3. **Report issue:**
   - GitHub: https://github.com/anthropics/rounds/issues
   - Include: Logs, configuration (no secrets), error messages, reproduction steps

## See Also

- [DEPLOY.md](../DEPLOY.md) - Deployment scenarios
- [DOCKER.md](./DOCKER.md) - Docker details
- [CONFIGURATION.md](./CONFIGURATION.md) - Configuration reference
