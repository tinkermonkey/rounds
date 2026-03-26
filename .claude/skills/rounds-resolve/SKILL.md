---
name: rounds-resolve
description: Mark a rounds error signature as resolved after a fix has been deployed
user_invocable: true
args: [signature_id, fix_description?]
---

# Rounds: Resolve Signature

Mark an error signature as resolved after a fix has been applied. Rounds will
stop monitoring and diagnosing this signature until the error reappears.

## Usage

```
/rounds-resolve SIGNATURE_ID [FIX_DESCRIPTION]
```

`FIX_DESCRIPTION` is optional free text describing what fix was applied.

## Implementation

Parse `$ARGUMENTS`: the first token is the signature ID; everything after it
(if any) is the fix description.

With a fix description:
```bash
cd /workspace/rounds && python -m rounds.main cli-run resolve '{"signature_id": "SIG_ID", "fix_applied": "FIX_DESCRIPTION"}'
```

Without a fix description:
```bash
cd /workspace/rounds && python -m rounds.main cli-run resolve '{"signature_id": "SIG_ID"}'
```

**On success** (`"status": "success"`):

Confirm: "Resolved signature `SIGNATURE_ID`" and include the fix description if
one was given.

**On error** (`"status": "error"`):

Show the `message` field.

## Examples

```
/rounds-resolve 3f2a1b4c-8d9e-4f5a-b6c7-d8e9f0a1b2c3
/rounds-resolve 3f2a1b4c-8d9e-4f5a-b6c7-d8e9f0a1b2c3 deployed null-check fix in PR #412
```
