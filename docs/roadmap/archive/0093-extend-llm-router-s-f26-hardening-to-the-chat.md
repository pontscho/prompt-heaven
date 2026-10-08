---
name: 0093-extend-llm-router-s-f26-hardening-to-the-chat
type: roadmap-item
status: active
title: R-0093 · Extend llm-router's F26 hardening to the chat-completions and Mistral kinds
description: Closed roadmap item R-0093 (done 2026-10-08).
id: R-0093
state: done
horizon: unset
origin: user:2026-10-07:router-f26-chat-mistral
blocked_by: []
severity: low
tags: [hardening, llm-router]
closed: 2026-10-08
commit: 51eced1585493c295917acc99e553d1d804a6163
---

# R-0093 · Extend llm-router's F26 hardening to the chat-completions and Mistral kinds

## Why

R-0085 batch 2 (225466c) shipped F26 for the Responses kinds (codex, openai) only; the builder left the chat-completions path and the Mistral adapter uncovered, and that choice was accepted overnight as an agent decision. Open: apply the same rule to the remaining kinds with a red-first router case per kind, or declare why they do not need it.

## Log

- 2026-10-08 new->unset: proposed by p:minion-builder
- 2026-10-08 unset->done: commit 51eced1585493c295917acc99e553d1d804a6163
