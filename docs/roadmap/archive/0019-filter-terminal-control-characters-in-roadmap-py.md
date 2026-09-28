---
name: 0019-filter-terminal-control-characters-in-roadmap-py
type: roadmap-item
status: active
title: R-0019 · Filter terminal control characters in roadmap.py input and output
description: Closed roadmap item R-0019 (done 2026-09-28).
id: R-0019
state: done
horizon: now
origin: user:2026-09-28:roadmap-control-chars
blocked_by: []
severity: low
tags: [roadmap, security]
closed: 2026-09-28
commit: 1178cbdce0657bdb18d050cc0770fee9196672fa
---

# R-0019 · Filter terminal control characters in roadmap.py input and output

## Why

Security review 2026-09-28 (F3/F4/F14, all LOW): the strict reader never re-applies the writer's character rule, so ESC/C1/bidi in a committed roadmap.md or archive page reach the terminal through list/show/export; check_line and check_why reject only C0, so DEL, C1, bidi overrides and zero-width characters pass on write; die() formats filesystem names with %s.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->next: first triage 2026-09-28
- 2026-09-28 next->now [idea->active]: started
- 2026-09-28 now->done: commit 1178cbdce0657bdb18d050cc0770fee9196672fa
