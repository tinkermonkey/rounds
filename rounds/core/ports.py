"""Port interfaces for the Rounds diagnostic system.

These abstract base classes define the boundaries between core
domain logic and external adapters. Implementations live in the
adapters/ package.

Port Interface Categories:

1. **Driven Ports** (core calls out to adapters)
   - TelemetryPort: Retrieve errors, traces, logs
   - SignatureStorePort: Persist and query signatures
   - DiagnosisPort: LLM-powered root cause analysis
   - NotificationPort: Report findings to developers

2. **Driving Ports** (adapters/external systems call into core)
   - PollPort: Entry point for poll and investigation cycles
   - ManagementPort: Human-initiated actions (mute, resolve, etc.)
"""

# Stub file - port interfaces to be implemented in Phase 2
