---
name: commands
type: subsystem
status: current
title: Slash Commands
description: Explicitly-invoked /p: workflows that orchestrate multi-step tasks.
sources:
  - ClaudeCode/commands/p
verified:
  commit: 51dd5f3
  date: 2026-05-27
links:
  - overview
  - skills
  - agents
---

# Slash Commands

Commands under `ClaudeCode/commands/p/` are `/p:<name>` workflows the user
invokes explicitly. Unlike a skill (loaded as knowledge context, see [[skills]]),
a command defines an *executable multi-step workflow*.

## Roster

| Command | Purpose |
|---------|---------|
| `/p:feature-plan` | Interactive requirement gathering -> writes an implementation plan |
| `/p:task-plan` | Converts a planning discussion into `requirements.yaml` (structured task list) |
| `/p:implement` | Executes tasks from `requirements.yaml` autonomously |
| `/p:analyze` | Smart router: picks module / subsystem / API analysis |
| `/p:analyze-module` | Deep-dive on a single source file |
| `/p:analyze-subsystem` | Architecture analysis of a multi-module component |
| `/p:analyze-api` | Public API reference documentation |
| `/p:project-explore` | Quick project-structure overview |
| `/p:checkpoint` | Session checkpoint / resume |
| `/p:deep-research` | Comprehensive multi-angle web research |
| `/p:spec-design` | Specification / design document authoring |

## The core workflow

The primary chain is `/p:feature-plan` -> `/p:task-plan` (emits
`requirements.yaml`) -> `/p:implement`. The implement command delegates each
task to the `minion-mason` executor documented in [[agents]], and the `task-*.py`
utilities in [[scripts]] operate on the same `requirements.yaml`.
