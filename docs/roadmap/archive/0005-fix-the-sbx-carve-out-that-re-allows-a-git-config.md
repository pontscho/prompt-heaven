---
name: 0005-fix-the-sbx-carve-out-that-re-allows-a-git-config
type: roadmap-item
status: active
title: R-0005 · Fix the sbx carve-out that re-allows a .git/config under it on copy-deployed installs
description: Closed roadmap item R-0005 (done 2026-09-28).
id: R-0005
state: done
horizon: now
origin: docs/adr/0007-a-path-spelled-deny-protects-the-spelling.md#consequences
blocked_by: []
tags: [sandbox, security]
closed: 2026-09-28
commit: 67772eccc7358643acb6fe8a72af46fec89c9cd4
---

# R-0005 · Fix the sbx carve-out that re-allows a .git/config under it on copy-deployed installs

## Why

The carve-out's subtree re-allow comes after the per-file .git/config masks, so a .git/config whose realpath falls under a carve-out loses its deny on a copy-deployed install. Recorded as a defect in ADR 0007; not yet confirmed at runtime.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->now: first triage 2026-09-28
- 2026-09-28 now->now [idea->active]: work started in session S028 (parallel now-lane execution)
- 2026-09-28 now->done: commit 67772eccc7358643acb6fe8a72af46fec89c9cd4
