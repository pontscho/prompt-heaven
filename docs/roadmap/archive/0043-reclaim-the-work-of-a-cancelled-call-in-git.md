---
name: 0043-reclaim-the-work-of-a-cancelled-call-in-git
type: roadmap-item
status: active
title: R-0043 · Reclaim the work of a cancelled call in git, inspect, wiki and postgres
description: Closed roadmap item R-0043 (done 2026-09-29).
id: R-0043
state: done
horizon: now
origin: user:2026-09-29:r0006-remaining-reclaim
blocked_by: []
tags: [mcp, scripts]
closed: 2026-09-29
commit: fc48546753126eef60f30e3e265b22ef165c2c74
---

# R-0043 · Reclaim the work of a cancelled call in git, inspect, wiki and postgres

## Why

Split from R-0006, whose scope the user cut to reply suppression plus the cheap reclaims (forge and tshark kill the child, LSP servers send the cancel request). git, inspect and wiki run their children through subprocess.run, so a cancel stops only the reply until they move to a recorded Popen that the cancel path can kill; their clamped timeouts make the gain small. postgres would need a PostgreSQL CancelRequest on a second connection, because abandoning a query mid-exchange desyncs the wire (ADR 0008). jenkins, webfetch and context7 block in socket reads that cannot be interrupted and stay reply-only.

## Log

- 2026-09-29 new->unset: User 2026-09-29: split from R-0006.
- 2026-09-29 unset->now [idea->active]: User 2026-09-29: go, four parallel builders.
- 2026-09-29 now->done: commit fc48546753126eef60f30e3e265b22ef165c2c74
