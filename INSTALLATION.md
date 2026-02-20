# Rounds Docker Image Distribution

This document explains how to obtain and use the Rounds Docker images.

## Available Images

| Image | Purpose | Size | Use Case |
|-------|---------|------|----------|
| `rounds:dist` | Production runtime | ~150MB | Daemon deployment to target projects |
| `rounds:dev` | Development environment | ~500MB | Local development and testing |

## Image Repositories

### Public Docker Hub (Recommended)

```bash
docker pull your-org/rounds:dist
docker pull your-org/rounds:dev
```

**Image Tags**:
- `rounds:dist` - Latest stable production build
- `rounds:dist-v1.0.0` - Specific version (recommended for production)
- `rounds:dev` - Latest development build
- `rounds:dev-latest` - Bleeding edge (may be unstable)

### Private Registry

If your organization hosts Rounds images privately:

```bash
# Login to your registry
docker login your-registry.com

# Pull production image
docker pull your-registry.com/rounds:dist

# Tag for local use
docker tag your-registry.com/rounds:dist rounds:dist
```

## Building Images Locally

### Build Production Image

```bash
# From Rounds repository root
docker build -t rounds:dist -f Dockerfile.prod .
```

**Or build directly from GitHub without cloning:**

```bash
docker build -t rounds:dist -f Dockerfile.prod https://github.com/your-org/rounds.git
```

### Build Development Image

```bash
# From Rounds repository root
docker build -t rounds:dev -f Dockerfile.dev .
```

## Image Versioning

Rounds follows semantic versioning (MAJOR.MINOR.PATCH):

- **MAJOR**: Breaking changes to configuration or ports
- **MINOR**: New features, backward-compatible
- **PATCH**: Bug fixes, security updates

**Recommended Tagging Strategy for Production**:

```yaml
# docker-compose.rounds.yml
services:
  rounds:
    image: your-org/rounds:dist-v1.2.3  # Pin to specific version
    # NOT: rounds:dist (floating tag)
```

## Updating Images

### Check for Updates

```bash
# Pull latest image
docker pull your-org/rounds:dist

# Check image ID changed
docker images your-org/rounds:dist
```

### Rolling Update (Zero Downtime)

```bash
# From target project root
docker-compose -f docker-compose.rounds.yml pull
docker-compose -f docker-compose.rounds.yml up -d
```

Docker Compose will:
1. Pull new image
2. Stop old container
3. Start new container with same volumes (preserves data)

### Verify Update

```bash
# Check running version
docker-compose -f docker-compose.rounds.yml exec rounds cat /app/VERSION

# Review changelog
docker-compose -f docker-compose.rounds.yml exec rounds cat /app/CHANGELOG.md
```

## Image Contents

### Production Image (`rounds:dist`)

```
/app/
├── rounds/              # Python package
├── entrypoint.sh        # Startup script
├── requirements.txt     # Python dependencies
├── data/                # Signature database (volume mount)
├── reports/             # Diagnosis output (volume mount)
└── VERSION              # Build version
```

**Base Image**: `python:3.11-slim`
**User**: `nurse` (UID 1000)
**Entrypoint**: `/app/entrypoint.sh`
**Default Command**: Daemon mode

### Development Image (`rounds:dev`)

Adds:
```
/app/
├── .git/                # Git repository
├── tests/               # Test suite
├── pyproject.toml       # Poetry configuration
├── pytest.ini           # Test configuration
└── [dev tools]          # mypy, ruff, pytest
```

**Base Image**: `python:3.11`
**User**: `root` (for development flexibility)
**Entrypoint**: `/bin/bash` (interactive shell)

## Image Security

### Vulnerability Scanning

Production images are scanned with Trivy on every build:

```bash
# Scan local image
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy image rounds:dist
```

### Image Signatures

Production images are signed with cosign:

```bash
# Verify signature (if enabled)
cosign verify --key cosign.pub your-org/rounds:dist-v1.0.0
```

## Troubleshooting

### Image pull fails with authentication error

```bash
# Login to Docker Hub
docker login

# Or login to private registry
docker login your-registry.com
```

### Image not found in docker-compose

Ensure the image name in `docker-compose.rounds.yml` matches your registry:

```yaml
# If using Docker Hub
image: your-org/rounds:dist

# If using private registry
image: your-registry.com/rounds:dist
```

### Old image cached

Force pull latest:

```bash
docker-compose -f docker-compose.rounds.yml pull --no-cache
docker-compose -f docker-compose.rounds.yml up -d --force-recreate
```

## CI/CD Integration

### GitHub Actions Example

```yaml
# .github/workflows/deploy-rounds.yml
name: Deploy Rounds
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Pull latest Rounds image
        run: docker pull your-org/rounds:dist

      - name: Deploy to production
        run: |
          docker-compose -f docker-compose.rounds.yml pull
          docker-compose -f docker-compose.rounds.yml up -d
```

## Build Arguments

Production image supports build-time customization:

```bash
docker build \
  --build-arg PYTHON_VERSION=3.11 \
  --build-arg CLAUDE_CLI_VERSION=latest \
  -t rounds:dist \
  -f Dockerfile.prod \
  .
```

## Support

- **Image Issues**: https://github.com/your-org/rounds/issues
- **Security Vulnerabilities**: security@your-org.com
- **Registry Access**: Contact DevOps team
