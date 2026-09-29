---
name: 0042-a-read-only-kanban-board-over-roadmap-export-2
type: roadmap-item
status: active
title: R-0042 · A read-only kanban board over roadmap-export/2
description: Closed roadmap item R-0042 (done 2026-09-29).
id: R-0042
state: done
horizon: now
origin: user:2026-09-29:r0014-kanban-board
blocked_by: []
tags: [roadmap]
closed: 2026-09-29
commit: fd47ca413ce92cff8f970865b788992d1c25fcc0
---

# R-0042 · A read-only kanban board over roadmap-export/2

## Why

Split from R-0014. ADR 0022 shipped only the export contract. A board consumes roadmap-export/2 read-only: columns by horizon, state as a card badge, reverse blocks edges computed by the consumer, why escaped by the consumer. Form is undecided (static HTML from a stdlib script, which needs the staging path pattern widened, or a terminal/markdown rendering, which falls under ADR 0016 cell escaping).

## Log

- 2026-09-29 new->unset: User 2026-09-29: split from R-0014.
- 2026-09-29 unset->now [idea->active]: User 2026-09-29: go, four parallel builders.
- 2026-09-29 now->done: commit fd47ca413ce92cff8f970865b788992d1c25fcc0
