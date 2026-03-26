---
name: rounds-reinvestigate
description: Re-run LLM diagnosis for an existing rounds error signature
user_invocable: true
args: [signature_id]
---

# Rounds: Reinvestigate Signature

Trigger a fresh LLM diagnosis for an existing error signature. Useful after
editing the diagnosis adapter or prompt, or when a previous diagnosis had low
confidence.

## Usage

```
/rounds-reinvestigate SIGNATURE_ID
```

## Implementation

Substituting `$ARGUMENTS` for the signature ID:

```bash
cd /workspace/rounds && python -m rounds.main cli-run reinvestigate '{"signature_id": "$ARGUMENTS"}'
```

The command outputs a JSON object. Parse it and present:

**On success** (`"status": "success"`):

- **Root Cause** — `diagnosis.root_cause`
- **Confidence** — `diagnosis.confidence` (low / medium / high)
- **Suggested Fix** — `diagnosis.suggested_fix`
- **Model** — `diagnosis.model`
- **Cost** — `diagnosis.cost_usd` formatted as `$X.XXXX`

**On error** (`"status": "error"`):

Show the `message` field. Common causes:
- Signature not found — run `/rounds-list` to confirm the ID
- Budget exceeded — check `CLAUDE_CODE_BUDGET_USD` / `DAILY_BUDGET_LIMIT`
- Telemetry unavailable — trace data needed for context

## Example

```
/rounds-reinvestigate 3f2a1b4c-8d9e-4f5a-b6c7-d8e9f0a1b2c3
```
