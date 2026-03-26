---
name: rounds-investigate
description: Investigate a distributed trace by ID — fetches the full trace, reads source files, and explains the end-to-end code flow
user_invocable: true
args: [trace_id]
---

# Rounds: Investigate Trace

Investigate a distributed trace using rounds' trace investigation pipeline.
Fetches the full trace from the telemetry backend, reads the relevant source
files from the mounted codebase, and explains the end-to-end code flow with
actual file:line citations.

## Usage

```
/rounds-investigate TRACE_ID
```

`TRACE_ID` is a 32-char lowercase hex OpenTelemetry trace ID, e.g.:
`d5b99e396c3aeac81aa6074635254687`

## Implementation

Run the following, substituting `$ARGUMENTS` for the trace ID:

```bash
cd /workspace/rounds && python -m rounds.main cli-run investigate-trace $ARGUMENTS
```

The command outputs a JSON object. Parse it and present the results as follows:

**On success** (`"status": "success"`):

1. **Summary** — the `investigation.summary` field as a clear paragraph
2. **Code Flow** — `investigation.code_flow` as a numbered list; each entry is a
   step in the request path
3. **Services Involved** — `investigation.services_involved` as a comma-separated
   list
4. **Key Findings** — `investigation.key_findings` as a bulleted list
5. **Cost** — `investigation.cost_usd` formatted as `$X.XXXX`

**On error** (`"status": "error"`):

Show the `message` field and suggest:
- Verify the trace ID is a valid 32-char lowercase hex string
- Check that the telemetry backend (SigNoz/Jaeger) is reachable from the container
- Confirm `CODEBASE_PATH=/workspace/target` is set and the target is mounted

## Example

```
/rounds-investigate d5b99e396c3aeac81aa6074635254687
```
