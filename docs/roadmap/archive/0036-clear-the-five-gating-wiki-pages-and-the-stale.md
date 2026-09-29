---
name: 0036-clear-the-five-gating-wiki-pages-and-the-stale
type: roadmap-item
status: active
title: R-0036 · Clear the five gating wiki pages and the stale README group column
description: Closed roadmap item R-0036 (done 2026-09-29).
id: R-0036
state: done
horizon: now
origin: user:2026-09-29:wiki-gating-dead-anchors
blocked_by: []
tags: [tests, wiki]
closed: 2026-09-29
commit: 5e7bf2b186a276d642683af914a6d78ae6ac3b58
---

# R-0036 · Clear the five gating wiki pages and the stale README group column

## Why

The server-side wiki freshness reports five gating pages with seven dead anchors. ADR 0022 still anchors roadmap.py:worktree_dirty twice after R-0024 removed it; spec-sandbox-run, layer-contract and requirements-yaml anchor docs/feature-implementation-plan.md, renamed in a6461d3; spec-sandbox-run anchors a tests/test_sbx.py that never existed; ADR 0017 uses example paths under tests/ that read as anchors. Accepted ADR bodies take only addenda, so the ADR cases need a decision on how an immutable page drops a dead anchor. The freshness.py CLI reports gating 0 because it gates only orphaned sources, which is how this stayed hidden. Separately, the groups column in tests/README.md is stale for purity_file_ops, wiki_index and checkpoint, and generated-regions.md types the server count its census region already renders.

## Log

- 2026-09-29 new->next: added
- 2026-09-29 next->now: triage 2026-09-29: user approved after R-0024/R-0034/R-0035
- 2026-09-29 now->now [idea->active]: Session S030: executing the now lane.
- 2026-09-29 now->done: commit 5e7bf2b186a276d642683af914a6d78ae6ac3b58
