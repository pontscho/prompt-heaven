---
name: 0067-mcp-proxy-nan-and-infinity-literals-are-accepted
type: roadmap-item
status: active
title: R-0067 · mcp-proxy: NaN and Infinity literals are accepted and re-emitted
description: Closed roadmap item R-0067 (done 2026-10-07).
id: R-0067
state: done
horizon: unset
origin: user:2026-10-03:mcp-proxy-allow-nan
blocked_by: []
severity: low
tags: [scripts, security]
closed: 2026-10-07
commit: d4a241bf8102c57ca657f524d73e81a5520d1d0b
---

# R-0067 · mcp-proxy: NaN and Infinity literals are accepted and re-emitted

## Why

Security review round 1, finding F19 (verified LOW, CWE-20). The stdlib json module accepts NaN, Infinity and -Infinity by default and emits them again, which is not valid JSON for a strict peer. Left unfixed because allow_nan=False changes behaviour fleet-wide; a fix should decide whether it belongs in the shared _mcp_json canonical source rather than in the proxy alone.

## Log

- 2026-10-03 new->unset: proposed by p:security-review
- 2026-10-07 unset->done: commit d4a241bf8102c57ca657f524d73e81a5520d1d0b
