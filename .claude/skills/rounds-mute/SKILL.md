---
name: rounds-mute
description: Mute a rounds error signature to suppress further notifications
user_invocable: true
args: [signature_id, reason?]
---

# Rounds: Mute Signature

Mute an error signature so rounds stops triggering notifications for it.
Useful for known false positives or acknowledged noise.

## Usage

```
/rounds-mute SIGNATURE_ID [REASON]
```

`REASON` is optional free text explaining why the signature is being muted.

## Implementation

Parse `$ARGUMENTS`: the first token is the signature ID; everything after it
(if any) is the reason string.

With a reason:
```bash
cd /workspace/rounds && python -m rounds.main cli-run mute '{"signature_id": "SIG_ID", "reason": "REASON"}'
```

Without a reason:
```bash
cd /workspace/rounds && python -m rounds.main cli-run mute '{"signature_id": "SIG_ID"}'
```

**On success** (`"status": "success"`):

Confirm: "Muted signature `SIGNATURE_ID`" and include the reason if one was given.

**On error** (`"status": "error"`):

Show the `message` field.

## Examples

```
/rounds-mute 3f2a1b4c-8d9e-4f5a-b6c7-d8e9f0a1b2c3
/rounds-mute 3f2a1b4c-8d9e-4f5a-b6c7-d8e9f0a1b2c3 known false positive in test env
```
