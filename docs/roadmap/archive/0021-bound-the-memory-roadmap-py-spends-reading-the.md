---
name: 0021-bound-the-memory-roadmap-py-spends-reading-the
type: roadmap-item
status: active
title: R-0021 · Bound the memory roadmap.py spends reading the archive
description: Closed roadmap item R-0021 (dropped 2026-09-29).
id: R-0021
state: dropped
horizon: later
origin: user:2026-09-28:roadmap-archive-memory
blocked_by: []
severity: low
tags: [roadmap, security]
closed: 2026-09-29
reason: Dropped by the R-0024 review: the only route to many oversized archive pages is a clone that already carried them, the consequence is a slow read that writes and loses nothing, every archived item must be parsed anyway, a total cap would be a second unargued number, and PAGE_MAX_BYTES already bounds a single planted file.
---

# R-0021 · Bound the memory roadmap.py spends reading the archive

## Why

Security review 2026-09-28 (F8, LOW): read_state_files holds every archive entry (each up to PAGE_MAX_BYTES) in memory before parsing, with no count or total-bytes bound.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->later: first triage 2026-09-28
- 2026-09-29 later->dropped: Dropped by the R-0024 review: the only route to many oversized archive pages is a clone that already carried them, the consequence is a slow read that writes and loses nothing, every archived item must be parsed anyway, a total cap would be a second unargued number, and PAGE_MAX_BYTES already bounds a single planted file.
