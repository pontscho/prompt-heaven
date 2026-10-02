---
name: 0017-ddg-planned-investigation-tls-capture-diff-byte
type: roadmap-item
status: active
title: R-0017 · DDG planned investigation (TLS capture diff, byte-identical handshake, __sc__ fingerprint)
description: Closed roadmap item R-0017 (done 2026-10-02).
id: R-0017
state: done
horizon: now
origin: docs/concepts/spec-ddg.md#82-planned-investigation
blocked_by: []
tags: [research, search]
closed: 2026-10-02
commit: 3fbe5bf10074cea49a417663c8d826d2a4571f3a
---

# R-0017 · DDG planned investigation (TLS capture diff, byte-identical handshake, __sc__ fingerprint)

## Why

Three planned investigations into DDG's bot detection: capture and diff raw TCP/TLS between Chrome and curl_cffi (mcp-tshark now exists), build a byte-identical browser handshake via an external library, and replicate the __sc__ DOM fingerprint for the main endpoint.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-29 unset->later: User 2026-09-29: liked, stays a plan for now.
- 2026-09-29 later->now [idea->active]: started
- 2026-10-02 now->done: commit 3fbe5bf10074cea49a417663c8d826d2a4571f3a
