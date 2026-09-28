---
name: 0007-extend-the-forge-syntax-target-to-claudecode
type: roadmap-item
status: active
title: R-0007 · Extend the forge syntax target to ClaudeCode/skills
description: Closed roadmap item R-0007 (done 2026-09-28).
id: R-0007
state: done
horizon: now
origin: docs/adr/0009-the-first-reader-is-a-cold-model.md#consequences
blocked_by: []
tags: [tests]
closed: 2026-09-28
commit: 73d87ea8df95a2fb12c11fd08c5175d93eceffe0
---

# R-0007 · Extend the forge syntax target to ClaudeCode/skills

## Why

The syntax target compiles Scripts, tests and ClaudeCode/hooks only, so a syntax error in a skill script such as checkpoint.py or roadmap.py still passes a fully green fleet.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->now: first triage 2026-09-28
- 2026-09-28 now->now [idea->active]: work started in session S028 (parallel now-lane execution)
- 2026-09-28 now->done: commit 73d87ea8df95a2fb12c11fd08c5175d93eceffe0
