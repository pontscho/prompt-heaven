---
name: skills
type: subsystem
status: current
title: Skills
description: Loadable knowledge packs activated on-demand or via Skill permissions.
sources:
  - ClaudeCode/skills
verified:
  commit: 850c266
  date: 2026-07-16
links:
  - overview
  - agents
  - feature-implementation-plan
---

# Skills

Skills are loadable knowledge packs under `ClaudeCode/skills/`. Each skill is a
directory named `p:<name>` containing at minimum a `SKILL.md`; some bundle
helper scripts and reference files. A skill defines *rules and patterns* (how to
use a tool, a language convention, a workflow); the former `/p:` slash-commands
(`p:analyze`, `p:feature-plan`, `p:task-plan`, `p:implement`, `p:deep-research`,
`p:project-explore`, `p:checkpoint`, `p:spec-design`) were migrated into skills,
so skills now also carry the explicitly-invoked multi-step workflows — see
[[overview]].

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
| `p:wiki` | This documentation-wiki engine ([[wiki-engine]]) |
| `p:recap` | Session recap into the AI Soul memory system |
| `p:feature-plan` / `p:task-plan` / `p:implement` | Migrated `/p:` workflow chain: plan -> `requirements.yaml` -> execute |
| `p:code-review` / `p:branch-review` | Multi-lens code review (finder/verifier minion fan-out) — see [[feature-implementation-plan]] |

The MCP-routing skills (`p:mcp-*`) all forbid built-in tool fallback, mirroring
the mandate documented in [[overview]].
