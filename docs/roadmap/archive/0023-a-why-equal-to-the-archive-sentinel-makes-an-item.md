---
name: 0023-a-why-equal-to-the-archive-sentinel-makes-an-item
type: roadmap-item
status: active
title: R-0023 · A why equal to the archive sentinel makes an item unclosable
description: Closed roadmap item R-0023 (done 2026-09-28).
id: R-0023
state: done
horizon: now
origin: user:2026-09-28:roadmap-none-sentinel
blocked_by: []
tags: [roadmap]
closed: 2026-09-28
commit: 386f6f2c7a11e7f6380b22a14044f0b86ec55ac4
---

# R-0023 · A why equal to the archive sentinel makes an item unclosable

## Why

render_archive writes the sentinel _(none)_ for an empty why and parse_archive reads it back as empty, so an item whose why is exactly that text fails close's round-trip self-check with an internal refusal. check_why should refuse it, or the sentinel should be escaped.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->next: first triage 2026-09-28
- 2026-09-28 next->now [idea->active]: started
- 2026-09-28 now->done: commit 386f6f2c7a11e7f6380b22a14044f0b86ec55ac4
