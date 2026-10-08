---
name: 0089-port-llm-router-s-pre-auth-header-bound-v3-to-mcp
type: roadmap-item
status: active
title: R-0089 · Port llm-router's pre-auth header bound (V3) to mcp-proxy's HTTP front
description: Closed roadmap item R-0089 (done 2026-10-08).
id: R-0089
state: done
horizon: unset
origin: docs/adr/0029-the-http-front-is-a-domain.md#declared-limits
blocked_by: []
severity: low
tags: [hardening, http-front, mcp-proxy]
closed: 2026-10-08
commit: 681e64870f1642ada5921610253cd3923bf231d2
---

# R-0089 · Port llm-router's pre-auth header bound (V3) to mcp-proxy's HTTP front

## Why

The proxy's handle_one_request applies one header bound (10 s) to every request; the router applies a shorter one (_HTTP_PREAUTH_TIMEOUT_S, 5 s) until the connection has authenticated, so an unauthenticated peer holds a --max-connections slot for less time (a mitigation only: a peer that reconnects can still keep every slot busy). R-0072 lifted only what was identical in the two fronts, so handle_one_request and setup stay declared adaptations in tests/test_generated_region.py HTTPFRONT_ADAPTATIONS, and R-0072 closed with P9, P10 and P12-P14 of ADR 0028's copy table (and the rest of P7/P8) still declared adaptations. Porting the bound is a behaviour change to the proxy, not part of a lift: it needs an authenticated flag set by the proxy's _precheck, a new proxy constant, a red-first proxy case, and a decision whether handle_one_request and setup then become canonical members. Also recorded as the proxy's missing V3 pre-auth bound in the S061 security triage residuals.

## Log

- 2026-10-08 new->unset: proposed by p:minion-mason
- 2026-10-08 unset->done: commit 681e64870f1642ada5921610253cd3923bf231d2
