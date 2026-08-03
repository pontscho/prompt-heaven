---
name: scripts
type: subsystem
status: active
title: Scripts & MCP Servers
description: Standalone Python scripts -- MCP servers and requirements.yaml task utilities.
sources:
  - Scripts
verified:
  commit: 5523dc6
  date: 2026-08-03
links:
  - overview
  - 0001-purity-server-unification
---

# Scripts & MCP Servers

`Scripts/` holds standalone Python 3.9+ scripts in three groups. They are
deployed to `~/.claude/scripts/` or run directly.

## MCP servers

Each server exposes its capability to Claude Code via a single dispatcher tool
routing to internal handlers by a `function` parameter — the pattern is best
seen in `Scripts/mcp-purity.py`. All use asyncio + JSON-RPC 2.0 over stdio.
The decision to fold `mcp-clangd` and `mcp-cuda` into `mcp-purity` behind the
`purity_call` entry point is recorded in [[0001-purity-server-unification]].

| Script | Server | Domain |
|--------|--------|--------|
| `Scripts/mcp-purity.py` | mcp-purity | File ops: list, search, read, write |
| `Scripts/mcp-forge.py` | mcp-forge | `project-forge.yaml` build targets |
| `Scripts/mcp-git.py` | mcp-git | Git operations |
| `Scripts/mcp-lldb.py` | mcp-lldb | LLDB debugger integration |
| `Scripts/mcp-context7.py` | mcp-context7 | Context7 documentation lookup |
| `Scripts/mcp-wiki.py` | mcp-wiki | Wiki freshness / reindex / search / page reads over `docs/` |
| `Scripts/mcp-inspect.py` | mcp-inspect | Read-only host/process/network inspection, file digests, syntax validation |

Superseded, not registered — their capabilities were folded into `purity_call`,
which is the live route for all three: `mcp-clangd.py` (C/C++), `mcp-lua-lsp.py`
(Lua, via the `luals_*` functions) and `mcp-cuda.py` (CUDA). The tools
`clangd_call`, `luals_call` and `cuda_call` are not registered and cannot be
called `Scripts/_mcp_smoke_test.py`; see [[0001-purity-server-unification]].

Present but not registered: `mcp-webfetch.py` `Scripts/_mcp_smoke_test.py`. It
still speaks the protocol but is not wired into Claude Code, and no decision on
record explains why.

Other servers: `mcp-tshark.py`, `mcp-jenkins.py`, `mcp-gdc.py`,
`mcp-postgres.py`.

## Task utilities

Operate on the `requirements.yaml` workflow (see [[overview]]):
`Scripts/task-plan.py` (status + dependency analysis), `task-update.py`,
`task-show-all.py`, `task-show-details.py`, `task-batch-planner.py`,
`task-implementation-plan.py` (token-efficient plan extraction), and
`task-validator.py` (requirements.yaml schema validation).

## Search

`Scripts/search_duckduckgo.py` (DDG-first with Bing fallback, browser
impersonation via primp/curl_cffi) and `Scripts/search_github.py` (code search
via grep.app). The DDG bot-detection research log is documented in [[spec-ddg]].
