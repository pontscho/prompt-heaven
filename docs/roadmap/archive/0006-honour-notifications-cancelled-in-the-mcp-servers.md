---
name: 0006-honour-notifications-cancelled-in-the-mcp-servers
type: roadmap-item
status: active
title: R-0006 · Honour notifications/cancelled in the MCP servers
description: Closed roadmap item R-0006 (done 2026-09-29).
id: R-0006
state: done
horizon: now
origin: docs/adr/0008-a-serialized-read-loop-looks-like-a-dead-server.md#consequences
blocked_by: []
tags: [mcp, scripts]
closed: 2026-09-29
commit: 5621e6529941650ca194e32471bb913073ea0e78
---

# R-0006 · Honour notifications/cancelled in the MCP servers

## Why

A cancelled request keeps its worker slot until its own timeout expires; no server under Scripts/ handles notifications/cancelled.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->later: first triage 2026-09-28
- 2026-09-29 later->now [idea->active]: User 2026-09-29: go.
- 2026-09-29 now->done: commit 5621e6529941650ca194e32471bb913073ea0e78
