---
name: 0058-pin-or-bound-mcp-webfetch-s-pep-723-dependencies
type: roadmap-item
status: active
title: R-0058 · Pin or bound mcp-webfetch's PEP 723 dependencies, or finish removing lxml
description: Closed roadmap item R-0058 (done 2026-10-02).
id: R-0058
state: done
horizon: next
origin: user:2026-10-01:webfetch-pep723-pinning
blocked_by: []
severity: low
tags: [security, webfetch]
closed: 2026-10-02
commit: 62163911f6d619d47a1800f2ec874a5a9379c791
---

# R-0058 · Pin or bound mcp-webfetch's PEP 723 dependencies, or finish removing lxml

## Why

Security review 20261001-082224, finding F47 (LOW, pre-existing): the PEP 723 block declares beautifulsoup4, markdownify and lxml with no version bounds, so every uv cold start resolves whatever the index serves that day. Either add bounds (or uv's exclude-newer), or replace bs4's lxml tree builder with html.parser so lxml leaves the tree as ADR 0024 decided for the search script. Recorded in the ADR 0024 addendum of 2026-10-01.

## Log

- 2026-10-01 new->unset: deferred by security review 20261001-082224
- 2026-10-02 unset->next: User 2026-10-02: approved -- bs4 on html.parser, lxml leaves, bound bs4/markdownify versions.
- 2026-10-02 next->done: commit 62163911f6d619d47a1800f2ec874a5a9379c791
