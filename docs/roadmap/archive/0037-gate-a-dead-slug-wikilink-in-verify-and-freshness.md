---
name: 0037-gate-a-dead-slug-wikilink-in-verify-and-freshness
type: roadmap-item
status: active
title: R-0037 · Gate a dead [[slug]] wikilink in verify and freshness
description: Closed roadmap item R-0037 (done 2026-09-29).
id: R-0037
state: done
horizon: now
origin: user:2026-09-29:r0013-census
blocked_by: []
tags: [wiki]
closed: 2026-09-29
commit: 7b8cca4329aa38e36198bd32093a9bc5d43c9ecd
---

# R-0037 · Gate a dead [[slug]] wikilink in verify and freshness

## Why

The R-0013 census found that nothing checks a [[slug]] wikilink: extract_wikilinks is called only in Scripts/mcp-wiki.py:reindex_collect to build the referenced set for orphan detection, and verify and freshness never read wikilinks. A link to a renamed or deleted page is accepted silently. None is dead today; the gap is that nothing would say so. The frozen-record carve-out (ADR 0019 addendum, R-0036) would need a decision for wikilinks too.

## Log

- 2026-09-29 new->unset: added
- 2026-09-29 unset->now: User 2026-09-29: go.
- 2026-09-29 now->now [idea->active]: User 2026-09-29: go.
- 2026-09-29 now->done: commit 7b8cca4329aa38e36198bd32093a9bc5d43c9ecd
