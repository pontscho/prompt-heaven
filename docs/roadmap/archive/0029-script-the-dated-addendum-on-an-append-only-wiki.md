---
name: 0029-script-the-dated-addendum-on-an-append-only-wiki
type: roadmap-item
status: active
title: R-0029 · Script the dated addendum on an append-only wiki page
description: Closed roadmap item R-0029 (done 2026-09-28).
id: R-0029
state: done
horizon: now
origin: user:2026-09-28:adr-addendum-writer
blocked_by: []
tags: [wiki]
closed: 2026-09-28
commit: 2577192eebdb55c66746ea45a0755c5656edf3ad
---

# R-0029 · Script the dated addendum on an append-only wiki page

## Why

Closing R-0007 needed a dated addendum on ADR 0009, written by hand with Edit. An accepted ADR is append-only, so the only legal write is at the end of the file: a wiki script that appends one addendum section (fixed heading shape, today's date, title and body from a staged file) and refuses anything else enforces that by construction instead of by care. Lives with the wiki scripts, not in roadmap.py, which writes only docs/roadmap.

## Log

- 2026-09-28 new->next: user asked to script the hand steps (S028); waits for R-0015, which is editing the wiki scripts
- 2026-09-28 next->now [idea->active]: R-0015 closed, the wiki scripts are free
- 2026-09-28 now->done: commit 2577192eebdb55c66746ea45a0755c5656edf3ad
