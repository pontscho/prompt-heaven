---
name: 0016-bound-search-for-pattern-regex-cost-redos-residual
type: roadmap-item
status: active
title: R-0016 · Bound search_for_pattern regex cost (ReDoS residual)
description: Closed roadmap item R-0016 (done 2026-09-29).
id: R-0016
state: done
horizon: now
origin: docs/specs/spec-purity-unification.md#known-security-limitations
blocked_by: []
tags: [purity, security]
closed: 2026-09-29
commit: c0ec1d5283e901246ac03a4787829715178212ca
---

# R-0016 · Bound search_for_pattern regex cost (ReDoS residual)

## Why

A caller regex can still backtrack catastrophically. A guard exists in mcp-purity now, but its comments call a single catastrophic call a residual. Check what is already covered before starting.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->later: first triage 2026-09-28
- 2026-09-29 later->now: User 2026-09-29: small, run in parallel.
- 2026-09-29 now->now [idea->active]: User 2026-09-29: small, run in parallel.
- 2026-09-29 now->done: commit c0ec1d5283e901246ac03a4787829715178212ca
