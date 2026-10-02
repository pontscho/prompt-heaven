---
name: 0004-let-the-implement-skill-route-a-failed-task-to
type: roadmap-item
status: active
title: R-0004 · Let the implement skill route a failed task to Quint
description: Closed roadmap item R-0004 (done 2026-10-02).
id: R-0004
state: done
horizon: next
origin: docs/adr/0006-the-second-executor.md#consequences
blocked_by: []
tags: [agents, skills]
closed: 2026-10-02
commit: 97e216e937a4888800a6d374c847323428eb6a8d
---

# R-0004 · Let the implement skill route a failed task to Quint

## Why

Under /p:implement a failing task still only reaches Watson, because Mason may not spawn another executor. The intended bridge is the skill calling Quint, deliberately left unbuilt.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->later: first triage 2026-09-28
- 2026-10-02 later->next: User 2026-10-02: next.
- 2026-10-02 next->done: commit 97e216e937a4888800a6d374c847323428eb6a8d
