#!/bin/bash
set -e

# Rounds Docker Compose Installer
# This script installs Rounds monitoring into your target project directory
# Run from your target project root: curl -fsSL https://raw.githubusercontent.com/your-org/rounds/main/scripts/install-compose.sh | bash

echo "🏥 Rounds Docker Compose Installer"
echo "=================================="
echo ""

# Validate Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed"
    echo "   Please install Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

# Validate docker-compose is available
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null 2>&1; then
    echo "❌ Error: docker-compose is not installed"
    echo "   Please install docker-compose: https://docs.docker.com/compose/install/"
    exit 1
fi

# Determine base URL for downloading files
ROUNDS_REPO="https://raw.githubusercontent.com/your-org/rounds/main"

echo "📥 Downloading Rounds configuration files..."

# Download docker-compose.rounds.yml
curl -fsSL "${ROUNDS_REPO}/docker-compose.rounds.yml" -o docker-compose.rounds.yml
if [ $? -ne 0 ]; then
    echo "❌ Failed to download docker-compose.rounds.yml"
    exit 1
fi
echo "   ✓ docker-compose.rounds.yml"

# Download .env.rounds.template
curl -fsSL "${ROUNDS_REPO}/.env.rounds.template" -o .env.rounds.template
if [ $? -ne 0 ]; then
    echo "❌ Failed to download .env.rounds.template"
    exit 1
fi
echo "   ✓ .env.rounds.template"

# Create .env.rounds if it doesn't exist
if [ ! -f .env.rounds ]; then
    cp .env.rounds.template .env.rounds
    echo "   ✓ .env.rounds (created from template)"
else
    echo "   ⚠ .env.rounds already exists (not overwritten)"
fi

echo ""
echo "✅ Installation complete!"
echo ""
echo "📋 Next steps:"
echo "   1. Edit .env.rounds and configure your settings:"
echo "      - Set TELEMETRY_BACKEND (signoz/jaeger/grafana_stack)"
echo "      - Set backend-specific URLs and API keys"
echo "      - Configure DIAGNOSIS_BACKEND if not using claude_code"
echo ""
echo "   2. Ensure the rounds:dist Docker image is available:"
echo "      - Pull from Docker Hub: docker pull your-org/rounds:dist"
echo "      - OR build locally: docker build -t rounds:dist -f Dockerfile.prod https://github.com/your-org/rounds.git"
echo ""
echo "   3. Start Rounds daemon:"
echo "      docker-compose -f docker-compose.rounds.yml up -d"
echo ""
echo "   4. View logs:"
echo "      docker-compose -f docker-compose.rounds.yml logs -f"
echo ""
echo "   5. Check diagnosis reports:"
echo "      ls -la .rounds/reports/"
echo ""
echo "📚 Documentation: https://github.com/your-org/rounds/blob/main/docs/QUICK_START_TARGET_PROJECT.md"
