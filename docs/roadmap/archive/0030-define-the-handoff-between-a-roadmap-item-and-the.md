---
name: 0030-define-the-handoff-between-a-roadmap-item-and-the
type: roadmap-item
status: active
title: R-0030 · Define the handoff between a roadmap item and the one requirements.yaml plan
description: Closed roadmap item R-0030 (done 2026-09-29).
id: R-0030
state: done
horizon: now
origin: user:2026-09-28:roadmap-plan-handoff
blocked_by: []
tags: [planning, roadmap]
closed: 2026-09-29
commit: 8786ee81238fb6a2dfc94434f79eff4272af1471
---

# R-0030 · Define the handoff between a roadmap item and the one requirements.yaml plan

## Why

requirements.yaml holds one plan at a time while the now lane holds up to three active items, so task-planning a second active item overwrites the first one's plan. Proposal: an optional roadmap_item field in requirements.yaml as the only link, the roadmap never mirrors task status, p:implement closes the item on its green end, and at most one active item goes through task-plan at a time while small items skip planning.

## Log

- 2026-09-28 new->unset: raised in S028 while executing the now lane
- 2026-09-29 unset->next: scope extended (user-approved S028): define state planned as 'has a plan' -- a spec: page or a requirements.yaml whose roadmap_item names it; at most one planned item owns the single plan slot; small items still go idea->active via start
- 2026-09-29 next->now [idea->active]: started
- 2026-09-29 now->done: commit 8786ee81238fb6a2dfc94434f79eff4272af1471
