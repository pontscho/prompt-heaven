---
name: 0009-give-mcp-forge-and-mcp-gdc-a-declared-reply
type: roadmap-item
status: active
title: R-0009 · Give mcp-forge and mcp-gdc a declared reply ceiling
description: Closed roadmap item R-0009 (done 2026-09-28).
id: R-0009
state: done
horizon: now
origin: docs/adr/0013-the-ceiling-is-a-payload-class.md#what-this-page-does-not-settle
blocked_by: []
tags: [mcp, scripts]
closed: 2026-09-28
commit: cf216621c87f74630132c1d84c0acd252ef334de
---

# R-0009 · Give mcp-forge and mcp-gdc a declared reply ceiling

## Why

These two registered servers have no reply-ceiling constant, so neither the payload-class model nor its gate covers them; an earlier commit's claim that no server is uncapped is false.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->next: first triage 2026-09-28
- 2026-09-28 next->now [idea->active]: started
- 2026-09-28 now->done: commit cf216621c87f74630132c1d84c0acd252ef334de
