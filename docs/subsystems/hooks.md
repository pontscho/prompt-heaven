---
name: hooks
type: subsystem
status: active
title: Post-edit Hooks
description: Claude Code hooks that auto-run after edits to enforce quality and tool routing.
sources:
  - ClaudeCode/hooks
verified:
  commit: 1446acb
  date: 2026-08-08
links:
  - overview
  - 0005-approve-the-wrapper-not-the-command
---

# Post-edit Hooks

The scripts under `ClaudeCode/hooks/` are Claude Code hooks wired into a
project's `.claude/settings.json`. Most run after a file edit to enforce code
quality; others fire on tool use, prompt submit, or session start to keep the
session's tool routing on track and inject fresh context (wall-clock time, host
identity).

## Roster

| Hook | Lang | Trigger | Action |
|------|------|---------|--------|
| `attention-reminder.py` | Python | PreToolUse / UserPromptSubmit | Emits a per-token-bucket reminder listing the active MCP servers |
| `mcp-first-guard.py` | Python | PreToolUse / matcher `Bash` | MCP-first routing guard: denies file-search / read / listing / inspection binaries as a *primary* Bash command, steering to the purity/inspect MCP equivalents; sees through wrappers and command substitution (`deny`\|empty polarity) |
| `sbx-gate.py` | Python | PreToolUse / matcher `Bash` | Grant-only gate: auto-allows a clean, contained, single `sbx` invocation (canonical-path identity, metacharacter-rejecting) and stays silent otherwise (`allow`\|empty polarity — disjoint from `mcp-first-guard.py`, so their order is safe either way). Its accept-set left of the bare `--` is closed `ClaudeCode/hooks/sbx-gate.py:is_clean_sbx`: `--ro` and `--dry-run` argument-less (`--dry-run` execs no child, so it grants strictly less than a plain run), `--write DIR`/`--write=DIR` only if the scope resolves inside the project root, `--net` refused, and every other token — including the equals-form `--dry-run=1` — a hard prompt. Rationale: [[0005-approve-the-wrapper-not-the-command]] |
| `post-edit-clang-format.sh` | Bash | edit `.c/.cpp/.h/.hpp` | Auto-formats via clang-format |
| `post-edit-clang-tidy.sh` | Bash | edit `.c/.cpp/.h/.hpp` | Runs clang-tidy; blocks on warnings |
| `post-edit-json-lint.sh` | Bash | edit `.json/.jsonc/...` | Validates JSON syntax |
| `post-edit-python-lint.sh` | Bash | edit `.py` | Python linting |
| `post-edit-vue-lint.sh` | Bash | edit `.vue/.js/.ts` | Auto-detects Biome / oxlint / ESLint |
| `post-edit-lint.sh` | Bash | edit (generic) | Generic lint dispatch |
| `prompt-inject-time.sh` | Bash | UserPromptSubmit | Injects fresh wall-clock time into context each prompt |
| `session-start-host-info.sh` | Bash | SessionStart | Injects host identity (hostname, arch, distro, CPU/RAM) once per session |

## The complex one

`attention-reminder.py` is the most involved: it reads the session transcript,
computes token buckets, runs `claude mcp list`, cross-checks child PIDs to
filter session-disabled MCP servers, and emits an `additionalContext` payload
into Claude's context. It is the enforcement layer behind the mandatory
tool-routing convention described in [[overview]].
