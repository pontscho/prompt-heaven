---
name: 0091-llm-router-drops-to-the-10-s-header-timeout-after
type: roadmap-item
status: active
title: R-0091 · llm-router drops to the 10 s header timeout after parse_request before authentication
description: Closed roadmap item R-0091 (done 2026-10-08).
id: R-0091
state: done
horizon: unset
origin: user:2026-10-07:router-post-parse-preauth-timeout
blocked_by: []
severity: low
tags: [hardening, http-front, llm-router]
closed: 2026-10-08
commit: a0e2d2748a9fbc96c2f11527acda594eb79953c6
---

# R-0091 · llm-router drops to the 10 s header timeout after parse_request before authentication

## Why

Pre-existing, observed during R-0072 and not changed by it. The router's handle_one_request arms the 5 s pre-auth bound (_HTTP_PREAUTH_TIMEOUT_S) for the header phase of a connection that has not authenticated, but the generated parse_request then sets the per-recv socket timeout to self.timeout, the 10 s header bound, before _precheck runs. Its comment speaks of the per-recv pre-auth timeout staying until the host's precheck passes; that is true in the proxy, whose pre-auth timeout is the header bound, and reads wrongly in the router. The body read runs under its own total deadline once the checks pass, and whether any socket read happens between parse_request and the precheck verdict was not measured, so the practical exposure is unknown and may be nil. Open: decide whether the router should restore its pre-auth timeout after parse_request (a hand hook, per the PD-7 rule of ADR 0029, since parse_request is generated) or whether the comment should be reworded in the canonical source; either needs a red-first router case.

## Log

- 2026-10-08 new->unset: proposed by p:minion-mason
- 2026-10-08 unset->done: commit a0e2d2748a9fbc96c2f11527acda594eb79953c6
