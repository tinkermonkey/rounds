# Docker Image Guide

This document provides detailed information about Rounds Docker images, their contents, customization options, and best practices.

## Image Overview

Rounds provides three pre-built Docker images for different use cases:

| Image | Tag | Use Case | Size | Base Image |
|-------|-----|----------|------|-----------|
| Production | `rounds:dist` | Production deployments | ~300MB | python:3.11-slim |
| Development | `rounds:dev` | Local development | ~400MB | python:3.11-slim |
| Agent | `rounds:agent` | Clauditoreum integration | varies | clauditoreum-orchestrator:latest |

### Production Image (`rounds:dist`)

Optimized for production deployments with minimal attack surface.

**Dockerfile:** `Dockerfile.dist`

**Features:**
- Multi-stage build for minimal image size
- Automatic Claude Code CLI auto-update on startup
- Health check endpoint
- Non-root user (UID 1000, username: `nurse`)
- Minimal dependencies (Python + critical packages only)

**Installed Packages:**
- Python 3.11 runtime
- Essential build tools (git, curl)
- Node.js and npm (required by Claude Code CLI)
- Python packages: httpx, pydantic, python-dotenv, aiosqlite
- Claude Code CLI (auto-updated at startup)

**Entrypoint:** `/docker/entrypoint.sh` - Handles authentication, directory setup, and mode selection

**Size:** ~300MB

**Security:**
- Runs as non-root user
- Minimal attack surface (slim base image)
- No development tools included
- Read-only filesystem support (with mounted volumes)

### Development Image (`rounds:dev`)

Comprehensive development environment with debugging tools.

**Dockerfile:** `Dockerfile.dev`

**Features:**
- Interactive bash shell
- Live code editing via mounted source
- Full test suite and type checking
- Debug logging enabled by default
- SQLite CLI tools

**Installed Packages:**
- All production packages
- Development tools: build-essential, git
- Testing: pytest, pytest-asyncio, pytest-cov
- Type checking: mypy, ruff
- Code formatting: black
- Database: sqlite3
- Utilities: curl, vim, nano

**Entrypoint:** `/bin/bash` - Interactive shell

**Size:** ~400MB

**Usage:**
```bash
# Interactive development session
docker-compose -f docker-compose.rounds.yml \
               -f docker-compose.rounds.dev.yml run --rm rounds

# Run specific command
docker-compose run --rm rounds python -m rounds.main
```

### Agent Image (`rounds:agent`)

Integration with the Clauditoreum orchestrator platform.

**Dockerfile:** `Dockerfile.agent`

**Base Image:** `clauditoreum-orchestrator:latest` (includes Claude CLI, Git CLI, GitHub CLI, Python)

**Features:**
- Pre-installed orchestrator tools
- Claude Code CLI already available
- GitHub integration

**Note:** Only used in Clauditoreum environments. See separate Clauditoreum documentation.

## Building Images

### Prerequisites

```bash
# Python 3.11+
python --version

# Docker and Docker Compose
docker --version
docker-compose --version

# (Optional) Node.js/npm for local Claude Code CLI testing
npm --version
```

### Build Commands

**Build all images:**
```bash
docker-compose build
```

**Build specific image:**
```bash
# Production
docker build -f Dockerfile.dist -t rounds:dist .

# Development
docker build -f Dockerfile.dev -t rounds:dev .

# Agent
docker build -f Dockerfile.agent -t rounds:agent .
```

**Build with custom Python version:**
```bash
docker build \
  --build-arg PYTHON_VERSION=3.12 \
  -f Dockerfile.dist \
  -t rounds:dist-py312 .
```

### Build Cache Optimization

Docker uses layer caching. Optimize builds by:

1. **Pin dependency versions:**
   - Prevents unnecessary cache invalidation
   - Improves reproducibility

2. **Order Dockerfile commands:**
   - Least frequently changed first (base image, system packages)
   - Most frequently changed last (source code)

3. **Use .dockerignore:**
   - Excludes unnecessary files from build context
   - Speeds up `docker build` command

```
# .dockerignore
.git
.gitignore
__pycache__
*.pyc
.pytest_cache
.mypy_cache
.env.rounds
.venv
venv
tests
docs
README.md
```

## Running Containers

### Basic Startup

```bash
# Production daemon mode
docker run -d \
  --name rounds \
  -e TELEMETRY_BACKEND=signoz \
  -e SIGNOZ_API_URL=http://signoz:4418 \
  -e RUN_MODE=daemon \
  --volume rounds-data:/app/data \
  rounds:dist

# Check logs
docker logs -f rounds

# Stop
docker stop rounds
```

### With Docker Compose

See [DEPLOY.md](../DEPLOY.md) for full Docker Compose examples.

### Environment Variables

All configuration is via environment variables. Mount or pass via `-e` flag:

```bash
docker run -e TELEMETRY_BACKEND=signoz \
           -e SIGNOZ_API_URL=http://localhost:4418 \
           -e RUN_MODE=daemon \
           rounds:dist
```

Load from file:
```bash
docker run --env-file .env.rounds rounds:dist
```

## Image Internals

### Container Filesystem

```
/app/
├── rounds/                  # Application source code
│   ├── main.py
│   ├── config.py
│   ├── core/
│   ├── adapters/
│   └── tests/
├── data/                    # Volume mount point for SQLite database
├── reports/                 # Volume mount point for markdown reports
└── requirements.txt         # Python dependencies

/docker/
├── entrypoint.sh           # Startup script (production image)
└── healthcheck.sh          # Health check probe

/home/nurse/                # Non-root user home directory
└── .config/claude-code/    # Optional: Claude Code CLI credentials (mounted)
```

### Users and Permissions

**Production & Development:**
- User: `nurse` (UID 1000)
- Group: `nurse` (GID 1000)
- Home: `/home/nurse`

**File ownership:**
```
/app/data:     nurse:nurse (rw-)
/app/reports:  nurse:nurse (rw-)
/app/rounds:   nurse:nurse (r-x)
```

### Networking

Containers use bridge networking by default:

```bash
# List networks
docker network ls

# Inspect rounds-network
docker network inspect rounds-network

# Container IP
docker inspect rounds --format='{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

All containers on the same Docker Compose network can communicate by service name (e.g., `http://signoz:4418`).

## Volume Mounts

### Data Persistence

**SQLite Database:**
```bash
--volume rounds-data:/app/data
```
- Contains `signatures.db` (diagnostic signatures and results)
- Persists across container restarts
- Must be writable by `nurse` user

**Markdown Reports:**
```bash
--volume ./.rounds/reports:/app/reports:rw
```
- Generated diagnosis reports
- Organized by date: `YYYY-MM-DD/signature-id.md`
- Readable from host filesystem

### Code Editing (Development)

Mount source for live code editing:
```bash
--volume ./rounds:/workspace/rounds:rw
```

Changes on host are immediately reflected in container. Useful for:
- Debugging with added print statements
- Testing configuration changes
- Iterating on adapter implementations

### Secrets (Optional)

Mount Claude Code CLI credentials:
```bash
--volume ~/.config/claude-code:/home/nurse/.config/claude-code:ro
```

**Note:** Credentials are typically passed via `ANTHROPIC_API_KEY` environment variable instead. Only use volume mount for non-containerized Claude Code CLI flows.

## Health Checks

### Built-in Health Check

The production image includes a basic health check:

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD /docker/healthcheck.sh
```

Check status:
```bash
docker ps | grep rounds
# NAMES       STATUS
# rounds      Up 2 minutes (healthy)
```

### Custom Health Check

Override in docker-compose.yml:

```yaml
services:
  rounds:
    image: rounds:dist
    healthcheck:
      test: ["CMD", "/docker/healthcheck.sh"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 5s
```

Disable health check:
```yaml
healthcheck:
  disable: true
```

## Logging

### Container Logs

View logs with Docker CLI:
```bash
# Real-time stream
docker logs -f rounds

# Last 50 lines
docker logs --tail=50 rounds

# Since specific time
docker logs --since 30m rounds

# Timestamps
docker logs -t rounds
```

### Log Driver Configuration

Configure in docker-compose.yml:

```yaml
services:
  rounds:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"        # Rotate at 10MB
        max-file: "3"          # Keep 3 files (30MB total)
        labels: "service=rounds"
```

**Alternative drivers:**
- `awslogs` - CloudWatch Logs
- `splunk` - Splunk Cloud
- `awsfirelens` - AWS for ECS
- `journald` - systemd journal
- `syslog` - Syslog protocol

### Application Logs

Rounds logs are written to stdout/stderr in the container. Format controlled by:

```bash
LOG_FORMAT=text     # Human-readable text
LOG_FORMAT=json     # Structured JSON (for log aggregation)
LOG_LEVEL=INFO      # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

## Performance Tuning

### Resource Limits

Typical configuration in docker-compose.yml:

```yaml
services:
  rounds:
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
        reservations:
          cpus: '0.25'
          memory: 128M
```

**Recommended limits:**
- Small deployments (< 10 signatures/hour): 0.5 CPU, 256 MB
- Medium deployments (10-100 signatures/hour): 1.0 CPU, 512 MB
- Large deployments (> 100 signatures/hour): 2.0 CPU, 1 GB

Monitor actual usage:
```bash
docker stats rounds
```

### Startup Sequence and Timing

The production image startup follows this sequence:

1. **Entrypoint initialization** (< 1 second)
   - Validate script syntax
   - Initialize color output

2. **Claude Code CLI update** (5-30 seconds typical)
   - Check for existing installation
   - Attempt npm update (if installed) or npm install
   - **Startup timing is logged before and after this step**
   - If network fails, falls back to existing installation (if present)
   - If no fallback available, exits with clear error message

3. **Authentication verification** (< 1 second)
   - Verify `ANTHROPIC_API_KEY` environment variable is set
   - Confirm CLI is functional and responsive

4. **Directory creation** (< 1 second)
   - Create `/app/reports` for diagnosis output
   - Create `/app/data` for signature database
   - Exit with error if permissions insufficient

5. **Application startup** (varies by mode)
   - Daemon: Begins polling for errors
   - CLI: Displays interactive menu
   - Webhook: Listens on configured port

**Typical total startup time:** 10-30 seconds (mostly Claude Code CLI update)

#### Startup Logging Examples

**Normal update with timing information:**
```
Starting Rounds container...
Preparing Claude Code CLI...
Claude Code CLI already installed: Claude Code version 1.2.2
Updating Claude Code CLI to latest version...
Claude Code CLI ready in 12s: Claude Code version 1.2.3
Verifying Claude Code CLI installation and authentication...
Claude Code CLI verified: Claude Code version 1.2.3
ANTHROPIC_API_KEY is configured
Created reports directory: /app/reports
Created data directory: /app/data
Launching Rounds in daemon mode...
Starting in daemon mode
```

**Offline fallback when network fails:**
```
Starting Rounds container...
Preparing Claude Code CLI...
Claude Code CLI already installed: Claude Code version 1.2.2
Updating Claude Code CLI to latest version...
ERROR: Could not install or update Claude Code CLI
Network failure detected. Falling back to existing installation: Claude Code version 1.2.2
Verifying Claude Code CLI installation and authentication...
Claude Code CLI verified: Claude Code version 1.2.2
ANTHROPIC_API_KEY is configured
...continuing with daemon startup
```

**Authentication missing (hard failure):**
```
Starting Rounds container...
Preparing Claude Code CLI...
Claude Code CLI already installed: Claude Code version 1.2.3
Updating Claude Code CLI to latest version...
Claude Code CLI ready in 2s: Claude Code version 1.2.3
Verifying Claude Code CLI installation and authentication...
Claude Code CLI verified: Claude Code version 1.2.3
ERROR: ANTHROPIC_API_KEY environment variable not set
Authentication is required for Claude Code to function.
[Container exits with status 1]
```

#### Deployment Notes

- **Update delays are normal**: Network and npm operations add 5-30 seconds to first startup
- **Successive restarts are faster**: Pre-installed Claude Code enables small diff updates instead of full installations
- **Authentication is required**: Always provide `ANTHROPIC_API_KEY` environment variable
- **Monitor startup logs**: Check container logs if startup exceeds 60 seconds, indicating potential issues

### Reduce Image Size

If disk space is constrained:

```dockerfile
# Keep only production dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Remove unnecessary files
RUN apt-get remove build-essential git

# Use distroless base image (advanced)
FROM gcr.io/distroless/python3.11
```

## Security Best Practices

### Image Security

1. **Minimal base image:**
   - `python:3.11-slim` reduces attack surface
   - Smaller image = fewer vulnerabilities

2. **Non-root user:**
   - Container runs as `nurse` (UID 1000)
   - Read-only filesystem support
   - Prevents privilege escalation

3. **Secrets management:**
   - Never embed API keys in Dockerfile
   - Use environment variables or secrets
   - Rotate credentials regularly

### Container Security

```yaml
services:
  rounds:
    image: rounds:dist

    # Security options
    security_opt:
      - no-new-privileges:true

    # Read-only root filesystem (if applicable)
    read_only: true

    # Limit capabilities
    cap_drop:
      - ALL
    cap_add:
      - NET_BIND_SERVICE

    # User context
    user: "1000:1000"

    # Resource limits
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
```

### Vulnerability Scanning

Scan images for known vulnerabilities:

```bash
# Trivy
trivy image rounds:dist

# Snyk
snyk container test rounds:dist

# Docker Scout
docker scout cves rounds:dist
```

### Registry Security

Push to secure registry:

```bash
# Tag for registry
docker tag rounds:dist registry.example.com/rounds:dist

# Push (with authentication)
docker login registry.example.com
docker push registry.example.com/rounds:dist

# Pull
docker pull registry.example.com/rounds:dist
```

## Customization

### Building Custom Images

Create custom Dockerfile:

```dockerfile
FROM rounds:dist

# Add custom configurations
ENV LOG_LEVEL=DEBUG
ENV POLL_INTERVAL_SECONDS=30

# Add custom entrypoint
COPY custom-entrypoint.sh /docker/entrypoint.sh
RUN chmod +x /docker/entrypoint.sh

# Add additional Python packages
RUN pip install my-custom-package==1.0.0
```

Build and run:
```bash
docker build -t rounds:custom .
docker run -e RUN_MODE=daemon rounds:custom
```

### Multi-Stage Builds

Reduce image size with multi-stage builds:

```dockerfile
FROM python:3.11-slim AS builder
RUN pip install -r requirements.txt

FROM python:3.11-slim
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
```

## Troubleshooting

### Container Won't Start

**Symptoms:** `docker ps` shows no running container

**Debug:**
```bash
# Check exit code
docker ps -a | grep rounds

# View logs
docker logs rounds

# Run interactively to see errors
docker run --rm -it \
  -e TELEMETRY_BACKEND=signoz \
  -e SIGNOZ_API_URL=http://localhost:4418 \
  rounds:dist bash
```

**Common issues:**
- Missing environment variables: Verify `.env.rounds`
- Network unreachable: Check `SIGNOZ_API_URL`, firewall rules
- Claude Code CLI authentication: Missing `ANTHROPIC_API_KEY`

### High Memory Usage

```bash
# Monitor memory
docker stats rounds

# Check for memory leaks
docker inspect rounds --format='{{.State.Pid}}' | xargs -I {} ps aux | grep {}

# Reduce batch size to lower memory footprint
# Edit .env.rounds: POLL_BATCH_SIZE=50
```

### Container Keeps Restarting

```bash
# Check restart policy
docker inspect rounds --format='{{.RestartPolicy}}'

# View recent logs
docker logs --tail=100 rounds

# Check disk space
docker exec rounds df -h /app/data
```

### Permission Denied Errors

```bash
# Verify user in container
docker exec rounds whoami

# Fix volume permissions on host
sudo chown -R 1000:1000 ./.rounds/reports

# Verify container can write
docker exec rounds touch /app/data/test.txt
```

## See Also

- [DEPLOY.md](../DEPLOY.md) - Deployment scenarios and configurations
- [CONFIGURATION.md](CONFIGURATION.md) - Complete environment variable reference
- [Dockerfile.dist](../Dockerfile.dist) - Production image specification
- [Dockerfile.dev](../Dockerfile.dev) - Development image specification
- Docker documentation: https://docs.docker.com/
