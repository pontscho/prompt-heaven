---
name: 0045-jenkins-read-only-for-watson-and-quint-is-enforced
type: roadmap-item
status: active
title: R-0045 · Jenkins read-only for Watson and Quint is enforced only in prompt text, and the bans miss the server's aliases
description: Closed roadmap item R-0045 (dropped 2026-10-02).
id: R-0045
state: dropped
horizon: unset
origin: peer review by session tampermonkey-72, 2026-09-30
blocked_by: []
tags: [agents, mcp, security]
closed: 2026-10-02
reason: User 2026-10-02: closable; prompt-text read-only for Watson and Quint is accepted.
---

# R-0045 · Jenkins read-only for Watson and Quint is enforced only in prompt text, and the bans miss the server's aliases

## Why

5f419b3 made Jenkins read-only for p:minion-watson and p:minion-bug-hunter by prompt text alone; the ban lists (minion-watson.md:49, minion-bug-hunter.md:167) name start_build/cancel_build/replay_build/run_and_wait but not the aliases Scripts/mcp-jenkins.py accepts (build, trigger, replay; reported near :2551-2572, not yet re-verified). A server-side read-only mode or a per-agent function allowlist would make the commit title true.

## Log

- 2026-09-30 new->unset: User 2026-09-30: approved filing the peer review findings outside R-0044 scope.
- 2026-10-02 unset->dropped: User 2026-10-02: closable; prompt-text read-only for Watson and Quint is accepted.
