---
name: skills
type: subsystem
status: current
title: Skills
description: Loadable knowledge packs activated on-demand or via Skill permissions.
sources:
  - ClaudeCode/skills
verified:
  commit: 51dd5f3
  date: 2026-05-27
links:
  - overview
  - agents
---

# Skills

Skills are loadable knowledge packs under `ClaudeCode/skills/`. Each skill is a
directory named `p:<name>` containing at minimum a `SKILL.md`; some bundle
helper scripts and reference files. A skill defines *rules and patterns* (how to
use a tool, a language convention, a workflow), as distinct from a command,
which defines an explicitly-invoked workflow — see [[overview]].

## Structure

- **Minimal skill**: a single `SKILL.md` — e.g. `ClaudeCode/skills/p:mcp-clangd/SKILL.md`.
- **Extended skill**: `SKILL.md` + scripts + reference — e.g.
  `ClaudeCode/skills/p:static-linking/` ships `SKILL.md`, `README.md`,
  `build-static.py`, `verify-static-linking.py`, `example-CMakeLists.txt`.

Frontmatter is required: `name` (matching the `p:`-prefixed directory) and a
`description` carrying both *what it does* and *when to trigger*. The
description is what Claude matches against to auto-activate the skill.

## Notable skills

| Skill | Purpose |
|-------|---------|
| `p:mcp-clangd` / `p:mcp-luals` / `p:mcp-cuda` | LSP code-intelligence routing for C/C++, Lua, CUDA |
| `p:mcp-purity` | File ops (search/glob/edit) routing |
| `p:mcp-forge` | `project-forge.yaml` build orchestration |
| `p:writer-skill` / `p:writer-agent` | Authoring new skills and agents |
| `p:wiki` | This documentation-wiki engine |
| `p:recap` | Session recap into the AI Soul memory system |

The MCP-routing skills (`p:mcp-*`) all forbid built-in tool fallback, mirroring
the mandate documented in [[overview]].
