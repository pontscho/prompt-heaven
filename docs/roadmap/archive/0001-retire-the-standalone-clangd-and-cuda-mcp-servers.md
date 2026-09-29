---
name: 0001-retire-the-standalone-clangd-and-cuda-mcp-servers
type: roadmap-item
status: active
title: R-0001 · Retire the standalone clangd and cuda MCP servers (purity Phase 2)
description: Closed roadmap item R-0001 (dropped 2026-09-29).
id: R-0001
state: dropped
horizon: later
origin: docs/specs/spec-purity-luals-phase1.md#still-deferred
blocked_by: []
tags: [purity, scripts]
closed: 2026-09-29
reason: User 2026-09-29: not wanted -- the standalone clangd and cuda MCP servers are not retired; the user actively uses them.
---

# R-0001 · Retire the standalone clangd and cuda MCP servers (purity Phase 2)

## Why

Phase 2 was meant to retire the standalone servers and move the p:mcp-clangd / p:mcp-cuda skills and the minion tool-lists onto purity_call. It never started; Scripts/mcp-clangd.py and Scripts/mcp-cuda.py are still in the tree.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->later: first triage 2026-09-28
- 2026-09-29 later->dropped: User 2026-09-29: not wanted -- the standalone clangd and cuda MCP servers are not retired; the user actively uses them.
