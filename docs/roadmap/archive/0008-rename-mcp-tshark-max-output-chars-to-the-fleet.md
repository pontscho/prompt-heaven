---
name: 0008-rename-mcp-tshark-max-output-chars-to-the-fleet
type: roadmap-item
status: active
title: R-0008 · Rename mcp-tshark max_output_chars to the fleet parameter spelling
description: Closed roadmap item R-0008 (done 2026-09-29).
id: R-0008
state: done
horizon: now
origin: docs/adr/0013-the-ceiling-is-a-payload-class.md#what-a-class-does-not-license
blocked_by: []
tags: [mcp, scripts]
closed: 2026-09-29
commit: 69a29fa1b87298c7c7f7f7a9951cfc9e9b6d9eea
---

# R-0008 · Rename mcp-tshark max_output_chars to the fleet parameter spelling

## Why

mcp-tshark is the only server with this divergent parameter name. Renaming it is a caller-visible change, so it was left as an open thread.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->later: first triage 2026-09-28
- 2026-09-29 later->now: User 2026-09-29: small, run in parallel.
- 2026-09-29 now->now [idea->active]: User 2026-09-29: small, run in parallel.
- 2026-09-29 now->done: commit 69a29fa1b87298c7c7f7f7a9951cfc9e9b6d9eea
