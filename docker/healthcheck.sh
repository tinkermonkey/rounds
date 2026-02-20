#!/bin/bash
# ============================================================================
# Health Check Script for Rounds Production Container
# ============================================================================
# This script verifies that the Rounds daemon process is running.
#
# Return codes:
# - 0: Service is healthy (process running)
# - 1: Service is unhealthy (process not running or other errors)
#
# Used by Docker HEALTHCHECK to monitor container status.
# ============================================================================

set -e

# Check if the Python process running rounds.main is active
# Note: We use a pattern without end-of-line anchor to match the command
# regardless of arguments (e.g., "python -m rounds.main daemon" or "python -m rounds.main")
if pgrep -f "python -m rounds\.main" > /dev/null; then
  exit 0  # Healthy
else
  # Log diagnostic information for debugging
  echo "ERROR: Rounds process not found. Expected: python -m rounds.main" >&2
  echo "Running processes matching 'python':" >&2
  pgrep -f "python" -l || echo "No Python processes found" >&2
  exit 1  # Unhealthy
fi
