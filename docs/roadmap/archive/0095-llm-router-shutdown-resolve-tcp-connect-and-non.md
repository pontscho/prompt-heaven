---
name: 0095-llm-router-shutdown-resolve-tcp-connect-and-non
type: roadmap-item
status: active
title: R-0095 · llm-router shutdown: resolve, TCP connect and non-stream upstream calls are not interruptible
description: Closed roadmap item R-0095 (done 2026-10-08).
id: R-0095
state: done
horizon: unset
origin: user:2026-10-07:router-shutdown-interrupt-remainder
blocked_by: []
follows: R-0083
severity: low
tags: [llm-router, shutdown]
closed: 2026-10-08
commit: 51eced1585493c295917acc99e553d1d804a6163
---

# R-0095 · llm-router shutdown: resolve, TCP connect and non-stream upstream calls are not interruptible

## Why

R-0083 (0267c3b) made a token POST inside _rt_send interruptible on shutdown: the TLS socket is registered before the handshake and the flock poll gives up. Two gaps remain: the name resolution and the TCP connect happen before the socket is registered, so a shutdown during a slow resolve or connect waits for its timeout; and non-stream upstream calls are still not interruptible. Open: register earlier (or connect through a socket the shutdown path can close) and cover the non-stream path, each with a red-first case.

## Log

- 2026-10-08 new->unset: proposed by p:minion-builder
- 2026-10-08 unset->done: commit 51eced1585493c295917acc99e553d1d804a6163
