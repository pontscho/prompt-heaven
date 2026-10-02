---
name: 0055-restrict-destination-ports-in-webfetch-and-the
type: roadmap-item
status: active
title: R-0055 · Restrict destination ports in webfetch and the search scripts (a bad-port list like the Fetch spec)
description: Closed roadmap item R-0055 (done 2026-10-02).
id: R-0055
state: done
horizon: next
origin: user:2026-10-01:chrome-client-bad-port-list
blocked_by: []
severity: low
tags: [chrome-client, security, webfetch]
closed: 2026-10-02
commit: aae26430c4cb709998b2eb867d9fa9b403d9a98e
---

# R-0055 · Restrict destination ports in webfetch and the search scripts (a bad-port list like the Fetch spec)

## Why

Security review 20261001-082224, finding F3 (LOW, verified, not a regression): the stdlib Chrome client and its hosts connect to any port a URL or a redirect names. Browsers refuse the Fetch standard's bad-port list (SMTP, IRC and similar) so a fetched page cannot make the client speak to a non-HTTP service; ours has no such list. ADR 0026 declares the gap.

## Log

- 2026-10-01 new->unset: deferred by security review 20261001-082224
- 2026-10-02 unset->next: User 2026-10-02: fix it.
- 2026-10-02 next->done: commit aae26430c4cb709998b2eb867d9fa9b403d9a98e
