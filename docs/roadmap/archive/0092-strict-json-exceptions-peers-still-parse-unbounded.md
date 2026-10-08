---
name: 0092-strict-json-exceptions-peers-still-parse-unbounded
type: roadmap-item
status: active
title: R-0092 · STRICT_JSON_EXCEPTIONS peers still parse unbounded ints and NaN on 3.9.6
description: Closed roadmap item R-0092 (done 2026-10-08).
id: R-0092
state: done
horizon: unset
origin: user:2026-10-07:strict-json-exception-peers
blocked_by: []
severity: low
tags: [fleet, hardening, json]
closed: 2026-10-08
commit: 86e19281339d1676bd0a058581a96e61b1a66ae1
---

# R-0092 · STRICT_JSON_EXCEPTIONS peers still parse unbounded ints and NaN on 3.9.6

## Why

R-0067/R-0068 (d4a241b) made the MCP wire strict: NaN/Infinity and integers longer than JSON_INT_LITERAL_LIMIT (4300 digits) are refused by the generated _strict_loads in all 17 servers and the proxy. The generated_region group H declares 21 STRICT_JSON_EXCEPTIONS -- parse sites that read a peer other than the MCP client: LSP bodies, the context7 and jenkins HTTP responses, the gdc CDP socket, inspect's _v_json. On Python 3.9.6 those sites still use plain json.loads, which accepts NaN and parses an integer literal of any length (3.9.6 predates the int-to-str digit limit), so a hostile or broken peer can still cost quadratic CPU or inject a non-finite number. Open: decide per exception whether it should move to _strict_loads, and gate the decision red first. Residual of the S061 security triage of d4a241b.

## Log

- 2026-10-08 new->unset: proposed by p:minion-inspector-security-officer
- 2026-10-08 unset->done: commit 86e19281339d1676bd0a058581a96e61b1a66ae1
