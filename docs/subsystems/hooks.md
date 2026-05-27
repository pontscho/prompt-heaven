---
name: hooks
type: subsystem
status: current
title: Post-edit Hooks
description: Claude Code hooks that auto-run after edits to enforce quality and tool routing.
sources:
  - ClaudeCode/hooks
verified:
  commit: 51dd5f3
  date: 2026-05-27
links:
  - overview
---

# Post-edit Hooks

The scripts under `ClaudeCode/hooks/` are Claude Code hooks wired into a
project's `.claude/settings.json`. Most run after a file edit to enforce code
quality; one runs before tool use / on prompt submit to keep the session's tool
routing on track.

## Roster

| Hook | Lang | Trigger | Action |
|------|------|---------|--------|
| `attention-reminder.py` | Python | PreToolUse / UserPromptSubmit | Emits a per-token-bucket reminder listing the active MCP servers |
| `post-edit-clang-format.sh` | Bash | edit `.c/.cpp/.h/.hpp` | Auto-formats via clang-format |
| `post-edit-clang-tidy.sh` | Bash | edit `.c/.cpp/.h/.hpp` | Runs clang-tidy; blocks on warnings |
| `post-edit-json-lint.sh` | Bash | edit `.json/.jsonc/...` | Validates JSON syntax |
| `post-edit-python-lint.sh` | Bash | edit `.py` | Python linting |
| `post-edit-vue-lint.sh` | Bash | edit `.vue/.js/.ts` | Auto-detects Biome / oxlint / ESLint |
| `post-edit-lint.sh` | Bash | edit (generic) | Generic lint dispatch |

## The complex one

`attention-reminder.py` is the most involved: it reads the session transcript,
computes token buckets, runs `claude mcp list`, cross-checks child PIDs to
filter session-disabled MCP servers, and emits an `additionalContext` payload
into Claude's context. It is the enforcement layer behind the mandatory
tool-routing convention described in [[overview]].
