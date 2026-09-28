---
name: 0032-bring-the-freshness-py-cli-gate-in-line-with-adr
type: roadmap-item
status: active
title: R-0032 · Bring the freshness.py CLI gate in line with ADR 0019
description: Closed roadmap item R-0032 (done 2026-09-28).
id: R-0032
state: done
horizon: now
origin: user:2026-09-28:freshness-cli-predates-adr-0019
blocked_by: []
tags: [wiki]
closed: 2026-09-28
commit: 837d7ee2bcedaffb1d1464f53ec42ce61c4c21c8
---

# R-0032 · Bring the freshness.py CLI gate in line with ADR 0019

## Why

Found by R-0025. The server's gating line counts only what verification can demonstrate and treats git lag (stale, orphaned-source, unverified) as advisory, per ADR 0019 half one. freshness.py still sets its exit code from stale + orphaned-source + unverified, the pre-0019 meaning, while the wiki SKILL.md says the CI gate runs the same logic as the server. Decide whether the CLI adopts the verdict/advisory split (and what it can prove without the server's anchor verification) or the SKILL.md sentence is corrected; R-0025's draft exemption is then either subsumed or kept. Close with an addendum on ADR 0019 via addendum.py.

## Log

- 2026-09-28 new->next: found by the R-0025 fix (S028)
- 2026-09-28 next->now [idea->active]: started
- 2026-09-28 now->done: commit 837d7ee2bcedaffb1d1464f53ec42ce61c4c21c8
