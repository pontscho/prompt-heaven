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
  commit: 9eeb66c
  date: 2026-08-10
links:
  - skills
  - agents
  - scripts
  - hooks
  - tests
  - wiki-engine
  - layer-contract
  - requirements-yaml
---

# prompt-heaven

A curated library of prompts, agents, skills, hooks, and MCP-server
scripts for Claude Code and OpenCode. It makes those tools more effective
through structured workflows, enforced MCP tool routing, and reusable
agent/skill definitions `README.md`.

The two tool targets live in parallel trees: `ClaudeCode/` is the primary
development target, `OpenCode/` mirrors a subset of it. Every skill and agent is
invoked under a `p:` prefix, but that prefix is part of neither the directory
name nor the frontmatter `name:` — the plugin manifest supplies it at invocation
time `ClaudeCode/.claude-plugin/plugin.json`.

## Where things live

| Area | Path | Page |
|------|------|------|
| Skills (loadable knowledge packs + workflows) | `ClaudeCode/skills/` | [[skills]] |
| Minion agents (delegate-able sub-agents) | `ClaudeCode/agents/` | [[agents]] |
| Standalone scripts + MCP servers | `Scripts/` | [[scripts]] |
| Post-edit / session hooks | `ClaudeCode/hooks/` | [[hooks]] |
| Functional test fleet (stdlib-only, `python3 tests/run.py`) | `tests/` | [[tests]] |
| Documentation wiki engine (skill + MCP server + librarian) | `ClaudeCode/skills/wiki/`, `Scripts/mcp-wiki.py` | [[wiki-engine]] |
| Layer contract: what may be a skill, an agent, or a fragment | `ClaudeCode/ARCHITECTURE.md` | [[layer-contract]] |

The former `/p:` slash-command tree (`ClaudeCode/commands/`) has been dissolved:
each command was migrated into a skill under `ClaudeCode/skills/` — see [[skills]].

## Entry points for a newcomer

- `README.md` — three-sentence project purpose.
- `ClaudeCode/CLAUDE.md` — the minion delegation table and coding mandates that
  govern every Claude Code session using this repo.
- `ClaudeCode/README.md` — the full plan -> task -> implement workflow.
- `ClaudeCode/ARCHITECTURE.md` — the canonical rulebook for the three layers and
  the depth-2 sub-agent nesting ceiling; the reasoning behind it is
  [[layer-contract]].
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
  with the `Scripts/task-*.py` utilities operating on that YAML — see
  [[requirements-yaml]].
