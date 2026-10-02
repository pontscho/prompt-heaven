---
name: 0053-detect-bing-s-challenge-served-as-a-200-that
type: roadmap-item
status: active
title: R-0053 · Detect Bing's challenge served as a 200 that parses to no results (R25)
description: Closed roadmap item R-0053 (dropped 2026-10-02).
id: R-0053
state: dropped
horizon: unset
origin: user:2026-10-01:bing-200-empty-challenge
blocked_by: []
severity: low
tags: [search]
closed: 2026-10-02
reason: User 2026-10-02: a Bing block has never been observed; nothing to measure against.
---

# R-0053 · Detect Bing's challenge served as a 200 that parses to no results (R25)

## Why

D16 (M3) of ADR 0026 makes 403 a block on Bing and 429/5xx never one, but a Bing challenge served as a 200 whose body parses to an empty result list is not detected by _bing_blocked in Scripts/search_duckduckgo.py: deferred and declared (R25), listed again in the ADR's "What stayed unverified, or open" section. Neither shape was ever observed: Bing answered the verified transport with 10 results in both live samples (task-038, task-044), so the 403 rule itself is declared rather than measured. The work needs a recorded Bing challenge page first, then a structural predicate that a genuine zero-result query cannot trip.

## Log

- 2026-10-01 new->unset: follow-up of R-0044 (task-056)
- 2026-10-02 unset->dropped: User 2026-10-02: a Bing block has never been observed; nothing to measure against.
