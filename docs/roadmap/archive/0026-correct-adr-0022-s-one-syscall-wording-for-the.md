---
name: 0026-correct-adr-0022-s-one-syscall-wording-for-the
type: roadmap-item
status: active
title: R-0026 · Correct ADR 0022's one-syscall wording for the containment window
description: Closed roadmap item R-0026 (done 2026-09-28).
id: R-0026
state: done
horizon: now
origin: user:2026-09-28:adr-0022-window-wording
blocked_by: []
tags: [roadmap, wiki]
closed: 2026-09-28
commit: 2dccd0b862f344ca15aaf260838290448172bf04
---

# R-0026 · Correct ADR 0022's one-syscall wording for the containment window

## Why

The ADR files the symlink-swap-after-check race under the one-syscall lock window, but the containment check runs once in main and the window spans parsing, git spawns and the wiki scan up to the write. The limit stands; the wording overclaims.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->next: first triage 2026-09-28
- 2026-09-28 next->now [idea->active]: started
- 2026-09-28 now->done: commit 2dccd0b862f344ca15aaf260838290448172bf04
