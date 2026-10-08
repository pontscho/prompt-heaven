---
name: hooks
type: subsystem
status: active
title: Post-edit Hooks
description: Claude Code hooks in ClaudeCode/hooks -- post-edit formatters and linters, PreToolUse routing and sandbox gates, and prompt/session context injectors.
sources:
  - ClaudeCode/hooks
verified:
  commit: da3f746
  date: 2026-10-08
links:
  - overview
  - scripts
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
| `attention-reminder.py` | Python | PreToolUse / UserPromptSubmit | Emits a per-token-bucket reminder listing the active MCP servers. Its debug log (`--log-path`), and the fatal-error log named by `ATTENTION_REMINDER_LOG` under `ATTENTION_REMINDER_DEBUG=1`, are created `0600`, and a log that already exists is tightened to `0600` `ClaudeCode/hooks/attention-reminder.py:log` |
| `mcp-first-guard.py` | Python | PreToolUse / matcher `Bash` | MCP-first routing guard: denies file-search / listing / file-read / stream-edit / dir-creation / system-inspection binaries as the *primary* command of a statement, steering to the purity / inspect MCP equivalents `ClaudeCode/hooks/mcp-first-guard.py:BLOCKED`; a downstream pipe stage stays allowed. It also denies specific invocations of innocent binaries `ClaudeCode/hooks/mcp-first-guard.py:BLOCKED_FORMS`: `python3 -m py_compile` / `-m compileall` (they write `.pyc` into the tree), `node --check` / `node -c` (only when node is installed), and `bash -n` / `sh -n` (only when bash is installed). `zsh -n` is deliberately left alone, because the validator it would be steered to parses with bash `ClaudeCode/hooks/mcp-first-guard.py:bash_noexec_mode`. It sees through wrappers, `-c` payloads and command substitution, always exits 0 and fails open (`deny`\|empty polarity) |
| `sbx-gate.py` | Python | PreToolUse / matcher `Bash` | Grant-only gate: auto-allows a clean, contained, single `sbx` invocation (canonical-path identity, rejects shell metacharacters) and stays silent otherwise (`allow`\|empty polarity, disjoint from `mcp-first-guard.py`, so their order is safe either way). Its accept-set left of the bare `--` is closed `ClaudeCode/hooks/sbx-gate.py:is_clean_sbx`: `--ro`, `--seccomp` and `--dry-run` take no argument (`--seccomp` only narrows a run the gate would already allow, and `--dry-run` execs no child, so it grants strictly less than a plain run), `--write DIR`/`--write=DIR` passes only if the scope resolves inside the project root, `--net` is refused, and every other token is a hard prompt, including the equals-forms `--seccomp=1` and `--dry-run=1`. Rationale: [[0005-approve-the-wrapper-not-the-command]] |
| `post-edit-clang-format.sh` | Bash | edit `.c/.cpp/.h/.hpp` | Auto-formats in place with the clang-format recorded in the CMake cache; no CMake build or no `.clang-format` is a silent skip |
| `post-edit-clang-tidy.sh` | Bash | edit `.c/.cpp/.h/.hpp` | Runs clang-tidy against `build/compile_commands.json`; blocks (exit 2) on warnings, errors (exit 1) on a missing CMake cache or compile database |
| `post-edit-json-lint.sh` | Bash | edit `.json/.jsonc/.jsonl/.json5` | Validates syntax with `jq` (comments stripped for JSONC/JSON5, line by line for JSONL); skips if jq is absent |
| `post-edit-python-lint.sh` | Bash | edit `.py` | In-memory `compile()` syntax check (writes no `.pyc`), then `ruff check` if ruff is available |
| `post-edit-vue-lint.sh` | Bash | edit `.vue/.js/.jsx/.ts/.tsx/.mjs/.cjs/.mts/.cts` | Auto-detects the linter from the nearest `package.json`, in priority order Biome, oxlint, ESLint |
| `post-edit-lint.sh` | Bash | edit (generic) | One dispatcher for the four lint hooks above (clang-tidy, JSON, Python, Vue/JS/TS), chosen by file extension; it does not format |
| `prompt-inject-time.sh` | Bash | UserPromptSubmit | Injects fresh wall-clock time into context each prompt |
| `session-start-host-info.sh` | Bash | SessionStart | Injects host identity (hostname, arch, distro, user, CPU/RAM) and the session-start time once per session, and again on resume / clear / compact |

Not in this directory: `Scripts/context-guard.sh` is also a Claude Code hook
(PreToolUse and PostToolUse, for restartable sessions), but it lives in
`Scripts/` and is documented on [[scripts]].

## Declared limits

`mcp-first-guard.py` is a routing steer, not a security boundary: it fails open
on any error, and by its own account every limit it has misses in the allow
direction `ClaudeCode/hooks/mcp-first-guard.py:MAX_DEPTH`. The one a user meets
first:

- **A shell reserved word hides the primary command.** `primary()` peels only
  group openers (`(`, `{`), leading `VAR=val` assignments and the wrapper
  commands in `SKIP_WRAPPERS`; it carries no keyword list
  `ClaudeCode/hooks/mcp-first-guard.py:primary`. A statement that begins with
  `if`, `then`, `else`, `elif`, `while`, `until`, `do` or `!`, or with a `case`
  arm, therefore reports the keyword as its primary command and is allowed:
  `for f in x; do head -c 10 "$f"; done` passes, while `true; head file` is
  denied. `for` itself hides nothing, because its body starts at `do`; `time`
  is in `SKIP_WRAPPERS` and `{` is peeled, so those two are seen through
  `ClaudeCode/hooks/mcp-first-guard.py:SKIP_WRAPPERS`. Documented rather than
  fixed, by decision.

## The complex one

`attention-reminder.py` is the most involved: it reads the session transcript,
computes token buckets, runs `claude mcp list`, cross-checks child PIDs to
filter session-disabled MCP servers, and emits an `additionalContext` payload
into Claude's context. It is the enforcement layer behind the mandatory
tool-routing convention described in [[overview]].
