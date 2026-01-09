# ClaudeCode Implementation Workflow

This document describes the complete workflow for implementing features using ClaudeCode with structured planning and autonomous execution.

## TL;DR

1. **Plan Mode** → Discuss requirements interactively with ClaudeCode and save the plan to a markdown file
2. **`/p:task-plan`** → Generates `requirements.yaml` (REQUIRED - this is the input for implementation)
3. **`/p:implement`** → Executes tasks autonomously from `requirements.yaml`
4. **Token exhausted?** → `/p:implement --continue` to resume

**Key:** `/p:task-plan` creates the structured task list that `/p:implement` needs. You MUST run it between planning and implementation.

## Overview

The workflow consists of four main phases:

1. **Planning Phase** - Interactive requirement gathering and implementation planning (plan mode)
2. **Task Generation** - `/p:task-plan` creates `requirements.yaml` with structured task list (REQUIRED)
3. **Implementation Phase** - `/p:implement` executes tasks autonomously using `requirements.yaml`
4. **Resume on Interruption** - Continue from where you left off if token budget runs out

## Quick Start

```bash
# 1. Start planning phase (in ClaudeCode)
# Describe your feature, answer questions interactively

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

For detailed planning workflow and YAML schema, see [commands/p/task-plan.md](commands/p/task-plan.md)

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

For detailed implementation workflow, see [commands/p/implement.md](commands/p/implement.md)

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
│   ├── README.md                 # This file
│   ├── commands/p/
│   │   ├── task-plan.md          # Detailed planning workflow
│   │   └── implement.md          # Detailed implementation workflow
│   └── skills/
│       └── p:implementation-plan/  # Helper skill for loading plans
└── src/                          # Your source code
```

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

- [Task Planning Schema and Workflow](commands/p/task-plan.md) - Detailed planning phase guide
- [Implementation Command Reference](commands/p/implement.md) - Detailed implementation phase guide
- [Implementation Plan Skill](skills/p:implementation-plan/SKILL.md) - Helper for loading plans efficiently
- [Requirements Skill](skills/p:requirements/SKILL.md) - Task management and status updates

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
