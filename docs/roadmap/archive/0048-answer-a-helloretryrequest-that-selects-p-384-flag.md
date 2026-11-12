---
name: 0048-answer-a-helloretryrequest-that-selects-p-384-flag
type: roadmap-item
status: active
title: R-0048 · Answer a HelloRetryRequest that selects P-384 (FLAG-2)
description: Closed roadmap item R-0048 (done 2026-11-12).
id: R-0048
state: done
horizon: later
origin: docs/adr/0026-speak-chrome-from-the-stdlib-verify-by-default.md#helloretryrequest-p-256-only-and-the-ch2-chrome-sends
blocked_by: []
severity: low
tags: [scripts, tls]
closed: 2026-11-12
commit: bb3911dc2689a25325e0bb2504e069b0e1a7873d
---

# R-0048 · Answer a HelloRetryRequest that selects P-384 (FLAG-2)

## Why

Chrome 153 offers P-384 (0x0018) alongside P-256 in supported_groups, but the stdlib client answers an HRR for P-256 only (FLAG-2, user, S035). An HRR selecting P-384 fails with "tls: HelloRetryRequest selected secp384r1 (not implemented)". A server that prefers P-384 therefore gets no handshake, where real Chrome would complete one. The work is a pure-Python P-384 ECDH with KATs, the CH2 shape the capture set already measured for P-256, and turning the refusal row of the HRR gate into an accepting one.

## Log

- 2026-10-01 new->unset: follow-up of R-0044 (task-056, FLAG-2)
- 2026-10-02 unset->later: User 2026-10-02: keep it recorded; log a warning when a server does select P-384 so a real case gets noticed.
- 2026-11-12 later->done: commit bb3911dc2689a25325e0bb2504e069b0e1a7873d
