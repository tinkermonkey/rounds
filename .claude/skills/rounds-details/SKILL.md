---
name: rounds-details
description: Get full details for a single rounds error signature including diagnosis, recent events, and related signatures
user_invocable: true
args: [signature_id]
---

# Rounds: Signature Details

Retrieve complete details for one error signature: status, occurrence history,
LLM diagnosis, recent events, and related signatures.

## Usage

```
/rounds-details SIGNATURE_ID
```

`SIGNATURE_ID` is a UUID string from the rounds database (visible in `/rounds-list`
output).

## Implementation

Substituting `$ARGUMENTS` for the signature ID:

```bash
cd /workspace/rounds && python -m rounds.main cli-run details '{"signature_id": "$ARGUMENTS"}'
```

The command outputs a JSON object. Parse it and present:

**On success** (`"status": "success"`), render `data` (a `SignatureDetails` object):

**Identity**
- Signature ID, Fingerprint, Service, Error Type

**Status & History**
- Status, Occurrence count, First seen, Last seen

**Message Template**
- The error message pattern

**Diagnosis** (if present in `data.signature.diagnosis`)
- Root cause, Confidence level, Suggested fix, Cost, Model, Diagnosed at

**Recent Events** (up to 5 from `data.recent_events`)
- Timestamp + error message for each

**Related Signatures** (up to 5 from `data.related_signatures`)
- ID (first 8 chars), Service, Occurrence count

**On error** (`"status": "error"`):

Show the `message` field. If it mentions "not found", the signature ID may be wrong
— suggest running `/rounds-list` to see valid IDs.

## Example

```
/rounds-details 3f2a1b4c-8d9e-4f5a-b6c7-d8e9f0a1b2c3
```
