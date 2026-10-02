---
name: 0056-normalise-hosts-with-uts-46-nontransitional
type: roadmap-item
status: active
title: R-0056 · Normalise hosts with UTS-46 nontransitional processing instead of IDNA2003
description: Closed roadmap item R-0056 (done 2026-10-02).
id: R-0056
state: done
horizon: next
origin: user:2026-10-01:chrome-client-uts46-host
blocked_by: []
severity: low
tags: [chrome-client, security]
closed: 2026-10-02
commit: aae26430c4cb709998b2eb867d9fa9b403d9a98e
---

# R-0056 · Normalise hosts with UTS-46 nontransitional processing instead of IDNA2003

## Why

Security review 20261001-082224, finding F5 (LOW, CWE-176): the client's URL normaliser IDNA-encodes with Python's idna codec, which is IDNA2003 and transitional, so a host with a sharp s becomes ss and final sigma, ZWJ and ZWNJ are mapped too. Chrome uses UTS-46 nontransitional processing, so the client can reach a different host than the browser would for the same URL. Fix: UTS-46 nontransitional mapping, or refuse the four deviation characters. ADR 0026 declares the gap.

## Log

- 2026-10-01 new->unset: deferred by security review 20261001-082224
- 2026-10-02 unset->next: User 2026-10-02: fix it.
- 2026-10-02 next->done: commit aae26430c4cb709998b2eb867d9fa9b403d9a98e
