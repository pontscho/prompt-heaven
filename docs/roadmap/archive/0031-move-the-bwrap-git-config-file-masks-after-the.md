---
name: 0031-move-the-bwrap-git-config-file-masks-after-the
type: roadmap-item
status: active
title: R-0031 · Move the bwrap .git/config file masks after the shadow binds too
description: Closed roadmap item R-0031 (done 2026-09-28).
id: R-0031
state: done
horizon: now
origin: user:2026-09-28:bwrap-shadow-bind-buries-git-config-mask
blocked_by: []
severity: low
tags: [sandbox, security]
closed: 2026-09-28
commit: 677c7f85bdb683b618401d5b167d546df9db6064
---

# R-0031 · Move the bwrap .git/config file masks after the shadow binds too

## Why

Found while fixing R-0005. In the bwrap builder of sbx the shadow ro-binds come after the .git/config file masks and take their source from the untouched host, so if a symlink directly under ~/.claude or a carve-out resolves to a directory holding a checkout's .git/config, the shadow mount buries that mask exactly as the carve-out did. Seatbelt is not affected (its shadow rules are write-only). Not reachable with this host's current links. Fix: emit the file masks after the shadow binds as well, with an offline argv-order case in sbx_gate group Q.

## Log

- 2026-09-28 new->next: found by the R-0005 fix (S028)
- 2026-09-28 next->now [idea->active]: the agent that fixed R-0005 still holds the sbx context
- 2026-09-28 now->done: commit 677c7f85bdb683b618401d5b167d546df9db6064
