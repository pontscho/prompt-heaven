---
name: 0114-webfetch-post-and-a-session-cookie-jar-for-multi
type: roadmap-item
status: active
title: R-0114 · webfetch: POST and a session cookie jar for multi-step authenticated flows
description: Closed roadmap item R-0114 (done 2026-11-12).
id: R-0114
state: done
horizon: unset
origin: peer ngs-elk-oholist, 2026-11-12 (live use)
blocked_by: []
tags: [mcp-webfetch, security]
closed: 2026-11-12
commit: e168e3be7c074d897bca019af6e11f604e38bfc1
---

# R-0114 · webfetch: POST and a session cookie jar for multi-step authenticated flows

## Why

webfetch_call fetch is GET/HEAD only with no cookie continuity between calls, so an OAuth/OIDC login (credential POST, Set-Cookie carried across redirects) cannot be driven; reported while testing an Entra ID / oauth2-proxy flow. Opposing considerations: POST bodies and a cookie jar widen the exfiltration and CSRF surface of a model-driven fetcher, and the Chrome path does not verify certificates (R-0047), so any design needs an explicit opt-in session mode, a per-session jar bound to one host set, and verified transport only. Not decided.

## Log

- 2026-11-12 new->unset: added
- 2026-11-12 unset->unset: edited why: drop an invented cross-reference
- 2026-11-12 unset->done: commit e168e3be7c074d897bca019af6e11f604e38bfc1
