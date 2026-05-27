---
name: scripts
type: subsystem
status: current
title: Scripts & MCP Servers
description: Standalone Python scripts -- MCP servers and requirements.yaml task utilities.
sources:
  - Scripts
verified:
  commit: 51dd5f3
  date: 2026-05-27
links:
  - overview
---

# Scripts & MCP Servers

`Scripts/` holds standalone Python 3.9+ scripts in three groups. They are
deployed to `~/.claude/scripts/` or run directly.

## MCP servers

Each server exposes its capability to Claude Code via a single dispatcher tool
routing to internal handlers by a `function` parameter — the pattern is best
seen in `Scripts/mcp-purity.py`. All use asyncio + JSON-RPC 2.0 over stdio.

| Script | Server | Domain |
|--------|--------|--------|
| `Scripts/mcp-purity.py` | mcp-purity | File ops: list, search, read, write |
| `Scripts/mcp-clangd.py` | mcp-clangd | C/C++ LSP intelligence |
| `Scripts/mcp-lua-lsp.py` | mcp-luals | Lua LSP intelligence |
| `Scripts/mcp-cuda.py` | mcp-cuda | CUDA symbol navigation |
| `Scripts/mcp-forge.py` | mcp-forge | `project-forge.yaml` build targets |
| `Scripts/mcp-git.py` | mcp-git | Git operations |
| `Scripts/mcp-lldb.py` | mcp-lldb | LLDB debugger integration |
| `Scripts/mcp-webfetch.py` | mcp-webfetch | Browser-emulated URL fetching |
| `Scripts/mcp-context7.py` | mcp-context7 | Context7 documentation lookup |

Other servers: `mcp-compile.py`, `mcp-tshark.py`, `mcp-jenkins.py`, `mcp-gdc.py`.

## Task utilities

Operate on the `requirements.yaml` workflow (see [[overview]]):
`Scripts/task-plan.py` (status + dependency analysis), `task-update.py`,
`task-show-all.py`, `task-show-details.py`, `task-batch-planner.py`,
`task-implementation-plan.py` (token-efficient plan extraction).

## Search

`Scripts/search_duckduckgo.py` (DDG-first with Bing fallback, browser
impersonation via primp/curl_cffi) and `Scripts/search_github.py` (code search
via grep.app). The DDG bot-detection research log is documented in [[spec-ddg]].
