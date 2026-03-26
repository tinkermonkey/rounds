---
name: rounds-list
description: List error signatures in the rounds database, optionally filtered by status
user_invocable: true
args: [status?]
---

# Rounds: List Signatures

List all error signatures tracked by rounds, with optional status filter.

## Usage

```
/rounds-list [STATUS]
```

`STATUS` (optional): `new`, `investigating`, `diagnosed`, `resolved`, or `muted`.
Omit to list all signatures.

## Implementation

Without a status filter:
```bash
cd /workspace/rounds && python -m rounds.main cli-run list
```

With a status filter, substituting `$ARGUMENTS` for the status value:
```bash
cd /workspace/rounds && python -m rounds.main cli-run list '{"status": "$ARGUMENTS"}'
```

If `$ARGUMENTS` is empty, run without the JSON argument.

The command outputs a JSON object. Parse it and present the results as a table:

**On success** (`"status": "success"`):

Render `signatures` as a Markdown table with columns:

| ID (first 8 chars) | Service | Error Type | Status | Occurrences | Last Seen |

If the list is empty, say "No signatures found" (with the filter if one was used).

**On error** (`"status": "error"`):

Show the `message` field.

## Examples

```
/rounds-list
/rounds-list new
/rounds-list diagnosed
```
