---
name: overview
type: overview
status: active
title: prompt-heaven Overview
description: What prompt-heaven is and a map to the documentation wiki.
sources:
  - README.md
  - ClaudeCode/CLAUDE.md
verified:
  commit: d6659f7
  date: 2026-07-16
links:
  - skills
  - agents
  - scripts
  - hooks
---

# prompt-heaven

A curated library of prompts, agents, skills, hooks, and MCP-server
scripts for Claude Code and OpenCode. It makes those tools more effective
through structured workflows, enforced MCP tool routing, and reusable
agent/skill definitions `README.md`.

The two tool targets live in parallel trees: `ClaudeCode/` is the primary
development target, `OpenCode/` mirrors a subset of it. All user-facing assets
(skills, agents) share the `p:` namespace prefix.

## Where things live

| Area | Path | Page |
|------|------|------|
| Skills (loadable knowledge packs + workflows) | `ClaudeCode/skills/` | [[skills]] |
| Minion agents (delegate-able sub-agents) | `ClaudeCode/agents/` | [[agents]] |
| Standalone scripts + MCP servers | `Scripts/` | [[scripts]] |
| Post-edit / session hooks | `ClaudeCode/hooks/` | [[hooks]] |

The former `/p:` slash-command tree (`ClaudeCode/commands/`) has been dissolved:
each command was migrated into a skill under `ClaudeCode/skills/` — see [[skills]].

## Entry points for a newcomer

- `README.md` — three-sentence project purpose.
- `ClaudeCode/CLAUDE.md` — the minion delegation table and coding mandates that
  govern every Claude Code session using this repo.
- `ClaudeCode/README.md` — the full plan -> task -> implement workflow.
- `Scripts/mcp-purity.py` — canonical example of the MCP-server pattern
  (single-tool dispatcher, JSON-RPC 2.0 over stdio).

## Cross-cutting conventions

- **`p:` prefix** on every skill / agent, in both tool trees.
- **MCP-server pattern**: each server exposes one dispatcher tool (`purity_call`,
  `clangd_call`, ...) routing to handlers via a `function` parameter; Python 3.9+
  with asyncio + JSON-RPC 2.0 over stdio.
- **Mandatory tool routing**: built-in Bash/Grep/Read are forbidden when an MCP
  covers the domain; enforced by agent prompts, tool descriptions, and the
  `ClaudeCode/hooks/attention-reminder.py` reminder hook.
- **`requirements.yaml` workflow**: plan -> `/p:task-plan` -> `/p:implement`,
  with the `Scripts/task-*.py` utilities operating on that YAML.
