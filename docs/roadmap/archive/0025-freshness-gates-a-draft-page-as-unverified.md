---
name: 0025-freshness-gates-a-draft-page-as-unverified
type: roadmap-item
status: active
title: R-0025 · freshness gates a draft page as unverified, contradicting the wiki schema
description: Closed roadmap item R-0025 (done 2026-09-28).
id: R-0025
state: done
horizon: now
origin: user:2026-09-28:freshness-draft-unverified
blocked_by: []
tags: [wiki]
closed: 2026-09-28
commit: 9edd002f128d607591983a7a11dffc4744541ffb
---

# R-0025 · freshness gates a draft page as unverified, contradicting the wiki schema

## Why

The p:wiki SKILL.md names status draft as the right way to express a sourced page without verified, but freshness.py classifies such a page as unverified and counts it in its gating exit code whatever its status. Observed on ADR 0022 before its promotion.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->next: first triage 2026-09-28
- 2026-09-28 next->now [idea->active]: started
- 2026-09-28 now->done: commit 9edd002f128d607591983a7a11dffc4744541ffb
