---
name: 0068-mcp-proxy-guard-huge-integer-literals-on-python
type: roadmap-item
status: active
title: R-0068 · mcp-proxy: guard huge integer literals on Python before 3.9.14
description: Closed roadmap item R-0068 (done 2026-10-07).
id: R-0068
state: done
horizon: unset
origin: user:2026-10-03:mcp-proxy-int-digit-guard
blocked_by: []
severity: low
tags: [scripts, security]
closed: 2026-10-07
commit: d4a241bf8102c57ca657f524d73e81a5520d1d0b
---

# R-0068 · mcp-proxy: guard huge integer literals on Python before 3.9.14

## Why

Security review round 1, finding F20 (verified LOW, CWE-400), and round 2 F5. The CVE-2020-10735 int-digit limit exists only from Python 3.9.14; the macOS system 3.9.6 parses a multi-megabyte digit run at quadratic cost. Today this is a declared limit in the module docstring (HTTP body bounded by --max-body-bytes, stdin is the trusted parent). A pre-parse digit-run cap would lift it for every fleet server, so it may belong in a shared canonical source.

Update 2026-10-07: the llm-router security review of 2026-10-07 (finding F9, verified LOW) found the same gap in llm-router and the _mcp_oauth canonical source, and it was fixed there: _rt_loads and _oauth_json_object now refuse an integer literal longer than 4300 characters (static case J31). That makes two more carriers of the same guard, which strengthens the case for one shared copy; mcp-proxy still has none.

## Log

- 2026-10-03 new->unset: proposed by p:security-review
- 2026-10-07 unset->unset: edited why: extended with the llm-router F9 fix (security review 2026-10-07)
- 2026-10-07 unset->done: commit d4a241bf8102c57ca657f524d73e81a5520d1d0b
