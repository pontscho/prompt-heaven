---
name: 0028-give-roadmap-py-a-start-command-so-starting-an
type: roadmap-item
status: active
title: R-0028 · Give roadmap.py a start command so starting an item is one call
description: Closed roadmap item R-0028 (done 2026-09-28).
id: R-0028
state: done
horizon: now
origin: user:2026-09-28:roadmap-start-command
blocked_by: []
tags: [roadmap]
closed: 2026-09-28
commit: 4442275185147432bdb087a656e067b98d107348
---

# R-0028 · Give roadmap.py a start command so starting an item is one call

## Why

Starting an item today takes a staged reason file plus a move to now with state active, for a reason the agent writes itself and that carries no harvested prose. A start command with a built-in reason (optional --reason-file override) makes it one call and keeps the WIP cap check. A --reason-stdin flag was rejected: from Bash it needs a heredoc, which puts the text back on the shell line the staging rule keeps it off.

## Log

- 2026-09-28 new->now [idea->active]: user asked to script the hand steps (S028)
- 2026-09-28 now->done: commit 4442275185147432bdb087a656e067b98d107348
