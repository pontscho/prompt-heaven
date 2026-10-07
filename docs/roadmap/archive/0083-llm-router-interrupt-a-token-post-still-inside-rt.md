---
name: 0083-llm-router-interrupt-a-token-post-still-inside-rt
type: roadmap-item
status: active
title: R-0083 · llm-router: interrupt a token POST still inside _rt_send on shutdown
description: Closed roadmap item R-0083 (done 2026-10-07).
id: R-0083
state: done
horizon: unset
origin: user:2026-10-07:llm-router-shutdown-token-post
blocked_by: []
tags: [llm-router]
closed: 2026-10-07
commit: 0267c3bc227b2fc2609d6d36d1f27f28bbc57ab8
---

# R-0083 · llm-router: interrupt a token POST still inside _rt_send on shutdown

## Why

Out of scope for the codex/openai feature and declared (limit 6 of the ADR 0028 addendum). Only the body read after _rt_send returns is registered in srv.inflight. A refresh POST still in connect, TLS, request write or response head is not, so a shutdown during that phase waits up to connect_timeout plus _RT_OAUTH_IDLE_S (15 s per read) and the drain, after _DRAIN_S (3 s), abandons the thread. A handler polling the config flock can outlive the drain the same way.

Registering the socket earlier needs a hook inside _rt_send, the single framing owner that static rule J4a pins, so the fix includes a J4a amendment with a planted negative control that still fires.

## Log

- 2026-10-07 new->unset: proposed by llm-router codex/openai plan Step 18 (task-060)
- 2026-10-07 unset->done: commit 0267c3bc227b2fc2609d6d36d1f27f28bbc57ab8
