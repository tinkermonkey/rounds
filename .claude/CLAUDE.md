# rounds — CLAUDE.md

This is the **rounds** project: a continuous error diagnosis daemon that polls SigNoz for production errors, fingerprints them, and runs LLM-powered root cause analysis.

**Architecture**: Hexagonal (Ports and Adapters). Core domain logic lives in `core/`, infrastructure adapters in `adapters/`. Single composition root in `main.py`. Never import adapters from core.

**Language/Runtime**: Python 3.11+, strict type annotations (mypy), async-first I/O, Pydantic 2.0, pytest with fakes (not mocks).

---

## Specialized Sub-Agents

This project has specialized sub-agents available. **Use them** instead of figuring things out from scratch — they have deep project-specific knowledge.

| Agent | When to use |
|---|---|
| `rounds-architect` | Before adding or moving code: understand where it belongs in the hexagonal architecture, how ports/adapters wire together, which layer owns what |
| `rounds-guardian` | After implementing: verify you haven't violated architecture boundaries, immutability patterns, or async/await conventions |
| `rounds-tester` | When writing tests: get the correct fakes-based patterns for this project, avoid mocks |
| `rounds-data-expert` | For any SQLite/aiosqlite work, repository patterns, or schema changes |
| `rounds-llm-expert` | For anything touching the Claude Code CLI integration, prompt construction, or LLM budget tracking |
| `rounds-doc-maintainer` | When documentation (README, architecture summaries, inline docs) needs updating |

### How to invoke a sub-agent

Use the `Task` tool with the agent name as `subagent_type`:

```
Task(subagent_type="rounds-architect", prompt="Where should I add a new telemetry backend adapter?")
Task(subagent_type="rounds-guardian", prompt="Review my changes to core/diagnosis.py for architecture violations")
Task(subagent_type="rounds-tester", prompt="Write tests for the new SignalGrouper port")
```

---

## Skills (Quick Commands)

| Skill | What it does |
|---|---|
| `/rounds-check` | Run mypy + ruff linting |
| `/rounds-test` | Run pytest with coverage |
| `/rounds-architecture` | Show architecture diagram and key file locations |
| `/rounds-patterns` | Show frozen dataclass, async port, and immutable collection patterns |
| `/rounds-budget` | Show current LLM usage budget and spending |
| `/rounds-daemon` | Start the daemon |

---

## Workflow Guidance

1. **Before implementing**: consult `rounds-architect` to confirm placement and `rounds-patterns` for conventions
2. **While implementing**: follow existing patterns — frozen dataclasses in core, async ports as Protocols, fakes in tests
3. **After implementing**: run `rounds-guardian` to catch boundary violations, then `rounds-tester` to write or verify tests
4. **Before finishing**: run `/rounds-check` and `/rounds-test` to confirm clean type-checking and passing tests
