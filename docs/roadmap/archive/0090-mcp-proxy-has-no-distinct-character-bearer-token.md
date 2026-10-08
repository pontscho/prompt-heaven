---
name: 0090-mcp-proxy-has-no-distinct-character-bearer-token
type: roadmap-item
status: active
title: R-0090 · mcp-proxy has no distinct-character bearer token floor
description: Closed roadmap item R-0090 (done 2026-10-08).
id: R-0090
state: done
horizon: unset
origin: user:2026-10-07:proxy-token-distinct-floor
blocked_by: []
severity: low
tags: [auth, hardening, mcp-proxy]
closed: 2026-10-08
commit: 681e64870f1642ada5921610253cd3923bf231d2
---

# R-0090 · mcp-proxy has no distinct-character bearer token floor

## Why

The router refuses a bearer token of fewer than _TOKEN_MIN_DISTINCT (8) distinct characters (checked in _rt_cfg_token around the generated _http_token_value call), so a 32-character run of one letter is refused there (V1) and accepted by mcp-proxy, whose only floor is the generated _http_token_value's length (_TOKEN_MIN_LEN, 32) and printable-ASCII charset. R-0072 kept the token policy host-side on purpose (min_len and error are arguments, pinned by router J40(f) and proxy K5(f)), so the distinct-character check stayed a router-only hand check. Open: add the same floor to the proxy's two token callers (--token-file and MCP_PROXY_TOKEN) with a red-first J47-style case, or move the check into the canonical source as a further argument. Found during R-0072 (ADR 0029 declared limits).

## Log

- 2026-10-08 new->unset: proposed by p:minion-mason
- 2026-10-08 unset->done: commit 681e64870f1642ada5921610253cd3923bf231d2
