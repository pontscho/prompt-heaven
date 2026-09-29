---
name: 0012-make-purity-s-path-style-glob-branch-honour
type: roadmap-item
status: active
title: R-0012 · Make purity's path-style glob branch honour fnmatch character classes
description: Closed roadmap item R-0012 (done 2026-09-29).
id: R-0012
state: done
horizon: now
origin: docs/adr/0017-a-silent-zero-is-the-defect.md#consequences
blocked_by: []
tags: [purity]
closed: 2026-09-29
commit: c0ec1d5283e901246ac03a4787829715178212ca
---

# R-0012 · Make purity's path-style glob branch honour fnmatch character classes

## Why

The path-style branch treats character classes as literals while the bare-mask branch honours them, so [ab].py and src/[ab].py disagree inside one handler. Declared, not fixed; nothing has reached for it yet.

## Log

- 2026-09-28 new->unset: harvested by adopt
- 2026-09-28 unset->later: first triage 2026-09-28
- 2026-09-29 later->now: User 2026-09-29: small, run in parallel.
- 2026-09-29 now->now [idea->active]: User 2026-09-29: small, run in parallel.
- 2026-09-29 now->done: commit c0ec1d5283e901246ac03a4787829715178212ca
