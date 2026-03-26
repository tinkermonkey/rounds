# Rounds Development Container

You are running inside the rounds development container — a Docker environment
wiring together the rounds diagnostic system source code and a target codebase
under analysis.

## Directory Layout

| Path | Purpose | Writable |
|------|---------|----------|
| `/workspace/rounds/` | Rounds project root (this repo) | yes |
| `/workspace/rounds/rounds/` | Python package source | yes |
| `/workspace/target/` | Target codebase under analysis | yes |
| `/app/data/signatures.db` | Signature database | yes |
| `/app/reports/` | Diagnosis report output | yes |

All changes to `/workspace/rounds/rounds/` take effect immediately on the next
rounds CLI invocation — no container restart needed.

## Running Rounds Commands

### Single command (preferred — use this in skills)

```bash
cd /workspace/rounds && python -m rounds.main cli-run COMMAND [ARGS]
```

`ARGS` is either a JSON object or, for `investigate-trace`, a bare trace ID:

```bash
python -m rounds.main cli-run investigate-trace d5b99e396c3aeac81aa6074635254687
python -m rounds.main cli-run list
python -m rounds.main cli-run list '{"status": "new"}'
python -m rounds.main cli-run details '{"signature_id": "uuid-here"}'
python -m rounds.main cli-run reinvestigate '{"signature_id": "uuid-here"}'
python -m rounds.main cli-run mute '{"signature_id": "uuid-here", "reason": "flap"}'
python -m rounds.main cli-run resolve '{"signature_id": "uuid-here"}'
```

Output is always JSON. A successful result has `"status": "success"`; errors have
`"status": "error"` with a `"message"` field.

### Interactive REPL (for exploratory work)

```bash
cd /workspace/rounds && RUN_MODE=cli python -m rounds.main
```

Type `help` at the `rounds>` prompt to see available commands, `exit` to quit.

### One-shot scan or daemon

```bash
# Single poll cycle
cd /workspace/rounds && python -m rounds.main scan

# Continuous daemon
cd /workspace/rounds && python -m rounds.main
```

## Available Skills

Use these slash commands in this Claude session:

| Skill | What it does |
|-------|-------------|
| `/rounds-investigate TRACE_ID` | Investigate a distributed trace end-to-end |
| `/rounds-list [STATUS]` | List error signatures (optionally filtered by status) |
| `/rounds-details SIGNATURE_ID` | Full details for one signature |
| `/rounds-reinvestigate SIGNATURE_ID` | Re-run LLM diagnosis for a signature |
| `/rounds-mute SIGNATURE_ID [REASON]` | Mute a signature |
| `/rounds-resolve SIGNATURE_ID` | Mark a signature resolved |
| `/rounds-daemon` | Start the polling daemon |
| `/rounds-test` | Run the pytest test suite |
| `/rounds-check` | Run mypy + ruff |
| `/rounds-architecture` | Show hexagonal architecture overview |
| `/rounds-patterns` | Show frozen dataclass / async port patterns |

## Development Workflow

1. Run a skill (e.g. `/rounds-investigate TRACE_ID`) to see current behaviour.
2. Identify the code to change — source lives in `/workspace/rounds/rounds/`.
3. Edit the file with the Edit tool.
4. Re-run the same skill to verify the change — no restart required.
5. Run `/rounds-test` to confirm tests still pass.
6. Run `/rounds-check` to confirm type safety and lint.

## Working with the Target Codebase

The target project is mounted read-write at `/workspace/target/`. You can read and
modify its source files — changes go directly to the host filesystem.

## Switching to Target Project Context

To start a Claude session rooted in the target project (giving access to its own
CLAUDE.md, skills, and agents):

```bash
# From the host, in a separate terminal:
docker compose -f docker-compose.dev.claude.yml run --rm rounds-target-claude
```

Or, if you are already inside this container:

```bash
claude --cwd /workspace/target
```
