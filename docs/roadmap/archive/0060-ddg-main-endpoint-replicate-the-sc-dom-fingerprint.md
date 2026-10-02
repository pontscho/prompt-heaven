---
name: 0060-ddg-main-endpoint-replicate-the-sc-dom-fingerprint
type: roadmap-item
status: active
title: R-0060 · DDG main endpoint: replicate the __sc__ DOM fingerprint
description: Closed roadmap item R-0060 (dropped 2026-10-02).
id: R-0060
state: dropped
horizon: later
origin: docs/roadmap/archive/0017-ddg-planned-investigation-tls-capture-diff-byte.md
blocked_by: []
follows: R-0017
tags: [research, search]
closed: 2026-10-02
reason: Triage 2026-10-02: lite passes 23/23; the main endpoint is speculative. If lite starts blocking, a fresh measurement will be needed anyway.
---

# R-0060 · DDG main endpoint: replicate the __sc__ DOM fingerprint

## Why

The third of R-0017's planned investigations, left open when R-0017 closed. The stdlib Chrome client (3fbe5bf) talks to DDG lite, which passed the 2026-10-01 acceptance run 23/23 on the verified transport; the main endpoint's __sc__ DOM fingerprint was never examined. Worth doing only if lite starts blocking or loses results the main endpoint has.

## Log

- 2026-10-02 new->later: added
- 2026-10-02 later->dropped: Triage 2026-10-02: lite passes 23/23; the main endpoint is speculative. If lite starts blocking, a fresh measurement will be needed anyway.
