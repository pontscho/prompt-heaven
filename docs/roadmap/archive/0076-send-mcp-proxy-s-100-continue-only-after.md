---
name: 0076-send-mcp-proxy-s-100-continue-only-after
type: roadmap-item
status: active
title: R-0076 · Send mcp-proxy's 100 Continue only after authentication
description: Closed roadmap item R-0076 (done 2026-10-07).
id: R-0076
state: done
horizon: unset
origin: user:2026-10-06:mcp-proxy-expect-100-after-auth
blocked_by: []
severity: low
tags: [scripts, security]
closed: 2026-10-07
commit: 6e91e1d7ffac871f12d6ff8b1ad7d472a32d941b
---

# R-0076 · Send mcp-proxy's 100 Continue only after authentication

## Why

Counterpart of llm-router finding V37 (verified LOW, CWE-696), fixed in the router as ADR 0028 deviation 18. mcp-proxy's HTTP front runs HTTP/1.1 and does not override handle_expect_100, so the stdlib answers Expect: 100-continue inside parse_request, before the bearer check. The body is still never read before auth, so the impact is negligible. Port the router's fix: handle_expect_100 sends nothing, and the POST path sends 100 Continue only after auth and the framing checks pass; test it red first.

## Log

- 2026-10-06 new->unset: proposed by llm-router validation round 1
- 2026-10-07 unset->done: commit 6e91e1d7ffac871f12d6ff8b1ad7d472a32d941b
