# ClaudeCode Implementation Workflow

This document describes the complete workflow for implementing features using ClaudeCode with structured planning and autonomous execution.

## Installation

`ClaudeCode/` is a Claude Code **plugin** named `p`. Its manifest is `.claude-plugin/plugin.json`; everything it ships — agents, skills, hooks — is exposed under the `p:` namespace (`p:minion-explorer`, `/p:feature-plan`, `Skill(p:wiki)`). The `p:` prefix is added automatically by the namespace, so the definition files themselves carry the **bare** name.

### Recommended: live-editable install (symlink)

Claude Code treats any `~/.claude/skills/<name>/` that contains a `.claude-plugin/plugin.json` as a full plugin and loads it **in place** — it is NOT copied into the plugin cache, so edits in this repo take effect immediately. Link the plugin root in:

```bash
# link ~/.claude/skills/p at this repo's plugin root (use an ABSOLUTE path)
ln -s /absolute/path/to/prompt-heaven/ClaudeCode ~/.claude/skills/p
```

Then, inside Claude Code, run `/reload-plugins` (or restart the session).

> **Important:** `~/.claude/skills` must be a real directory that *contains* the `p` symlink — it must NOT itself be a symlink to this repo's `skills/` folder, or `p` would nest inside `skills/` and discovery breaks.

### Alternative: per-launch flag (no symlink)

```bash
claude --plugin-dir /absolute/path/to/prompt-heaven/ClaudeCode
```

Loads the plugin for that session only. There is no `settings.json` key or env var to make `--plugin-dir` persistent — hence the symlink is preferred.

### Verify

```bash
claude plugin validate ~/.claude/skills/p --strict     # manifest + every agent & skill
```

In a session the fleet appears as `p:minion-*` (Agent tool `subagent_type`) and each skill as `p:<name>` (`/p:feature-plan`, `p:wiki`, …). A lone warning about a root `CLAUDE.md` under `--strict` is benign.

### Authoring rules that bite

- **No colon in a `name`.** A `:` in an agent/skill frontmatter `name:` field — or in a skill directory name — breaks loading. Keep names bare; the namespace adds `p:`.
- **Dots are sanitized.** A `.` in a skill name becomes `-`, so a directory `ctest-h/` loads as `p:ctest-h`.
- **Frontmatter `description:` must be valid YAML.** A one-line value containing `: ` (colon-space) or wrapped in backticks breaks the parser; use a folded block scalar (`>-`).
- **Bundled-script paths.** From a `SKILL.md` body use `${CLAUDE_PLUGIN_ROOT}/skills/<name>/…` (expanded only in SKILL.md bodies + `allowed-tools`); in support files/agent bodies use the literal install path `~/.claude/skills/p/skills/<name>/…`.

## TL;DR

1. **/p:feature-plan** → Discuss requirements interactively with ClaudeCode and save the plan to a markdown file
2. **`/p:task-plan`** → Generates `requirements.yaml` (REQUIRED - this is the input for implementation)
3. **`/p:implement`** → Executes tasks autonomously from `requirements.yaml`, then syncs the `docs/` wiki with the shipped code (via `/p:wiki ingest`) as its final step
4. **Token exhausted?** → `/p:implement --continue` to resume

**Key:** `/p:task-plan` creates the structured task list that `/p:implement` needs. You MUST run it between planning and implementation.

## Overview

The workflow consists of four main phases:

1. **Planning Phase** - `/p:feature-plan` interactive requirement gathering and implementation planning agent creates a detailed plan
2. **Task Generation** - `/p:task-plan` creates `requirements.yaml` with structured task list (REQUIRED)
3. **Implementation Phase** - `/p:implement` executes tasks autonomously using `requirements.yaml`, then documents the result by re-syncing the `docs/` wiki (`/p:wiki ingest`)
4. **Resume on Interruption** - Continue from where you left off if token budget runs out

## Quick Start

```bash
# 1. Start planning phase (in ClaudeCode)
/p:feature-plan Describe your feature, answer questions interactively

# 2. Generate structured task list (REQUIRED)
/p:task-plan

# 3. Execute implementation
/p:implement

# 4. If interrupted, resume
/p:implement --continue
```

**Critical:** `/p:task-plan` MUST be run before `/p:implement` - it creates the required `requirements.yaml` file.

## Workflow Steps

### 1. Planning Phase: Create Implementation Plan

Start ClaudeCode in plan mode to interactively gather requirements and create a comprehensive implementation plan.

**What happens during planning:**

- ClaudeCode asks clarifying questions about architecture, dependencies, data models, security, interfaces
- User answers questions one by one
- ClaudeCode searches the codebase for similar patterns and existing code
- Iterative refinement of requirements and constraints

**Key characteristics:**

- **Interactive**: One question at a time, iterative refinement
- **Thorough**: Searches for similar code patterns to reference
- **Collaborative**: User and ClaudeCode refine requirements together

**Output:** Understanding of requirements, constraints, and implementation approach

**Next step:** Run `/p:task-plan` to create the structured implementation plan

### 2. Task List Generation: Create requirements.yaml

After planning phase is complete, generate the structured implementation plan:

```bash
/p:task-plan
```

**This step is REQUIRED** - it creates the `requirements.yaml` file that `/p:implement` needs.

**What this command does:**

- Converts planning phase discussions into structured YAML format
- Creates `requirements.yaml` in project root (or specified location)
- Generates function-level implementation tasks with:
  - All requirements and answers
  - Constraints and success criteria
  - Task breakdown with code references and dependencies
- Creates technical specification document in `docs/` folder
- Validates the plan is complete and ready for implementation

**Output:** `requirements.yaml` with `complete: true` and fully populated `implementation_plan` section

For detailed planning workflow and YAML schema, see [skills/task-plan/SKILL.md](skills/task-plan/SKILL.md)

### 3. Implementation Phase: Execute the Plan

Once `requirements.yaml` exists (created by `/p:task-plan`), execute the implementation autonomously:

```bash
/p:implement
```

**Prerequisites:**
- `requirements.yaml` must exist in project root (or specify path with `--plan`)
- The YAML must have `complete: true`
- The `implementation_plan` section must be populated with tasks

**What happens during implementation:**

For each task in dependency order:

1. **Read code references** - Load all similar code patterns identified during planning
2. **Execute task** - Create/modify/delete functions following existing patterns
3. **Verify quality**:
   - Run language-specific linters (clang-tidy for C/C++, luac for Lua, etc.)
   - Build the project
   - Run tests
4. **Update progress** - Mark task as completed and move to next

**Key characteristics:**

- **Autonomous**: Runs without user intervention for well-defined tasks
- **Pattern-following**: Uses code references to maintain consistency
- **Test-driven**: Builds and tests after each task
- **Incremental**: Verifies each step before proceeding

**Command options:**

```bash
/p:implement                              # Use default requirements.yaml
/p:implement --plan ./docs/my-plan.yaml   # Use specific plan file
/p:implement --dry-run                    # Show what would be executed
/p:implement --task task-003              # Execute only specific task (debugging)
```

For detailed implementation workflow, see [skills/implement/SKILL.md](skills/implement/SKILL.md)

### 4. Resume on Token Budget Exhaustion

If ClaudeCode runs out of tokens during implementation, simply restart and continue:

```bash
/p:implement --continue
```

This will:
- Resume from the last completed task
- Skip already completed tasks
- Continue with remaining tasks in dependency order

## Complete Example

Here's a complete workflow example for implementing WebSocket ping/pong support:

### Step 1: Start Planning

```
User: I need to implement WebSocket ping/pong support in the server
```

ClaudeCode enters plan mode and asks questions:

```
Q1: Should the server auto-respond to pings, manually handle them, or both?
   1. Auto-respond (RFC 6455 compliant)
   2. Manual handling via callback
   3. Both options available

User: 1

Q2: Should we send periodic pings to clients for keep-alive?
   ...
```

After all questions are answered, planning phase is complete.

### Step 2: Generate Task List

Now create the structured implementation plan:

```bash
/p:task-plan
```

This command creates:
- `requirements.yaml` in project root with complete implementation plan
- `docs/websocket-ping-pong-spec.md` with technical specification

Output shows:
```
Created requirements.yaml with 4 tasks:
  task-001: Add ping/pong frame constants
  task-002: Implement send_ping function
  task-003: Handle incoming pings
  task-004: Create integration tests

✓ Implementation plan is ready
✓ Technical specification created: docs/websocket-ping-pong-spec.md
```

### Step 3: Execute Implementation

```bash
/p:implement
```

Output:
```
[Task 1/4: task-001]
Description: Add ping/pong frame type constants
✓ Read reference code
✓ Modified websocket-server.h
✓ clang-tidy passed
✓ Build successful

[Task 2/4: task-002]
Description: Implement websocket_send_ping function
✓ Read reference: websocket_send_frame
✓ Created function
✓ clang-tidy passed
✓ Build successful

[Task 3/4: task-003]
Description: Handle incoming ping frames
✓ Modified websocket_handle_frame
✓ clang-tidy passed
✓ Build successful

[Task 4/4: task-004]
Description: Create integration tests
✓ Created test file
✓ Build successful
✓ Tests passed (3/3)

✓ All success criteria met

[Documentation & Wiki]
✓ docs/ wiki synced (/p:wiki ingest): 2 pages updated
⚠ new subsystem → [PROPOSE-NEW-PAGE] surfaced for approval

Implementation complete!
```

### Step 4: Resume if Interrupted

If implementation was interrupted at task 3:

```bash
/p:implement --continue
```

Output:
```
Resuming from task 3 of 4...
[Task 3/4: task-003]
...
```

## Key Benefits

### Consistency
- Code references ensure new code follows existing patterns
- Same error handling, memory management, logging style throughout

### Reliability
- Each task is tested before proceeding
- Build verification after every change
- Language-specific quality checks (linters, syntax checks)

### Traceability
- Every decision documented in requirements.yaml
- Code references explain why patterns were chosen
- Technical specifications provide context

### Efficiency
- Autonomous execution once planning is complete
- Parallel work possible (multiple independent tasks)
- Resume capability for long implementations

## File Structure

```
project-root/
├── requirements.yaml              # Generated by /p:task-plan (REQUIRED for /p:implement)
├── docs/
│   └── feature-spec.md           # Technical specification (created by /p:task-plan)
├── ClaudeCode/
│   ├── .claude-plugin/
│   │   └── plugin.json           # Plugin manifest (name: p → the p: namespace)
│   ├── README.md                 # This file
│   ├── agents/                   # Minion fleet → p:minion-* (Agent tool subagent_type)
│   ├── skills/                   # Skills → p:<name> (e.g. skills/wiki/ → p:wiki)
│   │   ├── task-plan/SKILL.md    # Detailed planning workflow
│   │   └── implement/SKILL.md    # Detailed implementation workflow
│   ├── hooks/                    # Post-edit hooks for quality checks
│   │   ├── post-edit-clang-format.sh  # C/C++ auto-formatter
│   │   ├── post-edit-clang-tidy.sh    # C/C++ linter
│   │   ├── post-edit-json-lint.sh     # JSON/JSONC/JSONL/JSON5 validator
│   │   └── post-edit-vue-lint.sh      # Vue/JS/TS linter (auto-detect)
│   └── scripts/
│       └── task-implementation-plan.py  # Token-efficient plan extraction
└── src/                          # Your source code
```

## Hooks Configuration

Claude Code hooks run automatically after tool executions to ensure code quality. Configure them in your project's `.claude/settings.json`.

### Prerequisites

| Hook | Dependencies |
|------|--------------|
| `post-edit-clang-format.sh` | CMake configured build with `CLANG_FORMAT_EXE`, `.clang-format` config |
| `post-edit-clang-tidy.sh` | CMake configured build with `CLANG_TIDY_EXE`, `.clang-tidy` config, `compile_commands.json` |
| `post-edit-json-lint.sh` | `jq` (required), `npx json5` (optional, for full JSON5 support) |
| `post-edit-vue-lint.sh` | `jq` (required), `package.json` with linter, linter config file |

### Settings Configuration

Add to `.claude/settings.json`:

```json
{
  "hooks": {
    "postToolUse": [
      {
        "matcher": "Edit|MultiEdit|Write",
        "command": "/path/to/ClaudeCode/hooks/post-edit-clang-format.sh"
      },
      {
        "matcher": "Edit|MultiEdit|Write",
        "command": "/path/to/ClaudeCode/hooks/post-edit-clang-tidy.sh"
      },
      {
        "matcher": "Edit|MultiEdit|Write",
        "command": "/path/to/ClaudeCode/hooks/post-edit-json-lint.sh"
      },
      {
        "matcher": "Edit|MultiEdit|Write",
        "command": "/path/to/ClaudeCode/hooks/post-edit-vue-lint.sh"
      }
    ]
  }
}
```

**Note:** Replace `/path/to/` with the actual path to your ClaudeCode hooks directory.

### Hook Details

#### 1. clang-format (Auto-formatter)

**File:** `hooks/post-edit-clang-format.sh`

| Property | Value |
|----------|-------|
| Triggers on | `.c`, `.cpp`, `.h`, `.hpp` files |
| Tools | Edit, MultiEdit, Write |
| Action | Auto-formats file in-place |
| Exit codes | 0 = success, 1 = config error, 2 = format failed |

**Requirements:**
- `PROJECT_ROOT` or `CLAUDE_PROJECT_DIR` environment variable
- `build/CMakeCache.txt` with `CLANG_FORMAT_EXE:FILEPATH=` entry
- `.clang-format` config file in project root

#### 2. clang-tidy (C/C++ Linter)

**File:** `hooks/post-edit-clang-tidy.sh`

| Property | Value |
|----------|-------|
| Triggers on | `.c`, `.cpp`, `.h`, `.hpp` files |
| Tools | Edit, MultiEdit, Write |
| Action | Runs static analysis, blocks on warnings |
| Exit codes | 0 = success, 1 = config error, 2 = lint warnings (blocks) |

**Requirements:**
- `PROJECT_ROOT` or `CLAUDE_PROJECT_DIR` environment variable
- `build/CMakeCache.txt` with `CLANG_TIDY_EXE:FILEPATH=` entry
- `build/compile_commands.json` (CMake with `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`)
- `.clang-tidy` config file in project root

#### 3. json-lint (JSON Validator)

**File:** `hooks/post-edit-json-lint.sh`

| Property | Value |
|----------|-------|
| Triggers on | `.json`, `.jsonc`, `.jsonl`, `.json5` files |
| Tools | Edit, MultiEdit, Write |
| Action | Validates JSON syntax, blocks on errors |
| Exit codes | 0 = success (or jq not found), 2 = invalid JSON (blocks) |

**Format handling:**

| Extension | Validation Method |
|-----------|-------------------|
| `.json` | Direct `jq` validation |
| `.jsonc` | Strip `//` and `/* */` comments, then `jq` |
| `.jsonl` | Line-by-line `jq` validation |
| `.json5` | Strip comments + trailing commas, `jq` → fallback to `npx json5` |

**Requirements:**
- `jq` installed and in PATH
- (Optional) `npx` with `json5` package for full JSON5 support

#### 4. vue-lint (Vue/JS/TS Linter)

**File:** `hooks/post-edit-vue-lint.sh`

| Property | Value |
|----------|-------|
| Triggers on | `.vue`, `.js`, `.jsx`, `.ts`, `.tsx`, `.mjs`, `.cjs`, `.mts`, `.cts` files |
| Tools | Edit, MultiEdit, Write |
| Action | Auto-detects and runs linter, blocks on errors |
| Exit codes | 0 = success (or no linter found), 2 = lint errors (blocks) |

**Auto-detection priority:**

| Priority | Linter | Detection |
|----------|--------|-----------|
| 1 | Biome | `@biomejs/biome` in package.json + `biome.json` config |
| 2 | oxlint | `oxlint` in package.json |
| 3 | ESLint | `eslint` in package.json + eslint config file |

**Supported ESLint configs:**
- Flat config: `eslint.config.js`, `eslint.config.mjs`, `eslint.config.cjs`
- Legacy: `.eslintrc`, `.eslintrc.js`, `.eslintrc.cjs`, `.eslintrc.json`, `.eslintrc.yml`, `.eslintrc.yaml`
- package.json: `eslintConfig` field

**Requirements:**
- `jq` installed and in PATH
- `package.json` with linter dependency
- Linter config file (varies by linter)
- `npx` for running linters

### Example: Minimal C/C++ Project Setup

```bash
# 1. Configure CMake with compile commands
cmake -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON

# 2. Create .claude/settings.json
mkdir -p .claude
cat > .claude/settings.json << 'EOF'
{
  "hooks": {
    "postToolUse": [
      {
        "matcher": "Edit|MultiEdit|Write",
        "command": "/path/to/hooks/post-edit-clang-format.sh"
      },
      {
        "matcher": "Edit|MultiEdit|Write",
        "command": "/path/to/hooks/post-edit-clang-tidy.sh"
      },
      {
         "matcher": "Edit|MultiEdit|Write",
         "command": "/path/to/hooks/post-edit-json-lint.sh"
      }
    ]
  }
}
EOF

# 3. Ensure config files exist
touch .clang-format .clang-tidy
```

### Example: Vue/JS/TS Project Setup

```bash
# 1. Ensure ESLint is configured (or Biome/oxlint)
# Example with ESLint + Vue:
npm install -D eslint eslint-plugin-vue @vue/eslint-config-typescript

# 2. Create eslint config (flat config example)
cat > eslint.config.js << 'EOF'
import pluginVue from 'eslint-plugin-vue'
export default [
  ...pluginVue.configs['flat/recommended'],
]
EOF

# 3. Create .claude/settings.json
mkdir -p .claude
cat > .claude/settings.json << 'EOF'
{
  "hooks": {
    "postToolUse": [
      {
        "matcher": "Edit|MultiEdit|Write",
        "command": "/path/to/hooks/post-edit-json-lint.sh"
      },
      {
        "matcher": "Edit|MultiEdit|Write",
        "command": "/path/to/hooks/post-edit-vue-lint.sh"
      }
    ]
  }
}
EOF
```

### Troubleshooting Hooks

**Problem:** Hook not running
- Check that `.claude/settings.json` exists and has correct JSON syntax
- Verify hook script is executable (`chmod +x`)
- Check matcher regex matches the tool name

**Problem:** "PROJECT_ROOT not set"
- Set `CLAUDE_PROJECT_DIR` environment variable, or
- Run Claude Code from the project root directory

**Problem:** "No CMakeCache.txt found"
- Run CMake configuration first: `cmake -B build`

**Problem:** "jq not found"
- Install jq: `brew install jq` (macOS) or `apt install jq` (Linux)

## Best Practices

### During Planning Phase

1. **Read project conventions first**: ClaudeCode should read CLAUDE.md and language-specific instruction files
2. **Be thorough**: Answer all questions completely, architectural decisions first
3. **Reference similar code**: Point ClaudeCode to existing implementations to follow
4. **Define success clearly**: Specific, measurable criteria

### After Planning: Generate Task List

1. **Always run /p:task-plan**: This creates the required `requirements.yaml` file
2. **Review the output**: Check that all tasks, references, and dependencies are correct
3. **Verify completeness**: Ensure `complete: true` and `implementation_plan` section is populated
4. **Store in version control**: Commit `requirements.yaml` and technical spec before implementing

### During Implementation

1. **Trust the references**: Code references are mandatory patterns, not suggestions
2. **Let it run**: Don't interrupt unless there are errors
3. **Review, don't rewrite**: If output doesn't match expectations, refine the plan, not the code directly
4. **Use --continue**: Token budget exhausted? Just restart with --continue

### For Large Features

1. **Break into phases**: Multiple requirements.yaml files for major components
2. **Sequential phases**: Complete one phase before starting next
3. **Clear dependencies**: Make inter-phase dependencies explicit
4. **Incremental testing**: Full test suite after each phase

## Troubleshooting

### Planning Phase Issues

**Problem:** ClaudeCode isn't finding similar code patterns
- Solution: Manually point to relevant files using Glob/Grep patterns

**Problem:** Too many questions, seems stuck
- Solution: Mark requirements as "answered" even if uncertain, iterate later

### Implementation Phase Issues

**Problem:** Build fails during implementation
- Solution: Fix the error, ClaudeCode will ask how to proceed

**Problem:** Tests fail after a task
- Solution: Review test output, fix implementation, re-run verification

**Problem:** Token budget exhausted mid-implementation
- Solution: Use `/p:implement --continue` to resume

### General Issues

**Problem:** Generated code doesn't follow project style
- Solution: Ensure CLAUDE.md and language-specific instructions exist and are comprehensive

**Problem:** Implementation deviates from plan
- Solution: Review code_references in requirements.yaml, ensure they're accurate

## Advanced Usage

### Custom Plan Location

```bash
/p:implement --plan ./features/auth/requirements.yaml
```

### Debug Specific Task

```bash
/p:implement --task task-007
```

### Review Plan Without Implementing

```bash
/p:implement --dry-run
```

## Related Documentation

- [Task Planning Schema and Workflow](skills/task-plan/SKILL.md) - Detailed planning phase guide
- [Implementation Command Reference](skills/implement/SKILL.md) - Detailed implementation phase guide
- [Requirements Skill](skills/requirements/SKILL.md) - Task management and status updates

## Summary

The ClaudeCode workflow provides:
1. **Interactive planning** with thorough requirement gathering (plan mode)
2. **Structured task generation** with `/p:task-plan` creating `requirements.yaml` (REQUIRED)
3. **Autonomous implementation** with `/p:implement` following existing patterns
4. **Resume capability** with `--continue` for long-running implementations

Key points:
- **requirements.yaml is REQUIRED**: Created by `/p:task-plan`, used by `/p:implement`
- **Code references are mandatory**: New code must follow existing patterns
- **Test-driven**: Build and test after each task
- **Incremental**: Verify each step before proceeding

This approach ensures consistent, tested, traceable code changes that follow your project's conventions.
