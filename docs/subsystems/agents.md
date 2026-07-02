---
name: agents
type: subsystem
status: current
title: Minion Agents
description: Delegate-able sub-agents invoked via the Task tool to keep the main context clean.
sources:
  - ClaudeCode/agents/p
verified:
  commit: 51dd5f3
  date: 2026-05-27
links:
  - overview
  - skills
---

# Minion Agents

The "minions" under `ClaudeCode/agents/p/` are delegate-able sub-agents invoked
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
| `minion-mason.md` | Per-task build executor (LSP + forge) — the engine behind `/p:implement` |

## When each is used

The delegation heuristic lives in `ClaudeCode/CLAUDE.md`: build/test ->
`minion-builder`; >~3 read/search calls on one topic -> `minion-explorer`;
non-obvious failure -> `minion-watson`; plan written -> `minion-inspector-plan`
then `minion-inspector-security-officer`; implementation finished ->
`minion-inspector-implementation` then `minion-inspector-security-officer`. Skills that author these
files are covered in [[skills]] (`p:writer-agent`).
