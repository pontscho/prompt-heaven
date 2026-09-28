---
name: 0027-origin-dedup-allows-only-one-item-per-source
type: roadmap-item
status: active
title: R-0027 · Origin dedup allows only one item per source section
description: Closed roadmap item R-0027 (done 2026-09-28).
id: R-0027
state: done
horizon: next
origin: user:2026-09-28:roadmap-origin-per-section
blocked_by: []
tags: [roadmap]
closed: 2026-09-28
commit: 2267a7c7db16c05b865205fb012db32e566f7637
---

# R-0027 · Origin dedup allows only one item per source section

## Why

origin is both the anchor and the dedup key, and the harvest convention is path plus heading slug, so a section with several deferred threads can only yield one item; the first adopt had to bundle ADR 0014, 0016, 0022 and the DDG sections. Decide whether origin gets a disambiguating suffix or bundling is the intended shape.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->next: first triage 2026-09-28
- 2026-09-28 next->done: commit 2267a7c7db16c05b865205fb012db32e566f7637
