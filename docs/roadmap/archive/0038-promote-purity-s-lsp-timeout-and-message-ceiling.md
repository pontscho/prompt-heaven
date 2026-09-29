---
name: 0038-promote-purity-s-lsp-timeout-and-message-ceiling
type: roadmap-item
status: active
title: R-0038 · Promote purity's LSP timeout and message ceiling to module-level constants
description: Closed roadmap item R-0038 (done 2026-09-29).
id: R-0038
state: done
horizon: now
origin: user:2026-09-29:r0013-census-purity-constants
blocked_by: []
tags: [purity, wiki]
closed: 2026-09-29
commit: 36cf345ca1a9a1c6f46d37f0b4d04a1f30b4de76
---

# R-0038 · Promote purity's LSP timeout and message ceiling to module-level constants

## Why

docs/specs/spec-purity-luals-phase1.md types a 90-second timeout and a 64 MB LSP Content-Length ceiling, but in Scripts/mcp-purity.py the first is a literal timeout=90.0 and the second a function-local _LSP_MAX_MESSAGE, so the wiki can anchor neither by symbol. Found by the R-0013 census.

## Log

- 2026-09-29 new->unset: added
- 2026-09-29 unset->now [idea->active]: started
- 2026-09-29 now->done: commit 36cf345ca1a9a1c6f46d37f0b4d04a1f30b4de76
