---
name: 0035-pure-python-3-9-and-stdlib-no-traceback-for-a
type: roadmap-item
status: active
title: R-0035 · Pure Python 3.9 and stdlib: no traceback for a missing package
description: Closed roadmap item R-0035 (done 2026-09-29).
id: R-0035
state: done
horizon: now
origin: user:2026-09-29:pure-python-39-stdlib
blocked_by: []
tags: [dependencies, scripts, tests]
closed: 2026-09-29
commit: 59bd5d94df4538940b33b8562b5e4768cce9d953
---

# R-0035 · Pure Python 3.9 and stdlib: no traceback for a missing package

## Why

A missing third-party package surfaced as a raw Python traceback. The user set the rule: fleet scripts are pure Python 3.9 on the stdlib, a third-party module is allowed only when no stdlib way exists, and an absent one fails in one line. lxml goes, primp/curl_cffi and PyYAML are guarded with find_spec, a gate keeps the rule, and mcp-webfetch keeps bs4, markdownify and uv as a declared temporary exception.

## Log

- 2026-09-29 new->now: added
- 2026-09-29 now->now [idea->active]: started
- 2026-09-29 now->done: commit 59bd5d94df4538940b33b8562b5e4768cce9d953
