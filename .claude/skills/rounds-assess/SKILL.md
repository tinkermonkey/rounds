---
name: rounds-assess
description: Assess telemetry data — identifies distinct transaction types from an explore query and launches sub-agents to analyze each for instrumentation quality, code efficiency, usage correctness, and skill improvement opportunities
user_invocable: true
args: [query?]
---

# Rounds: Assess

Run a structured assessment of telemetry data using a two-phase approach:

1. **Explore phase** — query the telemetry backend (via the `rounds-explore` workflow)
   using the user's description to gather traces, spans, and logs.

2. **Assess phase** — identify the distinct transaction types present in the data,
   launch one sub-agent per transaction type, then synthesize all findings into a
   final report.

## Usage

```
/rounds-assess [natural-language description of the telemetry window to assess]
```

`$ARGUMENTS` is passed directly to the explore phase to scope the query (e.g.
"the payment service between 2pm and 3pm today" or "all errors from the last hour").

---

## Phase 1 — Explore

Invoke the full `rounds-explore` skill workflow using `$ARGUMENTS` as the query.
The explore phase uses the `search-logs`, `search-spans`, and `get-trace-tree`
commands (see the rounds-explore skill for full parameter reference). Collect:

- The full set of spans from the described time window
- Log entries correlated with those spans
- Trace trees for representative traces of each distinct operation

Goal: build a working list of **distinct transaction types** — unique operation names,
command names, or request patterns that were active in the described window. Group
spans by operation name or top-level command; treat each distinct group as one
transaction type.

---

## Phase 2 — Per-Transaction Assessment

For each distinct transaction type identified in Phase 1, launch a sub-agent using
the Agent tool. Each sub-agent receives:

- The transaction type name and a representative sample of its spans and trace trees
- Relevant source files read from `/workspace/target`
- The target project's Claude Code skills from `/workspace/target/.claude/`

Each sub-agent must answer these four questions:

### 1. Instrumentation completeness

Is the transaction fully instrumented so that the code flow and decision forks are
easily determined from the OTEL spans alone? Look for:
- Code branches with no corresponding span
- Spans with no useful attributes (operation name only, no parameters or status)
- Missing parent-child relationships that flatten the hierarchy
- Error paths that produce no span or log entry

Rate: **good** (flow is clear end-to-end), **partial** (some gaps), or **poor**
(span data is not sufficient to follow the code path).

### 2. Code quality

Is the code that implements this transaction efficient and effective? Look for:
- Unnecessary work or redundant calls visible in the trace
- Suspiciously long spans with no child spans (hidden blocking I/O)
- Structural issues or error-prone patterns in the source
- Durations that suggest correctness or performance problems

Rate: **good**, **fair**, or **poor**, with specific observations.

### 3. Correct usage

Is the caller or agent invoking this transaction correctly? Look for:
- Wrong or missing parameters
- Edge cases the caller does not handle
- The transaction being used in a way the implementation does not intend
- Sequences of calls that could be collapsed into a single operation

Rate: **yes**, **partial**, or **no**, with observations.

### 4. Skill improvement opportunities

Are there changes to the Claude Code skills installed in the target project
(under `/workspace/target/.claude/`) that would help an agent using this
transaction more effectively? Consider:
- Missing context that would clarify when or how to use this transaction
- Misleading descriptions that could cause an agent to misuse it
- Undocumented parameters, return shapes, or error conditions
- Example invocations that are absent or out of date

List specific suggested changes, or state "none identified".

### Sub-agent return shape

Each sub-agent returns a structured finding:

```
transaction: <operation name>
instrumentation: <good|partial|poor> — <key finding>
code_quality: <good|fair|poor> — <key finding>
correct_usage: <yes|partial|no> — <key finding>
skill_improvements:
  - <suggestion 1>
  - <suggestion 2>
  (or "none identified")
```

---

## Phase 3 — Synthesize Report

After all sub-agents complete, produce a final report in this format:

---

### Assessment Report

**Scope:** _(restate `$ARGUMENTS`)_
**Transactions assessed:** N
**Time of assessment:** _(current time)_

---

One block per transaction:

```
## <Transaction Name>

**Instrumentation:** <rating> — <key finding>
**Code quality:**    <rating> — <key finding>
**Correct usage:**   <yes/no/partial> — <key finding>
**Skill improvements:** <count> suggestion(s)
  - <suggestion>
```

---

**Overall findings:**

- Any transactions with poor or partial instrumentation (actionable gaps listed)
- Code quality or correctness issues worth addressing
- All skill improvement suggestions consolidated across all transactions

---

## Notes

- Phase 1 uses no LLM budget — pure telemetry queries via rounds adapter interfaces
- Phase 2 uses diagnosis budget — one sub-agent per distinct transaction type
- Obtain a representative `get-trace-tree` for each transaction before launching its
  sub-agent so the agent has the full span hierarchy in context
- Read source files from `/workspace/target` to support code quality and
  instrumentation analysis
- Read skills from `/workspace/target/.claude/skills/` for skill improvement analysis
