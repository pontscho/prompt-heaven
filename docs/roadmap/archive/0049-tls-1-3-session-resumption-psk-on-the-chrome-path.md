---
name: 0049-tls-1-3-session-resumption-psk-on-the-chrome-path
type: roadmap-item
status: active
title: R-0049 · TLS 1.3 session resumption (PSK) on the Chrome path
description: Closed roadmap item R-0049 (dropped 2026-10-02).
id: R-0049
state: dropped
horizon: unset
origin: user:2026-10-01:chrome-session-resumption
blocked_by: []
severity: low
tags: [scripts, tls]
closed: 2026-10-02
reason: User 2026-10-02: resumption only pays off for several fetches over one connection within one process, and even then its value is doubtful.
---

# R-0049 · TLS 1.3 session resumption (PSK) on the Chrome path

## Why

Every Chrome-path connection is a fresh full handshake: the client implements no session resumption and no PSK, so Chrome's resumed JA4 (t13d1518h2_...) is never produced, and a host that sees one client always arrive without a ticket sees a shape real Chrome rarely shows on a return visit. Declared in ADR 0026, sections "What stayed unverified, or open" (other declared limits) and "Out of scope, deliberately". The 2026-09-30 capture set holds no resumed handshake (spec-ddg section 2.9, the Chrome 153 capture set), so a capture against a trusted loopback certificate comes first. 0-RTT stays out of scope.

## Log

- 2026-10-01 new->unset: follow-up of R-0044 (task-056)
- 2026-10-02 unset->dropped: User 2026-10-02: resumption only pays off for several fetches over one connection within one process, and even then its value is doubtful.
