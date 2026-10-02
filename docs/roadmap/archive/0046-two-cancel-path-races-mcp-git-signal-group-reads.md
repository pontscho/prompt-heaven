---
name: 0046-two-cancel-path-races-mcp-git-signal-group-reads
type: roadmap-item
status: active
title: R-0046 · Two cancel-path races: mcp-git _signal_group reads returncode unlocked; postgres holds _cancel_lock across network I/O
description: Closed roadmap item R-0046 (done 2026-10-02).
id: R-0046
state: done
horizon: next
origin: peer review by session tampermonkey-72, 2026-09-30, finding 3 (cancel paths)
blocked_by: []
tags: [concurrency, mcp]
closed: 2026-10-02
commit: 52701e9de8dce3097e485b687b5bc944570592c1
---

# R-0046 · Two cancel-path races: mcp-git _signal_group reads returncode unlocked; postgres holds _cancel_lock across network I/O

## Why

After R-0043: mcp-git's _signal_group reads proc.returncode without the lock, leaving a small PID-reuse window its docstring claims is closed; mcp-psql's send_cancel_request keeps _cancel_lock held across the cancel request's network I/O, so a slow or dead server stalls every other cancel. Both reported by a read-only review, not yet re-verified against the source.

## Log

- 2026-09-30 new->unset: User 2026-09-30: approved filing the peer review findings outside R-0044 scope.
- 2026-10-02 unset->next: User 2026-10-02: next.
- 2026-10-02 next->done: commit 52701e9de8dce3097e485b687b5bc944570592c1
