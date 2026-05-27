---
name: overview
type: overview
status: current
title: prompt-heaven Overview
description: What prompt-heaven is and a map to the documentation wiki.
sources:
  - README.md
  - ClaudeCode/CLAUDE.md
verified:
  commit: 51dd5f3
  date: 2026-05-27
links:
  - skills
  - agents
  - scripts
  - commands
  - hooks
---

# prompt-heaven

A curated library of prompts, agents, commands, hooks, skills, and MCP-server
scripts for Claude Code and OpenCode. It makes those tools more effective
through structured workflows, enforced MCP tool routing, and reusable
agent/skill definitions `README.md`.

The two tool targets live in parallel trees: `ClaudeCode/` is the primary
development target, `OpenCode/` mirrors a subset of it. All user-facing assets
(skills, agents, commands) share the `p:` namespace prefix.

## Where things live

| Area | Path | Page |
|------|------|------|
| Skills (loadable knowledge packs) | `ClaudeCode/skills/` | [[skills]] |
| Minion agents (delegate-able sub-agents) | `ClaudeCode/agents/p/` | [[agents]] |
| Standalone scripts + MCP servers | `Scripts/` | [[scripts]] |
| Slash commands (multi-step workflows) | `ClaudeCode/commands/p/` | [[commands]] |
| Post-edit quality hooks | `ClaudeCode/hooks/` | [[hooks]] |

## Entry points for a newcomer

- `README.md` — three-sentence project purpose.
- `ClaudeCode/CLAUDE.md` — the minion delegation table and coding mandates that
  govern every Claude Code session using this repo.
- `ClaudeCode/README.md` — the full plan -> task -> implement workflow.
- `Scripts/mcp-purity.py` — canonical example of the MCP-server pattern
  (single-tool dispatcher, JSON-RPC 2.0 over stdio).

## Cross-cutting conventions

- **`p:` prefix** on every skill / agent / command, in both tool trees.
- **MCP-server pattern**: each server exposes one dispatcher tool (`purity_call`,
  `clangd_call`, ...) routing to handlers via a `function` parameter; Python 3.9+
  with asyncio + JSON-RPC 2.0 over stdio.
- **Mandatory tool routing**: built-in Bash/Grep/Read are forbidden when an MCP
  covers the domain; enforced by agent prompts, tool descriptions, and the
  `ClaudeCode/hooks/attention-reminder.py` reminder hook.
- **`requirements.yaml` workflow**: plan -> `/p:task-plan` -> `/p:implement`,
  with the `Scripts/task-*.py` utilities operating on that YAML.
