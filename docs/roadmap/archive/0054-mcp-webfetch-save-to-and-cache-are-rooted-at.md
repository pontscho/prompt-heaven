---
name: 0054-mcp-webfetch-save-to-and-cache-are-rooted-at
type: roadmap-item
status: active
title: R-0054 · mcp-webfetch save_to and cache are rooted at ~/.claude, not the session's project
description: Closed roadmap item R-0054 (done 2026-10-02).
id: R-0054
state: done
horizon: later
origin: user:2026-10-01:webfetch-save-to-root
blocked_by: []
severity: low
tags: [scripts]
closed: 2026-10-02
commit: 9dd4f1aae1dcd5d187bce87cd73acac332be6022
---

# R-0054 · mcp-webfetch save_to and cache are rooted at ~/.claude, not the session's project

## Why

save_to is contained in the server's project_root (_resolve_save_path in Scripts/mcp-webfetch.py: a relative path is joined to it, anything resolving outside it is refused), and the disk cache lives under the same root. That root is fixed at launch by --project-root (default: the cwd) and handle_fetch has no per-call or per-session project. The server is registered at user scope, and the launch line recorded in docs/subsystems/scripts.md passes --project-root ~/.claude, so in every project a save_to lands under ~/.claude, a relative path such as docs/x.md is written to ~/.claude/docs/x.md, a path in the actual project is refused as outside the root, and the webfetch cache of every project is shared under ~/.claude/.cache/webfetch. The registration itself lives in ~/.claude.json, outside the tree, and was not re-read here. The fix needs a per-session project root (for example MCP roots or the client's working directory) rather than a launch-time constant, without loosening the confused-deputy containment.

## Log

- 2026-10-01 new->unset: found during R-0044 (task-056)
- 2026-10-02 unset->later: Triage 2026-10-02: the shared cache is intended (user); save_to's root is the remaining half, unused so far -- pull it up when it bites.
- 2026-10-02 later->done: commit 9dd4f1aae1dcd5d187bce87cd73acac332be6022
