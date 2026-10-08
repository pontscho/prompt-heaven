---
name: 0094-wiki-call-verify-does-not-resolve-dotted-class
type: roadmap-item
status: active
title: R-0094 · wiki_call verify does not resolve dotted Class.method anchors
description: Closed roadmap item R-0094 (done 2026-10-08).
id: R-0094
state: done
horizon: unset
origin: user:2026-10-07:wiki-verify-dotted-anchors
blocked_by: []
severity: low
tags: [wiki]
closed: 2026-10-08
commit: 62ccda7df06e77943af9d02889ee8062f0fd4ef3
---

# R-0094 · wiki_call verify does not resolve dotted Class.method anchors

## Why

The verify symbol matcher looks up a path:symbol anchor as a bare name, so an anchor naming a method as Class.method is never found and is reported as a frozen advisory although the method exists. Observed on ADR 0028 during R-0072: two false advisories, for _RouterHandler.send_error and _RouterHandler._rt_client_gone. Open: resolve a dotted symbol as a member of the named class (Python first), with a red-first wiki case.

## Log

- 2026-10-08 new->unset: proposed by Safranek
- 2026-10-08 unset->done: commit 62ccda7df06e77943af9d02889ee8062f0fd4ef3
