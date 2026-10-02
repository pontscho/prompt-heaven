---
name: 0062-webfetch-save-to-may-write-git-and-claude-config
type: roadmap-item
status: active
title: R-0062 · webfetch save_to may write .git/ and Claude config inside the project root
description: Closed roadmap item R-0062 (done 2026-10-02).
id: R-0062
state: done
horizon: next
origin: agent:2026-10-02:r0054-security-triage
blocked_by: []
severity: high
tags: [scripts, security]
closed: 2026-10-02
commit: 2a795dbf078c4b16cb05f0598bfe4478c8f2915d
---

# R-0062 · webfetch save_to may write .git/ and Claude config inside the project root

## Why

mcp-webfetch save_to is contained only by a realpath prefix check against the project root (_resolve_save_path in Scripts/mcp-webfetch.py); nothing inside the root is denied. With overwrite=true and output=html/raw the saved bytes are byte-exact server content, so a prompt-injected page can stage a payload and ask the model to save it over .git/config (core.hooksPath, core.fsmonitor, core.sshCommand run on the next routine git command, no session restart needed), over an existing executable .git/hooks/* (open "w" keeps the mode), or into .claude/settings*.json, .claude/hooks/ or .mcp.json. Before R-0054 (9dd4f1a) the same class applied to ~/.claude for every project; R-0054 moved the root to the session project, which changed the blast radius rather than closing it. Found by the security triage of the R-0054 change (CWE-73 / CWE-15). Proposed fix: after the realpath containment, refuse any target under the project's .git/, .claude/settings*.json, .claude/hooks/ and .mcp.json -- for creation as well as overwrite, since creating .git/config or .mcp.json is already dangerous. Realpath-first means a symlink cannot route around it. Needs rows in tests/test_webfetch_roots.py, red first.

## Log

- 2026-10-02 new->next: security triage of R-0054; user approved next lane
- 2026-10-02 next->done: commit 2a795dbf078c4b16cb05f0598bfe4478c8f2915d
