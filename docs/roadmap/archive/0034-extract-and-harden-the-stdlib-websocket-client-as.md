---
name: 0034-extract-and-harden-the-stdlib-websocket-client-as
type: roadmap-item
status: active
title: R-0034 · Extract and harden the stdlib WebSocket client as a canonical source
description: Closed roadmap item R-0034 (done 2026-09-29).
id: R-0034
state: done
horizon: now
origin: user:2026-09-29:mcp-websocket-canonical-source
blocked_by: []
tags: [generated-regions, scripts, websocket]
closed: 2026-09-29
commit: fa347318298115006fde070992f22fd08dddaeec
---

# R-0034 · Extract and harden the stdlib WebSocket client as a canonical source

## Why

mcp-gdc carried a minimal stdlib RFC 6455 client, while the DDG cdp backend imported the third-party websocket-client for the same Chrome DevTools socket. The user asked to lift the gdc client into its own shared module, harden it (fragmentation, ping/pong, control-frame rules, size caps, strict handshake parse) and use it wherever a WebSocket client is needed, so the search script loses its dependency.

## Log

- 2026-09-29 new->now: added
- 2026-09-29 now->now [idea->active]: started
- 2026-09-29 now->done: commit fa347318298115006fde070992f22fd08dddaeec
