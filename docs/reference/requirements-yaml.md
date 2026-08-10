---
name: requirements-yaml
type: reference
status: active
title: requirements.yaml — the machine-checkable half of the plan
description: Why the plan-to-implement handoff splits into a prose plan and a validated YAML task graph, and what each load-bearing choice in the YAML buys.
sources:
  - requirements.yaml
  - Scripts/task-validator.py
verified:
  commit: 9eeb66c
  date: 2026-08-10
links:
  - scripts
  - skills
  - feature-implementation-plan
  - tests
  - 0002-index-claims-no-freshness
---

# requirements.yaml

`requirements.yaml` is the second of the two artifacts in the feature pipeline:
`/p:feature-plan` writes a prose plan, `/p:task-plan` turns it into this file, and
`/p:implement` consumes it `ClaudeCode/skills/_lib/handoff-contracts.md`. This page
carries *why* the handoff splits in two and what each load-bearing choice buys.

The field-by-field schema is deliberately **not** repeated here: the authoring
contract is `ClaudeCode/skills/task-plan/SKILL.md`, the enforced version is
`Scripts/task-validator.py`, and the read/update commands are the `p:requirements`
skill. See [[skills]] and [[scripts]] for those.

## Why a second artifact at all

The plan and the YAML have different readers. `docs/feature-implementation-plan.md`
is prose — judged by a validation loop and read by a human ([[feature-implementation-plan]]).
The YAML is read and *rewritten* by programs: `Scripts/task-update.py` flips a
task's status in place while preserving the file's formatting, `Scripts/task-plan.py`
derives execution levels and detects file conflicts between tasks, and the validator
runs a Kahn topological sort over the dependency graph
`Scripts/task-validator.py:_check_cycles`. None of the three can operate on prose.

Why the *validator* exists is recorded, identically in two places: the file is the
sole input to `/p:implement`, so a dangling dependency, a dependency cycle, a wrong
effort breakdown or a bad enum "is otherwise only discovered mid-implementation,
expensively" `Scripts/task-validator.py` `ClaudeCode/skills/task-plan/SKILL.md`. It
is the gate before `complete: true`.

The choice of YAML *specifically*, rather than extending the markdown plan, is
**not recorded** — only its consequences are.

## The one script in the family that takes a dependency

The `task-*.py` utilities are deliberately regex-based with no YAML library. The
validator breaks that rule, and the exception is argued rather than assumed: a
validator "must check YAML well-formedness itself, which regex cannot"
`Scripts/task-validator.py`, so PyYAML is imported behind a guard that exits 2 with
an install hint. The rejected alternative — validating with the same regexes as its
siblings — cannot detect the one failure class the validator exists for.

## Function-level granularity

A task must be function-level, "not file-level or line-level"
`ClaudeCode/skills/task-plan/SKILL.md`. The rule is stated; a rationale for the
*choice* is **not recorded**. What it buys is visible in the tooling: because every
task names its target file and its references, the batch planner unions those into a
per-task file set and refuses to run two tasks touching the same file concurrently
`Scripts/task-plan.py:Task`. File-level tasks would make that conflict check
vacuous; line-level tasks would make it unstable.

## Sizing is aggregated, never summed

Effort is T-shirt sized. Why that scale rather than hours is **not recorded**. What
is recorded is the aggregation rule, and it is deliberately not addition:
`total_effort` is the *largest* single task size, bumped one level at 4-7 tasks and
two at 8-12 — and at 13 or more the instruction is to split the plan rather than
emit a bigger number `ClaudeCode/skills/task-plan/SKILL.md`. The scale tops out on
purpose, so an estimate that would overflow it is treated as a planning error rather
than reported as a large number.

The validator then checks the breakdown against the actual task counts and warns on
drift `Scripts/task-validator.py`, making the aggregate a checked number rather than
a typed one — the same discipline [[tests]] records for the test fleet's case counts.

## Why `code_references` carry excerpts, not paths

This is the most explicitly argued field in the schema, marked **CRITICAL** in two
separate places: a reference carries a 10-30 line excerpt of the actual code, and the
stated reason is that it "eliminates re-reading during implementation"
`ClaudeCode/skills/task-plan/SKILL.md`. The same preference is written into the
consuming end — `/p:implement` is told to prefer the YAML's captured context over
re-reading the reference files `ClaudeCode/skills/_lib/handoff-contracts.md`.

It is a context-budget decision, and the same one the minion doctrine makes
everywhere else: pay the read once, during planning, and hand the result forward. A
bare path makes every implementing agent pay it again.

## What `implementation_complete` gates

`complete:` and `implementation_complete:` are different flags on different phases,
and only the first is known to the validator. `complete:` marks task-planning
finished, and the validator is phase-aware on it: `complete: false` requires only
requirements, constraints and success criteria, while `complete: true` additionally
requires the full implementation plan and captured context `Scripts/task-validator.py`.

`implementation_complete: true` marks shipped code, and it is a gate rather than a
note. It may be written only after the post-implementation validation fan-out clears
*both* lanes, with the security lane "required clean" before it may be set
`ClaudeCode/skills/implement/SKILL.md`. The round-5 escape hatch offers halting
**without** writing it as an explicit option, and the accept-and-proceed option
records the residue in the open-item lists instead
`ClaudeCode/skills/implement/SKILL.md`. So the flag asserts that two independent
reviewers cleared the work — not that its author believes it is done.

## The validator's vocabulary stops at that gate

None of the keys `/p:implement` writes *after* the gate appear in the validator's
top-level allowlist `Scripts/task-validator.py:TOP_LEVEL_KEYS`, and an unrecognised
top-level key draws an "unknown top-level key (typo?)" warning. Measured against the
file at this commit: of its 19 top-level keys, 8 are in the allowlist and **11 are
not** — every implementation-record and documentation-record field. Re-running the
validator on a post-implementation file therefore emits eleven warnings, and
`--strict` turns them into a non-zero exit.

That is a consequence of the two-phase design rather than a decision about it: no
rationale is recorded for leaving the implementation record outside the validator's
vocabulary.

## It is also the post-implementation record

The wiki sync that closes `/p:implement` writes back what the documentation pass
touched and what it left open `ClaudeCode/skills/_lib/handoff-contracts.md`, so the
file is the input to the *next* wiki pass as well as the output of the last one.
Those open items are real work rather than bookkeeping: the set recorded at this
commit included a note that three pages carried a placeholder `verified.commit`
predating the code they described `requirements.yaml` — exactly the false-freshness
claim [[0002-index-claims-no-freshness]] exists to prevent. It has since been closed.
