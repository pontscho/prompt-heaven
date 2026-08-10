---
name: layer-contract
type: concept
status: draft
title: The ClaudeCode layer contract
description: Why skills, agents and fragments are three non-overlapping layers, what an executor minion is, and why sub-agent nesting is capped below what the harness can actually do.
sources:
  - ClaudeCode/ARCHITECTURE.md
links:
  - agents
  - skills
  - 0006-the-second-executor
---

# The ClaudeCode layer contract

`ClaudeCode/ARCHITECTURE.md` is the canonical rulebook for how skills, agents and
fragments fit together. This page carries the *why* behind it — the failure each
rule prevents — and exists so the rulebook is finally covered by a page that
freshness-tracks it. The rulebook itself remains authoritative for the exact
wording; when the two disagree, the rulebook wins and this page is stale.

## Three layers, and why the boundary is drawn there

A **skill** is an active orchestrator running in the invoking context; an
**agent** (minion) is a single-purpose worker in an isolated sub-agent context; a
**fragment** under `ClaudeCode/skills/_lib/` is passive prose that is not
user-callable `ClaudeCode/ARCHITECTURE.md`.

The split is about *where context is spent*. A skill's fan-out is visible but
costs the main conversation; a minion's work is invisible but free. So the
selection rule is not aesthetic: a *workflow* (several steps, several agents,
possibly interactive) belongs in a skill, a *single worker job* belongs in an
agent, and prose repeated across two or more skills belongs in a fragment
`ClaudeCode/ARCHITECTURE.md`. The `_lib/` directory is reserved and must not hold
a user-callable `SKILL.md`.

## The executor tier, and a ceiling set below the ability

Sub-agent nesting was long treated as unsupported. It is now measured working in
this harness to **three or more levels** — including a custom minion holding an
`Agent` grant spawning a leaf child (verified 2026-07) — and the project caps it
at two anyway `ClaudeCode/ARCHITECTURE.md`. The cap is a deliberate policy, not a
technical limit, and it is the interesting part of the contract.

What the cap buys is the thing the original no-nesting rule guarded: cost and
context-opacity. A chain that can nest without bound can also spend without
bound, and every level down is a level whose reasoning nobody in the main
conversation ever sees.

An **executor minion** is a worker that carries ONE unit of production work
end-to-end and may spawn leaf workers to offload token-heavy sub-tasks
`ClaudeCode/ARCHITECTURE.md`. There are exactly two, and their unit is what
distinguishes them:

| Executor | Its unit of work | Where its acceptance criterion comes from |
|---|---|---|
| `p:minion-mason` | one planned task | **given** — the task spec in `requirements.yaml` |
| `p:minion-bug-hunter` | one reported bug | **manufactured** — it must build its own reproduction |

That second column is the whole justification for the second executor; the
reasoning and the six rejected designs are frozen in
[[0006-the-second-executor]].

Each executor declares a **fixed child allowlist** in its own file, and every
child must be a leaf, so the chain is `main/skill → executor → leaf → (stop)`
`ClaudeCode/ARCHITECTURE.md`. Two consequences follow that are easy to trip over:

- **Everything that is not a designated executor is a leaf** — planners,
  inspectors, reviewers, verifiers, builder, runner, explorer, watson. A leaf
  must not list `Agent` in its `tools:`.
- **An executor may not spawn another executor.** So `p:minion-mason` cannot hand
  a failing task to `p:minion-bug-hunter`; only a skill or the main context can
  reach an executor.

And being an executor is **not** a licence to orchestrate. It carries one unit
and offloads sub-tasks *within* it; the moment a job spans several units, needs
ordering between them, or needs an interactive decision, it is a workflow and
belongs in a skill `ClaudeCode/ARCHITECTURE.md`.

## Grants that confer nothing

The `tools:` list is the ONLY place a capability is granted, spelled
`mcp__<server>__<tool>`. Claude Code **ignores** `mcpServers:`, `hooks:` and
`permissionMode:` in a plugin-shipped agent's frontmatter, for security reasons —
so such a key reads exactly like a granted capability while conferring none,
which is worse than omitting it `ClaudeCode/ARCHITECTURE.md`. That is why the
name-existence suite treats writing one as a FAIL-level defect rather than a
style nit; see [[agents]] for how the corpus is checked.

`Skill` is permitted for any minion, leaf or executor, because it only loads
instructions into the *current* context and spawns nothing — it cannot breach the
nesting rule. Used sparingly.

## Step, Phase, and why the words are not interchangeable

**Step N** is a linear step inside a skill: no iteration, no loop. **Phase A / B / C**
is reserved *exclusively* for a validation loop — iterative reviewer invocation
up to five rounds with an escape hatch, defined once in
`ClaudeCode/skills/_lib/validation-loop.md` `ClaudeCode/ARCHITECTURE.md`. Writing
"Phase 2" in a skill that has no validation loop means Step 2, and the overload
is called out as an anti-pattern precisely because it once existed.

## What the contract forbids

The anti-pattern list is short and each entry names a failure that already
happened `ClaudeCode/ARCHITECTURE.md`: unbounded or lateral nesting; two skills
doing the same thing (pick one canonical implementation and have the other invoke
it via `Skill(...)`); mixed Phase semantics; inline duplication of the validation
loop instead of referencing the fragment; a cross-skill write with no entry in
`ClaudeCode/skills/_lib/handoff-contracts.md`; and ad-hoc in-band routing tokens
inside a minion — the security minion's `PHASE:` directive is an accepted
exception, and a new one requires an explicit decision rather than a per-minion
hack.

One consequence is worth writing down here, because it reads like a wiki-layout
defect and is not. **`docs/feature-implementation-plan.md` sits at the `docs/`
root rather than under `specs/` deliberately.** Its path is pinned by the
handoff contract itself `ClaudeCode/skills/_lib/handoff-contracts.md`, by the
default in `ClaudeCode/scripts/task-implementation-plan.py`, and by the four
skills and three agents of the plan→task→implement chain. A live interface
outranks the wiki's own layout rule, so the file stays put — the audit has been
run, and the move should not be proposed again.
