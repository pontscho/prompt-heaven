---
name: 0070-lift-log-value-into-shared-plumbing-so-no-server
type: roadmap-item
status: active
title: R-0070 · Lift _log_value into shared plumbing so no server logs raw method, id or name
description: Closed roadmap item R-0070 (done 2026-10-07).
id: R-0070
state: done
horizon: unset
origin: user:2026-10-05:fleet-log-value
blocked_by: []
severity: low
tags: [logging, scripts, security]
closed: 2026-10-07
commit: 72ed9b42d33171b54005cf5e1e217f0fb1f38aff
---

# R-0070 · Lift _log_value into shared plumbing so no server logs raw method, id or name

## Why

Security review 2026-10-05 of mcp-search, finding F19 (CWE-117): the fleet-canonical debug log lines write the JSON-RPC method, id and tool name raw, so a caller can forge log lines. mcp-proxy.py and, since 8dde3a6, mcp-search.py each carry a hand-written _log_value sanitizer; the other servers and the MCP_SKELETON template do not. Lift it into a canonical source (or the skeleton) and use it at every log site, with the wire_log gate extended to check it.

## Log

- 2026-10-05 new->unset: proposed by security review 2026-10-05
- 2026-10-07 unset->done: commit 72ed9b42d33171b54005cf5e1e217f0fb1f38aff
