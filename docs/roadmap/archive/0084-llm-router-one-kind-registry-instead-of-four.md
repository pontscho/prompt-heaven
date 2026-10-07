---
name: 0084-llm-router-one-kind-registry-instead-of-four
type: roadmap-item
status: active
title: R-0084 · llm-router: one kind registry instead of four
description: Closed roadmap item R-0084 (done 2026-10-07).
id: R-0084
state: done
horizon: unset
origin: user:2026-10-07:llm-router-single-kind-registry
blocked_by: []
tags: [llm-router, shared-code]
closed: 2026-10-07
commit: 82a4e1af15b1f8cfe139d8815da7e836139f35c6
---

# R-0084 · llm-router: one kind registry instead of four

## Why

NFR-2 of the codex/openai feature: a dialect that is also a new config kind costs one profile row, a three-line ResponsesAdapter subclass, and one entry each in _KINDS, _RT_KIND_AUTH_PROFILES, KIND_CLASSES and ADAPTERS. Static case J28 gates only the translator half (row plus subclass); the four registries are listed, not gated (declared limit 25, M5).

Making them data-driven from one registry was out of scope because it touches every existing kind and the A14 refusal path. The work: derive the four from one table, checked at import, and extend J28 (or a new J case) to cover the registry half, so a new kind is one row.

## Log

- 2026-10-07 new->unset: proposed by llm-router codex/openai plan Step 18 (task-060)
- 2026-10-07 unset->done: commit 82a4e1af15b1f8cfe139d8815da7e836139f35c6
