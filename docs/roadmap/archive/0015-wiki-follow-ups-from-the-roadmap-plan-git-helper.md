---
name: 0015-wiki-follow-ups-from-the-roadmap-plan-git-helper
type: roadmap-item
status: active
title: R-0015 · Wiki follow-ups from the roadmap plan (git helper timeout and hardening, type signal tokens, stale plan-slot links)
description: Closed roadmap item R-0015 (done 2026-09-28).
id: R-0015
state: done
horizon: now
origin: docs/specs/spec-roadmap.md#out-of-scope-post-implementation-follow-ups
blocked_by: []
tags: [wiki]
closed: 2026-09-28
commit: 69a93c08edd319443a9cd2d07586f65ff1a9dfb6
---

# R-0015 · Wiki follow-ups from the roadmap plan (git helper timeout and hardening, type signal tokens, stale plan-slot links)

## Why

Give _wikilib.git() the server's timeout and fix the stale tests/test_spawn_stdin.py docstring; apply the roadmap git hardening (safe argv, GIT_NO_LAZY_FETCH) to the wiki git helpers; add TYPE_SIGNAL_TOKENS entries for roadmap and roadmap-item; repoint the generic feature-implementation-plan links in the requirements-yaml and layer-contract pages, which now name an empty slot.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->now: first triage 2026-09-28
- 2026-09-28 now->now [idea->active]: work started in session S028 (parallel now-lane execution)
- 2026-09-28 now->done: commit 69a93c08edd319443a9cd2d07586f65ff1a9dfb6
