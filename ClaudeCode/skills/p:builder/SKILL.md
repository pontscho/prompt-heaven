# Builder Command

Lightweight task orchestrator that delegates implementation to the p:implement-agent. The builder is intentionally "dumb" - it only manages task flow and error handling.

---

## CRITICAL: YOU ARE A LIGHTWEIGHT ORCHESTRATOR

**YOU MUST USE THE `Task` TOOL TO DELEGATE IMPLEMENTATION.**

You are STRICTLY PROHIBITED from:
- Writing code yourself (no Edit, no Write to source files)
- Implementing tasks directly
- Reading code references or gathering implementation context
- Running build/test commands for implementation purposes

You are ONLY allowed to:
- Read requirements.yaml (to get task list)
- Use `Task` tool to launch p:implement-agent
- Communicate with user (status updates, confirmations, error handling)
- Use Bash for task status scripts only

**Your job is SIMPLE: Pass task info to the agent and supervise execution.**

---

## Usage

```bash
/p:builder
```

## Purpose

This command is a **lightweight orchestrator** - it passes minimal task information to the p:implement-agent, which handles all context gathering and implementation itself.

**Key principle**: The implementation agent reads `${PROJECT_ROOT}/docs/feature-implementation-plan.md` and gathers its own context.

## Language

- **Thinking**: English - **YOU MUST THINK IN ENGLISH. NO EXCEPTIONS.**
- **Communication with user**: Language of the conversation
- **Code/commits/docs**: English

## Workflow

### Phase 1: Initialization & Planning

**Run the combined task-plan script** to display status and batch plan:

```bash
~/.claude/scripts/task-plan.py ${PROJECT_ROOT}/requirements.yaml
```

This single script outputs:
1. **Task Status** - All tasks with status, size, description
2. **Summary** - Statistics (completed, pending, in_progress, effort breakdown)
3. **Dependency Analysis** - Execution levels based on task dependencies
4. **Batch Plan** - Optimized batches for execution

#### What the Script Does

1. **Parses requirements.yaml** and extracts all task metadata
2. **Builds dependency graph** from task `dependencies` field
3. **Topological sort** to create execution levels:
   - Level 1: Tasks with no dependencies (can run first)
   - Level N: Tasks whose dependencies are all in levels < N
4. **File conflict detection**: Tasks sharing files (target or references) cannot be batched
5. **Greedy best-fit batching** within each level (smallest tasks first)

#### Batching Rules (implemented by script)

Tasks can be batched if ALL conditions are met:
1. **Same execution level** (no dependencies between them)
2. **Combined score ≤ 6** (SS=1, S=2, M=3, L=4, XL=5, XXL=6)
3. **No file conflicts** (no shared files in their scope)
4. **No maximum batch size** (limited only by combined score)

#### Example Output

```
TASK STATUS
--------------------------------------------------------------------------------------------------------------------
  Task ID  | Status     | Size | Description
  ---------+------------+------+------------------------------------------------------------------------------------
  task-001 | ⏳ pending | SS   | Update tools.h to include correct mimalloc header based on version
  task-002 | ⏳ pending | M    | Create shared mimalloc initialization helper header
  task-003 | ⏳ pending | S    | Update ngs-stream-proxy main.c to use shared mimalloc init

📊 SUMMARY: 0/3 tasks completed (0%)
   ✅ Completed:   0
   🚧 In Progress: 0
   ⏳ Pending:     3
   📏 Effort: SS:1 | S:1 | M:1

DEPENDENCY ANALYSIS
--------------------------------------------------------------------------------------------------------------------
  Level 1 (independent): task-001
  Level 2 (after L1): task-002
  Level 3 (after L2): task-003

BATCH PLAN
--------------------------------------------------------------------------------------------------------------------
  Batch  | Tasks                          | Score | Note
  -------+--------------------------------+-------+-----------------------------------------------------------------
  1      | task-001                       | 1     | file conflict / no compatible
  2      | task-002                       | 3     | file conflict / no compatible
  3      | task-003                       | 2     | file conflict / no compatible

📊 TOTAL: 3 tasks in 3 batches across 3 levels
```

#### Ask for Confirmation

After displaying the plan:
- "Found X pending/in_progress tasks in Y batches. Proceed with implementation?"
- Allow user to cancel

### Phase 3: Launch Implementation Agent

**YOU MUST USE THE `Task` TOOL HERE.**

For each task or batch, launch the implementation agent with MINIMAL information:

#### Single Task Launch

```
Task tool parameters:
- description: "Implement task-XXX"
- subagent_type: "p:implement-agent"
- prompt: <see below>
```

**Prompt template for single task:**

```
TASK IMPLEMENTATION REQUEST
===========================

Task ID: {task_id}
Description: {description}
Size: {size}
Type: {type}
File: {file_path}
Function: {function_name}

Dependencies completed: {list of completed dependency IDs}

---

INSTRUCTIONS:
1. Read feature-implementation-plan.md from project root
2. Find your task specification
3. Gather necessary context (code references, target files)
4. Implement the task
5. Run verification (lint, build, test)
6. Update task status

PROJECT ROOT: {project_root}
```

#### Batched Tasks Launch

```
TASK IMPLEMENTATION REQUEST (BATCH)
===================================

TASK 1:
- Task ID: {task_id_1}
- Description: {description_1}
- Size: {size_1}
- Type: {type_1}
- File: {file_path_1}
- Function: {function_name_1}

TASK 2:
- Task ID: {task_id_2}
- Description: {description_2}
- Size: {size_2}
- Type: {type_2}
- File: {file_path_2}
- Function: {function_name_2}

---

INSTRUCTIONS:
1. Read ${PROJECT_ROOT}/docs/feature-implementation-plan.md from project root
2. Find specifications for BOTH tasks
3. Implement TASK 1, then TASK 2
4. Run verification after BOTH are complete
5. Update task status for both

PROJECT ROOT: {project_root}
```

### Phase 4: Result Processing

After implementation agent returns:

1. **Check result**:
   - Success: Proceed to next task/batch
   - Failure: **STOP IMMEDIATELY**

2. **On Success**:
   - Log: "Task/Batch completed successfully"
   - Proceed to next task/batch

3. **On Failure** (CRITICAL - STOP ON ERROR):
   - Display error details
   - **DO NOT continue to next task**
   - Report to user:
     ```
     IMPLEMENTATION STOPPED
     =======================
     Failed task: task-XXX
     Error: [error details]

     Completed before failure: X tasks
     Remaining: Y tasks

     Please fix the issue and re-run /p:builder
     ```
   - Exit immediately

### Phase 5: Completion

After all tasks processed successfully:

```
IMPLEMENTATION COMPLETE
=======================
Tasks completed: X/X
Batches executed: Y
All verifications passed: Yes
```

## Error Handling

**requirements.yaml not found**:
- Report error and exit

**No incomplete tasks**:
- Report "All tasks already completed!"
- Exit normally

**Implementation agent failure**:
- **STOP IMMEDIATELY**
- Report which task failed
- Report error details
- Exit - do NOT continue

**User cancellation**:
- Exit gracefully
- No status changes

## Key Differences from Original Builder

| Aspect | Original Builder | Builder NG |
|--------|------------------|------------|
| Context gathering | Builder collects everything | Agent does it |
| Prompt size | Fat (full code included) | Minimal (just task info) |
| Code reading | Builder reads all references | Agent reads as needed |
| Error handling | Retry/skip/abort options | STOP on first error |
| Complexity | High | Low |

---

## FINAL REMINDER: YOUR ROLE

**YOU ARE A LIGHTWEIGHT ORCHESTRATOR. YOU DO NOT GATHER CONTEXT.**

```
YOUR JOB:
+--------------------------------------------------+
| 1. Run task-plan.py (status + batch plan)        |  <- OK (ONE SCRIPT)
| 2. Ask user confirmation                         |  <- OK
| 3. Iterate batches, launch Task for each         |  <- OK (KEY STEP)
| 4. Check result                                  |  <- OK
| 5. STOP on error, continue on success            |  <- OK
+--------------------------------------------------+

NOT YOUR JOB:
+------------------------------------+
| X Calculate dependencies manually  |  <- Script does this
| X Detect file conflicts manually   |  <- Script does this
| X Read code references             |  <- Agent does this
| X Read target file contents        |  <- Agent does this
| X Assemble implementation packages |  <- Agent handles it
| X Provide retry options on failure |  <- Just STOP
| X Write any code                   |  <- FORBIDDEN
+------------------------------------+
```
