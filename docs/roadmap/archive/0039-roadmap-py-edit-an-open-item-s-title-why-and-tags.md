---
name: 0039-roadmap-py-edit-an-open-item-s-title-why-and-tags
type: roadmap-item
status: active
title: R-0039 · roadmap.py: edit an open item's title, why and tags after add
description: Closed roadmap item R-0039 (done 2026-09-29).
id: R-0039
state: done
horizon: now
origin: user:2026-09-29:r0014-item-edit-split
blocked_by: []
tags: [roadmap]
closed: 2026-09-29
commit: d17e4cd8799723c8503857e4bb867a8c194e88be
---

# R-0039 · roadmap.py: edit an open item's title, why and tags after add

## Why

Split out of R-0014, which keeps the producer hooks and the kanban board. roadmap.py has no command that changes an open item's title, why or tags after add, so a wrong word stays until the item is archived and then becomes immutable. Found the hard way on 2026-09-29: a placeholder wikilink written into R-0037's why could not be corrected and gated until roadmap pages became frozen records.

## Log

- 2026-09-29 new->now: added
- 2026-09-29 now->now [idea->active]: User 2026-09-29: go.
- 2026-09-29 now->done: commit d17e4cd8799723c8503857e4bb867a8c194e88be
