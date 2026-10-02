---
name: 0057-single-pass-htmlparser-for-ddg-lite-bing-and-grep
type: roadmap-item
status: active
title: R-0057 · Single-pass HTMLParser for DDG lite, Bing and grep.app result parsing
description: Closed roadmap item R-0057 (done 2026-10-02).
id: R-0057
state: done
horizon: later
origin: user:2026-10-01:search-single-pass-parser
blocked_by: []
severity: low
tags: [search, security]
closed: 2026-10-02
commit: a8c3443d856fd3e8f3a5646e4f53bd0ab73cc760
---

# R-0057 · Single-pass HTMLParser for DDG lite, Bing and grep.app result parsing

## Why

Security review 20261001-082224, finding F32 (LOW): the DDG lite and grep.app result parsers are regex findall passes that go super-linear on a hostile body, which the endpoint or a MITM on the unverified Chrome fallback controls. The fix pass capped search bodies at 2 MiB and made DDG parse once, which bounds the cost but keeps the shape. A single-pass html.parser walk (the Bing parser already is one) would make the parsing linear.

## Log

- 2026-10-01 new->unset: deferred by security review 20261001-082224
- 2026-10-02 unset->later: Triage 2026-10-02: the 2 MiB body cap and the single DDG parse already bound the cost.
- 2026-10-02 later->done: commit a8c3443d856fd3e8f3a5646e4f53bd0ab73cc760
