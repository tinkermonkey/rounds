---
name: rounds-help
description: Print available rounds skills, project directory layout, and a brief overview of what rounds does
user_invocable: true
args: []
---

# Rounds: Help

Print a quick-reference guide for working with rounds inside the container.

## Implementation

Output the following text exactly, with no additional commentary:

```
ROUNDS — Autonomous Error Diagnosis Agent
==========================================

Rounds is a lightweight daemon that watches your OpenTelemetry data, fingerprints
failure patterns, and uses LLM-powered analysis to diagnose root causes in your
codebase. It makes periodic passes over your telemetry — polling for errors,
tracing them through your system, recognizing recurring patterns, and building up
a persistent knowledge base of failure signatures and diagnoses. Think of it as a
nurse making rounds on your running software.

SKILLS AVAILABLE IN THE ROUNDS CONTEXT
---------------------------------------
/rounds-help                          — show this message
/rounds-investigate TRACE_ID          — investigate a distributed trace
/rounds-list [STATUS]                 — list error signatures
/rounds-details SIGNATURE_ID          — full signature details
/rounds-reinvestigate SIG_ID          — re-run LLM diagnosis
/rounds-mute SIG_ID [REASON]          — mute a signature
/rounds-resolve SIG_ID                — mark a signature resolved
/rounds-daemon                        — start the polling daemon
/rounds-test                          — run pytest
/rounds-check                         — mypy + ruff

DIRECTORIES (inside container)
-------------------------------
/workspace/rounds   — rounds source code (this project)
/workspace/target   — target codebase being monitored and diagnosed
```
