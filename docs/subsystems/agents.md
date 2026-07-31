---
name: agents
type: subsystem
status: active
title: Minion Agents
description: Delegate-able sub-agents invoked via the Task tool to keep the main context clean.
sources:
  - ClaudeCode/agents
verified:
  commit: d6659f7
  date: 2026-07-16
links:
  - overview
  - skills
---

# Minion Agents

The "minions" under `ClaudeCode/agents/` are delegate-able sub-agents invoked
via the Task tool. Each is a markdown file defining role, behavior, constraints,
and MCP tool routing. They iterate in their own sandboxes and return clean
reports, keeping the main context free of build/search/iteration noise — the
core principle stated in `ClaudeCode/CLAUDE.md`.

## Roster

| Agent | Role |
|-------|------|
| `minion-explorer.md` | Read-only multi-round codebase reconnaissance |
| `minion-builder.md` | Build + test + fix cycles (cmake/make/forge) |
| `minion-runner.md` | Script/command run-fix-retry loops |
| `minion-watson.md` | Non-obvious bug/failure investigation via clangd/luals |
| `minion-inspector-plan.md` | Validate a plan against the live codebase before coding |
| `minion-inspector-implementation.md` | Audit a completed implementation against the plan |
| `minion-inspector-security-officer.md` | OWASP/CWE security review (plan- and code-mode) |
| `minion-web-explorer.md` | Single-shot external/web lookups |
| `minion-deep-researcher.md` | Comprehensive web research (10-15 parallel queries) |
| `minion-mason.md` | Per-task build executor (LSP + forge) — the engine behind `p:implement` |
| `minion-feature-planner.md` | Authoritative feature implementation-plan writer — LSP-verified plan against the live codebase (Vitruvius) |
| `minion-task-planner.md` | Task-plan writer — emits `requirements.yaml` with function-level tasks + dependency graph (Gantt) |
| `minion-code-reviewer.md` | Single-lens code-review finder — surfaces candidate findings through one lens (Statler) |
| `minion-code-verifier.md` | Single-candidate code-review verifier — CONFIRMED/PLAUSIBLE/REFUTED on a fresh read (Waldorf) |
| `minion-librarian.md` | Executor for the `p:wiki` skill — ingest/lint/query/init/adopt against the `docs/` wiki (Dewey) |

## When each is used

The delegation heuristic lives in `ClaudeCode/CLAUDE.md`: build/test ->
`minion-builder`; >~3 read/search calls on one topic -> `minion-explorer`;
non-obvious failure -> `minion-watson`; plan written -> `minion-inspector-plan`
then `minion-inspector-security-officer`; implementation finished ->
`minion-inspector-implementation` then `minion-inspector-security-officer`. The
planning chain is written by `minion-feature-planner` (implementation plan) then
`minion-task-planner` (`requirements.yaml`); the `p:code-review` / `p:branch-review`
pipeline fans out `minion-code-reviewer` (one per lens) into `minion-code-verifier`
(one per candidate); and `minion-librarian` maintains the `docs/` wiki. Skills that
author these files are covered in [[skills]] (`p:writer-agent`).
