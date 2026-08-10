---
name: agents
type: subsystem
status: active
title: Minion Agents
description: Delegate-able sub-agents invoked via the Task tool to keep the main context clean.
sources:
  - ClaudeCode/agents
verified:
  commit: bbcbede
  date: 2026-08-04
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

That routing is granted in each file's `tools:` list, and `tools:` is the only
place it can be granted: the four web-facing minions — `minion-web-explorer`,
`minion-deep-researcher`, `minion-watson` and
`minion-inspector-security-officer` — each list
`mcp__mcp-webfetch__webfetch_call` there and keep the built-in `WebFetch` as an
explicit fallback, because the impersonating dispatcher gets through the
Cloudflare/Akamai-style bot detection the built-in cannot (see [[scripts]])
`ClaudeCode/agents/minion-web-explorer.md`. **An `mcpServers:` key grants
nothing** — a plugin agent's frontmatter ignores it at load time, so it reads as
a declared capability while conferring none; no file here has one, and the agent
suite treats writing one as a defect. And `webfetch` is not a tool name: the
dispatcher is `webfetch_call`, routed by `function="fetch"` — the only function
this fleet's prose may name, because it is the one the server advertises when it
refuses an unknown one.

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
| `minion-bug-hunter.md` | Autonomous bug-closing executor — symptom in, reproduced/diagnosed/fixed/proven out (Quint) |

## When each is used

The delegation heuristic lives in `ClaudeCode/CLAUDE.md`: build/test ->
`minion-builder`; >~3 read/search calls on one topic -> `minion-explorer`;
non-obvious failure -> `minion-watson`; plan written -> `minion-inspector-plan`
then `minion-inspector-security-officer`; implementation finished ->
`minion-inspector-implementation` then `minion-inspector-security-officer`. The
planning chain is written by `minion-feature-planner` (implementation plan) then
`minion-task-planner` (`requirements.yaml`); the `p:code-review` / `p:branch-review`
pipeline fans out `minion-code-reviewer` (one per lens) into `minion-code-verifier`
(one per candidate); and `minion-librarian` maintains the `docs/` wiki.

A *reported* bug — one with a symptom attached — goes to `minion-bug-hunter`, the
fleet's second executor `ClaudeCode/ARCHITECTURE.md`, where `minion-watson`
diagnoses and stops. It reproduces the symptom, employs `minion-watson` for the
root cause rather than re-deriving it, and fixes only a *contained* change: no
public API or signature change, no new dependency, no schema, protocol, or
on-disk-format change, no module-boundary refactor, no data mutation
`ClaudeCode/agents/minion-bug-hunter.md`. Anything past that boundary is reported
as a diagnosis with no code touched. Its evidence gate is a reproduction that
fails before the fix and passes after — promoted into the project's test tree as
a regression test; when no reproduction can be built, `minion-code-verifier` must
judge the root-cause claim before any edit, and no-reproduction plus
not-contained stops the run outright.

Skills that author these files are covered in [[skills]] (`p:writer-agent`).
