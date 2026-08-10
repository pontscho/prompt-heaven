---
name: 0006-the-second-executor
type: adr
status: active
title: The second executor — who closes the loop on a reported bug
description: Decision to add a second executor minion (Quint) that carries a reported bug end-to-end, gated by a shape-based containment rule and a reproduce-or-verify evidence rule, plus the six designs rejected on the way.
sources:
  - ClaudeCode/agents/minion-bug-hunter.md
  - ClaudeCode/ARCHITECTURE.md
links:
  - agents
  - layer-contract
---

# ADR 0006: The second executor — who closes the loop on a reported bug

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is `ClaudeCode/agents/minion-bug-hunter.md`, the [[agents]]
roster, and the [[layer-contract]] executor tier.

## Context

The fleet had fifteen minions and one structural gap: **nothing closed the loop
on a reported bug.**

Each incumbent stops one step short, and each stops there for a good reason:

- `p:minion-watson` traces root cause through source and **stops** — read-only by
  contract `ClaudeCode/agents/minion-watson.md`, and that contract is load-bearing
  (see Consequences).
- `p:minion-code-reviewer` surfaces candidates through ONE lens over a scope
  **handed to it**; without a `LENS:` directive it returns an empty list rather
  than guessing `ClaudeCode/agents/minion-code-reviewer.md`.
- `p:minion-code-verifier` judges ONE already-formed candidate; it cannot start
  from a target `ClaudeCode/agents/minion-code-verifier.md`.
- `p:minion-mason` implements — but only from a written task spec, which is where
  its acceptance criterion comes from `ClaudeCode/agents/minion-mason.md`.
- `p:minion-builder` fixes what the compiler says `ClaudeCode/agents/minion-builder.md`.
  A bug the compiler is content with is outside its signal.

So "here is a symptom, make it go away *correctly*" was a fully manual path: a
human read Watson's finding and typed the fix.

The first design aimed at a different gap — a *symptomless* hunter for the bug
class that never produces a log line, and therefore can never be the input to
Watson. That design was abandoned (Option 2). The boundary that actually
justifies a new agent turned out not to be the investigative angle but **who
manufactures the acceptance criterion**: mason is *given* one by the plan; a bug
fixer has to build one.

## Decision

Add ONE new executor minion — `p:minion-bug-hunter`, human name **Quint** —
that takes a symptom and carries it end-to-end in a single invocation:
reproduce, diagnose, decide, fix, prove.

**It employs rather than duplicates.** Four leaf children, all of which already
exist and all of which stay within the depth-2 ceiling: `p:minion-watson` (root
cause — the happy path, not an escape hatch), `p:minion-explorer` (recon),
`p:minion-runner` (noisy or iterative reproduction runs), `p:minion-code-verifier`
(the evidence gate). Quint does not re-derive the diagnosis; Watson is better at
it, and re-implementing him inside a second agent would have been the largest
duplication in the design.

**Two gates, both evaluated after the root cause and before the first edit:**

- **Gate A — containment, judged on SHAPE not size.** A fix is contained only if
  it changes no public API or exported signature, introduces no dependency,
  changes no schema / wire protocol / on-disk format, moves no module boundary,
  and mutates no data. Outside that: report the diagnosis, change nothing, stop.
- **Gate B — evidence.** A reproduction that fails before the fix is the
  evidence. When none can be built, `p:minion-code-verifier` must judge the
  root-cause claim first, and the report must say the fix landed without a
  reproduction.
- **Compound rule: no reproduction AND not contained → stop, unconditionally.**
  Either uncertainty alone is manageable; together they are how an autonomous
  agent does damage.

**A successful reproduction is promoted into the project's test tree** as a
regression test, in the project's own convention.

**`mcp__mcp-lldb__lldb_call` is granted here and nowhere else in the fleet**
`ClaudeCode/agents/minion-bug-hunter.md`, because the debugger *runs and controls
a process* — a capability a read-only leaf worker may not hold, and which
therefore belongs to an executor.

## Alternatives Evaluated

### Option 1 — A ninth lens in the code-review catalogue
- **Pros:** `ClaudeCode/skills/_lib/code-review-lenses.md` states outright that a
  capability expressible as a lens MUST be a lens, not a new agent. No new agent,
  and `/p:code-review` already accepts a directory, so no plumbing is missing.
- **Cons:** every one of the eight lens texts is diff-anchored — "the changed
  hunk", "the change DELETES", "new code", "complexity the change adds" — and is
  meaningless without a diff. More decisively, a lens is *handed* its scope, and
  the entire problem is that nobody has decided what to look at.

### Option 2 — A symptomless bug hunter (the first design)
- **Pros:** targets the one class Watson can never be invoked for — bugs that
  produce no log line, no crash, no failing test: fallback-to-default, swallowed
  exception, a sentinel nobody checks.
- **Cons:** the boundary justifying it was thin ("who scopes?"), and the use case
  is already served by `/p:code-review <dir>` with the eight lenses; an
  angle-per-invocation fan-out is literally `p:minion-code-reviewer`. Worse, it
  would have *found* bugs and handed them back for a human to fix — leaving the
  loop open, which is the actual gap. Rejected in favour of a coarser and far
  more valuable boundary: who closes the loop.

### Option 3 — Give Watson write access
- **Pros:** no new agent at all; the fleet's diagnostic engine simply finishes
  the job it already starts.
- **Cons:** destroys a clean, load-bearing boundary. Watson's read-only contract
  is precisely what lets `p:minion-mason` use him as a bounded escape hatch. And
  an agent that diagnoses, fixes, and then declares itself correct violates the
  fleet's deepest pattern — the finder never judges itself (Statler/Waldorf;
  find/verify in the security pipeline).

### Option 4 — A `/p:fix` skill that orchestrates the chain
- **Pros:** this is what `ClaudeCode/ARCHITECTURE.md` prescribes for a job with
  multiple steps calling several agents, and the bail-out decision would happen
  in main context where a human can see it.
- **Cons:** `/p:implement` is a skill because MANY tasks run in dependency order —
  genuine multi-round orchestration. One bug is one chain; the skill would be a
  wrapper around a single `Agent` call, and its fan-out would consume the main
  context the minion doctrine exists to protect. An executor carrying one unit of
  work and offloading sub-tasks within it is the established shape (mason), not a
  new exception.

### Option 5 — A mandatory reproduction gate
- **Pros:** the strongest available answer to symptom suppression — "the symptom
  is gone" stops being an acceptable signal, with no escape hatch.
- **Cons:** it excludes exactly the bugs nobody closes today: non-deterministic,
  timing-dependent, environment-dependent. Rejected in favour of
  reproduce-or-verified-claim, with the compound rule covering the dangerous
  corner where both safeguards are absent at once.

### Option 6 — A file-count cap on blast radius
- **Pros:** trivially checkable, and a test could measure it directly.
- **Cons:** the wrong proxy. A six-file mechanical correction is harmless; a
  one-file signature change is not. A count stops the safe case and admits the
  dangerous one. Shape is what actually distinguishes them.

### Chosen — one executor, two gates, four employees
The gap is the open loop, not the missing investigative angle. Quint closes it
while re-using every specialist that already exists, and both gates sit before
the first edit rather than after it, because an autonomous fixer's failures are
cheap to prevent and expensive to unwind.

## Consequences

- **The executor tier is now plural.** `ClaudeCode/ARCHITECTURE.md` previously
  read "currently ONLY `p:minion-mason`" in two places; both now name two
  executors with their per-executor child allowlists. A clarifying clause was
  added at the same time: being an executor is **not** a licence to orchestrate —
  it carries ONE unit of work and offloads sub-tasks within it. The moment a job
  spans several units or needs an interactive decision, it is a workflow and
  belongs in a skill.

- **Mason cannot call Quint.** The anti-pattern list forbids an executor spawning
  a non-leaf, and Quint is an executor. So when a task fails under `/p:implement`,
  Dave still gets only Watson. The *skill* may call Quint — skill→executor is
  legal — and that is the intended bridge, deliberately not built yet.

- **`lldb_call` stopped being an orphan capability.** Before this agent existed,
  the name-existence suite's rule 3c reported `lldb_call` as registered,
  reachable, and granted by NO agent's `tools:` list `tests/test_name_existence.py`.
  Quint is now its only grantee, and that INFO became a PASS. The fleet had a
  debugger it could not use.

- **The database constraint is prompt-enforced, not tool-enforced.**
  `postgres_call` permits arbitrary SQL and states it has no read-only
  restriction `Scripts/mcp-postgres.py`; nothing mechanical stops a write. This is
  a known soft spot, and
  it is why Gate A names data mutation as a containment boundary explicitly
  rather than leaving it implied by "no schema change".

- **Symptom suppression is mitigated structurally, not eliminated.** The gates
  raise its cost; they cannot make it impossible. The named prohibition list —
  swallowed catch, widened tolerance, added retry, weakened assertion,
  special-cased input, guard at the observation point instead of the origin — is
  the second layer, and the one-sentence test ("why is this the cause and not the
  symptom?") is the third.

- **The test suite will grow with agent-authored tests.** Accepted, because a
  promoted reproduction clears a bar most hand-written tests never do: it was
  *observed to fail* before the fix and pass after. The mitigation against noise
  is that the reproduction must follow the project's existing test convention,
  or it stays under `.claude/tmp/` and the report says so.

- **The symptomless hunt gained nothing.** It remains `/p:code-review <dir>` with
  the eight lenses. Option 2 is recorded here so the idea is not re-derived from
  scratch next time it looks attractive.
