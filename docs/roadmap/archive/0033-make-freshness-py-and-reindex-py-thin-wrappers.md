---
name: 0033-make-freshness-py-and-reindex-py-thin-wrappers
type: roadmap-item
status: active
title: R-0033 · Make freshness.py and reindex.py thin wrappers over mcp-wiki, as measure_cli.py already is
description: Closed roadmap item R-0033 (done 2026-09-29).
id: R-0033
state: done
horizon: now
origin: user:2026-09-29:wiki-cli-thin-wrappers
blocked_by: []
tags: [wiki]
closed: 2026-09-29
commit: 1fa5047246e676513db52f8a769e8aeb9889e67c
---

# R-0033 · Make freshness.py and reindex.py thin wrappers over mcp-wiki, as measure_cli.py already is

## Why

R-0002 gated the 19 vendored function pairs and 12 constants for parity but kept the duplication, because generation is refused by the generator's anchored-on-file and tab rules. measure_cli.py already loads the server module off disk and calls it; doing the same for freshness.py and reindex.py would make a change land once and retire most of the parity gate. It changes CLI behaviour (the CLI would inherit the server's verdict, including anchor verification that R-0032 left server-only), so it needs its own decision.

## Log

- 2026-09-29 new->later: the removal R-0002 could not do inside its three options (S029)
- 2026-09-29 later->next: triage 2026-09-29: user approved after R-0024/R-0034/R-0035
- 2026-09-29 next->now [idea->active]: Session S030: R-0036 closed; next lane in rank order.
- 2026-09-29 now->done: commit 1fa5047246e676513db52f8a769e8aeb9889e67c
