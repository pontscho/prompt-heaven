---
name: 0002-remove-the-hand-vendored-duplication-between-the
type: roadmap-item
status: active
title: R-0002 · Remove the hand-vendored duplication between the wiki CLI scripts and mcp-wiki
description: Closed roadmap item R-0002 (done 2026-09-29).
id: R-0002
state: done
horizon: now
origin: docs/adr/0002-index-claims-no-freshness.md#consequences
blocked_by: []
tags: [wiki]
closed: 2026-09-29
commit: 43140aea3d262e67a799286ff94591e96cb25943
---

# R-0002 · Remove the hand-vendored duplication between the wiki CLI scripts and mcp-wiki

## Why

mcp-wiki.py copies the skill's reindex, freshness and frontmatter helpers by hand, so every change lands twice. The page-type constants now have a parity gate; DETAIL_STATUSES is still typed in both freshness.py and mcp-wiki.py.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->next: first triage 2026-09-28
- 2026-09-29 next->now [idea->active]: started
- 2026-09-29 now->done: commit 43140aea3d262e67a799286ff94591e96cb25943
