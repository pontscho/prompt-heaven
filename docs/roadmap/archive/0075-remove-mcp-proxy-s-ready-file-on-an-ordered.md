---
name: 0075-remove-mcp-proxy-s-ready-file-on-an-ordered
type: roadmap-item
status: active
title: R-0075 · Remove mcp-proxy's ready file on an ordered shutdown
description: Closed roadmap item R-0075 (done 2026-10-07).
id: R-0075
state: done
horizon: unset
origin: user:2026-10-06:mcp-proxy-ready-file-removal
blocked_by: []
severity: low
tags: [scripts, security]
closed: 2026-10-07
commit: 1748f4681d51fb74137e9dbb342f6f445115ee87
---

# R-0075 · Remove mcp-proxy's ready file on an ordered shutdown

## Why

Counterpart of llm-router finding V24 (verified LOW, CWE-459), fixed in the router as ADR 0028 deviation 29. mcp-proxy writes its ready file but never removes it, so a stale file with a dead pid and port outlives the process. Port the router's approach: on ordered shutdown open the file without following a symlink, and unlink it only if it still holds our own pid; declare the read-then-unlink race and the SIGKILL case.

## Log

- 2026-10-06 new->unset: proposed by llm-router validation round 1
- 2026-10-07 unset->done: commit 1748f4681d51fb74137e9dbb342f6f445115ee87
