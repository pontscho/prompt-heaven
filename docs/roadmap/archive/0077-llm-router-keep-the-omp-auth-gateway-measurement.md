---
name: 0077-llm-router-keep-the-omp-auth-gateway-measurement
type: roadmap-item
status: active
title: R-0077 · llm-router: keep the omp auth-gateway measurement as a record (rejected passthrough route)
description: Closed roadmap item R-0077 (done 2026-10-07).
id: R-0077
state: done
horizon: unset
origin: user:2026-10-07:llm-router-omp-gateway-measurement
blocked_by: []
tags: [llm-router]
closed: 2026-10-07
commit: 2ef3ef97a65863fd7e1659ab882c32bf608f26fa
---

# R-0077 · llm-router: keep the omp auth-gateway measurement as a record (rejected passthrough route)

## Why

Before the native codex and openai kinds were built, routing through the omp auth gateway with the existing passthrough kind was measured on 2026-10-06 and rejected: count_tokens returned 404, thinking was dropped silently, cache tokens were always 0, the streamed input_tokens was 0, and tool ids looked like call_...|fc_.... The feature plan (rejected alternative, plan Step 18 item a) keeps it only as a measurement record, also summarised in the ADR 0028 addendum.

This item holds that record so a later proposal to route through the gateway starts from the measured facts. Re-measure the five points before reconsidering it; drop the item once the native kinds make the question moot.

## Log

- 2026-10-07 new->unset: proposed by llm-router codex/openai plan Step 18 (task-060)
- 2026-10-07 unset->done: commit 2ef3ef97a65863fd7e1659ab882c32bf608f26fa
