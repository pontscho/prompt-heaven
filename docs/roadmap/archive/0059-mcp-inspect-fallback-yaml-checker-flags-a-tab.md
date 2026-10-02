---
name: 0059-mcp-inspect-fallback-yaml-checker-flags-a-tab
type: roadmap-item
status: active
title: R-0059 · mcp-inspect fallback YAML checker flags a TAB inside a literal block scalar
description: Closed roadmap item R-0059 (done 2026-10-02).
id: R-0059
state: done
horizon: next
origin: user:2026-10-01:inspect-yaml-fallback-tab-in-block-scalar
blocked_by: []
severity: low
tags: [inspect]
closed: 2026-10-02
commit: 65028cabc51c2220f3e97fed21f2e76334bbd2b1
---

# R-0059 · mcp-inspect fallback YAML checker flags a TAB inside a literal block scalar

## Why

Observed during security review 20261001-082224's fix pass: the mcp-inspect validate fallback YAML checker (the path taken without PyYAML) reports a TAB inside a literal block scalar as an error, at requirements.yaml line 2466, although YAML allows a tab in a block scalar's content; only indentation must be spaces. A false positive on the plan file every task run validates. Fix the fallback checker so the tab rule applies to indentation only.

## Log

- 2026-10-01 new->unset: observed during security review 20261001-082224
- 2026-10-02 unset->next: User 2026-10-02: next.
- 2026-10-02 next->done: commit 65028cabc51c2220f3e97fed21f2e76334bbd2b1
