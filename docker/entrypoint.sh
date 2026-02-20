#!/bin/bash
# ============================================================================
# Entrypoint Script for Rounds Production Container
# ============================================================================
# This script handles:
# 1. Auto-update of Claude Code CLI
# 2. Claude authentication verification
# 3. Directory creation for reports and data persistence
# 4. Launch of the Rounds daemon, CLI, or webhook
#
# Environment variables expected:
# - RUN_MODE: "daemon" (production), "cli" (manual operations), or "webhook" (HTTP server)
# - CLAUDE_CODE_VERSION: Optional specific version to install
# - All ROUNDS_ prefixed configuration variables
# ============================================================================

set -e

# Enable error output
trap 'echo "ERROR: entrypoint.sh failed at line $LINENO"' ERR

# Colors for output (unconditionally emitted for compatibility with log parsing)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "Starting Rounds container..."

# ============================================================================
# Step 1: Update Claude Code CLI
# ============================================================================
echo -e "${YELLOW}Updating Claude Code CLI...${NC}"

if [ -n "$CLAUDE_CODE_VERSION" ]; then
  echo "Installing Claude Code CLI version $CLAUDE_CODE_VERSION..."
  if npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"; then
    echo -e "${GREEN}Claude Code CLI version $CLAUDE_CODE_VERSION installed successfully${NC}"
  else
    echo -e "${RED}ERROR: Failed to install Claude Code CLI version $CLAUDE_CODE_VERSION${NC}"
    echo "Requested version: $CLAUDE_CODE_VERSION"
    exit 1
  fi
else
  echo "Installing latest Claude Code CLI..."
  # Try to update first (if already installed), then fall back to install
  if npm update -g "@anthropic-ai/claude-code" || npm install -g "@anthropic-ai/claude-code"; then
    echo -e "${GREEN}Claude Code CLI installed/updated successfully${NC}"
  else
    echo -e "${RED}ERROR: Could not install Claude Code CLI${NC}"
    exit 1
  fi
fi

# ============================================================================
# Step 2: Verify Claude Code CLI Installation
# ============================================================================
echo -e "${YELLOW}Verifying Claude Code CLI installation...${NC}"

if claude --version &>/dev/null; then
  CLAUDE_VERSION=$(claude --version)
  echo -e "${GREEN}Claude Code CLI verified: $CLAUDE_VERSION${NC}"
else
  echo -e "${RED}ERROR: Claude Code CLI not found or not working${NC}"
  echo "Make sure ANTHROPIC_API_KEY environment variable is set"
  exit 1
fi

# ============================================================================
# Step 3: Create Required Directories
# ============================================================================
echo -e "${YELLOW}Creating required directories...${NC}"

# Create reports directory (default: /app/reports)
REPORTS_DIR="${NOTIFICATION_OUTPUT_DIR:-/app/reports}"
if ! mkdir -p "$REPORTS_DIR" 2>/dev/null; then
  echo -e "${RED}ERROR: Failed to create reports directory at $REPORTS_DIR${NC}"
  echo "Check that parent directories exist and you have write permissions"
  exit 1
fi
echo -e "${GREEN}Created reports directory: $REPORTS_DIR${NC}"

# Create data directory for SQLite database (default: /app/data)
DATA_DIR="$(dirname "${STORE_SQLITE_PATH:-/app/data/signatures.db}")"
if ! mkdir -p "$DATA_DIR" 2>/dev/null; then
  echo -e "${RED}ERROR: Failed to create data directory at $DATA_DIR${NC}"
  echo "Check that parent directories exist and you have write permissions"
  exit 1
fi
echo -e "${GREEN}Created data directory: $DATA_DIR${NC}"

# ============================================================================
# Step 4: Launch Rounds
# ============================================================================
# Set default run mode if not specified
RUN_MODE="${RUN_MODE:-daemon}"

echo -e "${YELLOW}Launching Rounds in $RUN_MODE mode...${NC}"

# Verify run mode is valid
case "$RUN_MODE" in
  daemon|cli|webhook)
    echo -e "${GREEN}Starting in $RUN_MODE mode${NC}"
    ;;
  *)
    echo -e "${RED}ERROR: Invalid RUN_MODE '$RUN_MODE'. Must be 'daemon', 'cli', or 'webhook'${NC}"
    exit 1
    ;;
esac

# Export RUN_MODE for the Python application
export RUN_MODE

# Launch the Rounds application
# RUN_MODE from environment is used (defaults to "daemon" above)
# The Python application reads RUN_MODE via config.py
exec python -m rounds.main
