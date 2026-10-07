---
name: 0072-lift-the-stdlib-http-front-mcp-proxy-llm-router
type: roadmap-item
status: active
title: R-0072 · Lift the stdlib HTTP front (mcp-proxy, llm-router) into a canonical source
description: Closed roadmap item R-0072 (done 2026-10-07).
id: R-0072
state: done
horizon: later
origin: docs/adr/0028-route-by-model-translate-at-the-edge.md#the-http-front-is-a-copy
blocked_by: []
tags: [llm-router, mcp-proxy, shared-code]
closed: 2026-10-07
commit: 18f2b24a2beccf58aaf6c093361189d9bb095d5b
---

# R-0072 · Lift the stdlib HTTP front (mcp-proxy, llm-router) into a canonical source

## Why

The stdlib HTTP front (plan pieces P5 and P7-P17: the threading server, the header-deadline reader, the structure-only log line, the refusal, the single-header read, the precheck, the bounded body framing, the SSE relay, the value logger, the ready file and the token value) is now carried by two hosts, mcp-proxy and llm-router, as declared copies. ADR 0014 and ADR 0025 make a second carrier the revisit trigger for a canonical source, and ADR 0028 records the lift as deferred, not done.

The work is first a decision, then a move: what the domain of a canonical stdlib HTTP front is, and which of the divergences are parameters (mcp-proxy bridges into an asyncio core and answers empty-bodied refusals; the router is synchronous and answers Anthropic error envelopes). Until then the copy's drift risk is held by ADR 0028's copy table, by static case J1, and by this item. The verbatim copies kept their mcp-proxy names so the lift is a move without renames.

## Log

- 2026-10-05 new->later: recorded by ADR 0028
- 2026-10-07 later->later [idea->planned]: planned in requirements.yaml
- 2026-10-07 later->done: commit 18f2b24a2beccf58aaf6c093361189d9bb095d5b
