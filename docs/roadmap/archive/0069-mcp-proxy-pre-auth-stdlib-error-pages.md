---
name: 0069-mcp-proxy-pre-auth-stdlib-error-pages
type: roadmap-item
status: active
title: R-0069 · mcp-proxy: pre-auth stdlib error pages
description: Closed roadmap item R-0069 (done 2026-10-07).
id: R-0069
state: done
horizon: unset
origin: user:2026-10-03:mcp-proxy-stdlib-error-pages
blocked_by: []
severity: low
tags: [scripts, security]
closed: 2026-10-07
commit: c69b6433c7feaf12f2495efd74bbfe906bff4601
---

# R-0069 · mcp-proxy: pre-auth stdlib error pages

## Why

Security review round 1, finding F42 (verified LOW), plus a round-3 candidate on the same surface. Errors the stdlib http.server answers before any proxy check (400 bad request line, 414, 431, 501 unknown method) use its default HTML error page: no nosniff or no-store headers, and the offending request-line token is reflected (escaped) into the reason phrase and body. Overriding send_error with a fixed, body-less refusal would close both.

## Log

- 2026-10-03 new->unset: proposed by p:security-review
- 2026-10-07 unset->done: commit c69b6433c7feaf12f2495efd74bbfe906bff4601
