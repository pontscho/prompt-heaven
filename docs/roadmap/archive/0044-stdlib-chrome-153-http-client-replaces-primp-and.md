---
name: 0044-stdlib-chrome-153-http-client-replaces-primp-and
type: roadmap-item
status: active
title: R-0044 · Stdlib Chrome 153 HTTP client replaces primp and curl_cffi in the search scripts and webfetch
description: Closed roadmap item R-0044 (done 2026-10-02).
id: R-0044
state: done
horizon: now
origin: docs/feature-implementation-plan.md
blocked_by: []
tags: [scripts, search, security]
closed: 2026-10-02
commit: 3fbe5bf10074cea49a417663c8d826d2a4571f3a
---

# R-0044 · Stdlib Chrome 153 HTTP client replaces primp and curl_cffi in the search scripts and webfetch

## Why

R-0017 measured fixed primp tells no version string fixes and a curl_cffi that lags Chrome; the pure-Python PoC reproduced Chrome 153's JA4 on loopback and got one DDG lite result page live (a single sample, spec-ddg section 2.9). Porting it into three generated canonical sources (_mcp_chrome, _mcp_brotli, _mcp_zstd) removes both third-party backends and closes webfetch's redirect SSRF hole.

Live evidence, as spec-ddg section 2.9 records it: the verified transport got 10 DDG results without a block in two live samples (Gate G6 on 2026-09-30, task-038; the cost run on 2026-10-01, task-044). The DDG challenge page was never observed, so the block predicate that shipped is the plan's fallback, not the structural match. Two samples from one IP do not show that DDG has stopped blocking.

## Log

- 2026-09-30 new->now [idea->planned]: User 2026-09-30: continuation of R-0017, planned via p:feature-plan and p:task-plan.
- 2026-10-01 now->now: edited why: why stated the live evidence spec-ddg 2.9 records instead of passed live DDG (task-056)
- 2026-10-02 now->done: commit 3fbe5bf10074cea49a417663c8d826d2a4571f3a
