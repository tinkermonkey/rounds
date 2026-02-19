#!/bin/bash
# ============================================================================
# Health Check Script for Rounds Production Container
# ============================================================================
# This script verifies that the Rounds daemon is running and responsive.
#
# Return codes:
# - 0: Service is healthy
# - 1: Service is unhealthy (process not running or other errors)
#
# Used by Docker HEALTHCHECK to monitor container status.
# ============================================================================

set -e

# Check if the Python process running rounds.main is active
if pgrep -f "python -m rounds.main$" > /dev/null; then
  exit 0  # Healthy
else
  exit 1  # Unhealthy
fi
