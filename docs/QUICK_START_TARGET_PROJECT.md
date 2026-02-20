# Quick Start: Deploy Rounds to Your Project

This guide shows you how to deploy Rounds monitoring to your target application from your project's root directory.

## Prerequisites

- Docker and docker-compose installed
- Your application is instrumented with OpenTelemetry traces
- Access to a telemetry backend (SigNoz, Jaeger, or Grafana Stack)

## Installation

### Option 1: Automated Installation (Recommended)

Run this command from your target project's root directory:

```bash
curl -fsSL https://raw.githubusercontent.com/your-org/rounds/main/scripts/install-compose.sh | bash
```

This will download:
- `docker-compose.rounds.yml` - Rounds service definition
- `.env.rounds.template` - Configuration template
- `.env.rounds` - Your local configuration (created from template)

### Option 2: Manual Installation

1. Download the required files to your project root:

```bash
# From your project root directory
curl -fsSL https://raw.githubusercontent.com/your-org/rounds/main/docker-compose.rounds.yml -o docker-compose.rounds.yml
curl -fsSL https://raw.githubusercontent.com/your-org/rounds/main/.env.rounds.template -o .env.rounds.template
cp .env.rounds.template .env.rounds
```

## Configuration

Edit `.env.rounds` in your project root and configure:

### 1. Telemetry Backend

```bash
# Choose your telemetry backend
TELEMETRY_BACKEND=signoz  # or jaeger, grafana_stack

# For SigNoz:
SIGNOZ_API_URL=http://localhost:3301
SIGNOZ_API_KEY=your-api-key-here

# For Jaeger:
JAEGER_API_URL=http://localhost:16686

# For Grafana Stack:
GRAFANA_TEMPO_URL=http://localhost:3200
GRAFANA_LOKI_URL=http://localhost:3100
```

### 2. Diagnosis Engine (Optional)

```bash
# Default is Claude Code CLI
DIAGNOSIS_BACKEND=claude_code
CLAUDE_CODE_BUDGET_USD=0.50
CLAUDE_MODEL=claude-sonnet-4

# Or use OpenAI:
# DIAGNOSIS_BACKEND=openai
# OPENAI_API_KEY=sk-...
```

### 3. Polling Configuration

```bash
POLL_INTERVAL_SECONDS=60
ERROR_LOOKBACK_MINUTES=15
POLL_BATCH_SIZE=100
```

### 4. Notification Backend

```bash
# Choose notification method
NOTIFICATION_BACKEND=markdown  # or stdout, github_issue

# For markdown (recommended):
NOTIFICATION_OUTPUT_DIR=/app/reports

# For GitHub Issues:
# GITHUB_TOKEN=ghp_...
# GITHUB_REPO=your-org/your-repo
```

## Obtain Docker Image

### Option 1: Pull from Docker Hub

```bash
docker pull your-org/rounds:dist
```

### Option 2: Build Locally

```bash
docker build -t rounds:dist -f Dockerfile.prod https://github.com/your-org/rounds.git
```

### Option 3: Use Private Registry

If your organization hosts the image privately:

```bash
docker login your-registry.com
docker pull your-registry.com/rounds:dist
docker tag your-registry.com/rounds:dist rounds:dist
```

## Start Rounds Daemon

From your project root:

```bash
docker-compose -f docker-compose.rounds.yml up -d
```

## Verify Deployment

### Check container status:

```bash
docker-compose -f docker-compose.rounds.yml ps
```

Expected output:
```
NAME                COMMAND                  SERVICE             STATUS
rounds-daemon       "/app/entrypoint.sh"     rounds              Up 10 seconds
```

### View logs:

```bash
docker-compose -f docker-compose.rounds.yml logs -f
```

You should see:
```
rounds-daemon | Starting Rounds daemon...
rounds-daemon | Telemetry backend: signoz
rounds-daemon | Poll interval: 60s
rounds-daemon | Polling for errors...
```

### Check diagnosis reports:

```bash
ls -la .rounds/reports/
```

Reports are organized as:
```
.rounds/reports/
├── 2026-02-20/
│   ├── 14-30-00_auth-service_ConnectionTimeout.md
│   ├── 14-35-00_api-gateway_NullPointerException.md
│   └── ...
└── summary.md
```

## Troubleshooting

### Image not found

If you see `Error: image rounds:dist not found`, pull or build the image first:

```bash
docker pull your-org/rounds:dist
# OR
docker build -t rounds:dist -f Dockerfile.prod https://github.com/your-org/rounds.git
```

### Permission denied on reports directory

Ensure the `.rounds/reports` directory is writable:

```bash
mkdir -p .rounds/reports
chmod 755 .rounds/reports
```

### No errors detected

Verify your telemetry backend configuration:

```bash
# Check if SIGNOZ_API_URL is reachable
curl -I $SIGNOZ_API_URL

# Review telemetry backend logs
docker-compose -f docker-compose.rounds.yml logs rounds | grep -i telemetry
```

### Diagnosis budget exceeded

Check current spending:

```bash
docker-compose -f docker-compose.rounds.yml exec rounds cat /app/data/budget.json
```

Adjust budget limits in `.env.rounds`:

```bash
CLAUDE_CODE_BUDGET_USD=1.00  # Increase per-diagnosis budget
DAILY_BUDGET_LIMIT=10.00     # Increase daily cap
```

## Stop Rounds Daemon

```bash
docker-compose -f docker-compose.rounds.yml down
```

## Update Rounds

Pull latest image and restart:

```bash
docker-compose -f docker-compose.rounds.yml pull
docker-compose -f docker-compose.rounds.yml up -d
```

## Next Steps

- Review diagnosis reports in `.rounds/reports/`
- Configure GitHub Issues integration for automated ticket creation
- Adjust polling intervals based on error volume
- Set up alerts for high-severity diagnoses

## Support

- **Documentation**: https://github.com/your-org/rounds
- **Issues**: https://github.com/your-org/rounds/issues
- **Architecture Guide**: See `DEPLOY.md` for detailed deployment options
