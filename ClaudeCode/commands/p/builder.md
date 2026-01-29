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

### Phase 1: Initialization

1. **Load requirements.yaml**:
   - Path: `${PROJECT_ROOT}/requirements.yaml`
   - If file doesn't exist, report error and exit
   - Extract task list only (no need for full context)

2. **Display current task status**:
   ```bash
   ~/.claude/scripts/task-show-all.py ${PROJECT_ROOT}/requirements.yaml
   ```

3. **Identify incomplete tasks**:
   - Look for tasks with status `pending` or `in_progress`

4. **Ask for confirmation**:
   - "Found X pending/in_progress tasks. Proceed with implementation?"
   - Allow user to cancel

### Phase 2: Task Batching Analysis

Same batching rules as original builder:

#### Size Score Mapping

| Size | Score |
|------|-------|
| SS | 1 |
| S | 2 |
| M | 3 |
| L | 4 |
| XL | 5 |
| XXL | 6 |
| - (undefined) | 3 (default to M) |

#### Batching Rules

Two consecutive tasks can be batched if ALL conditions are met:
1. **Combined score ≤ 4**
2. **No interdependencies**
3. **No file conflicts**
4. **Maximum batch size**: 2 tasks

#### Display Batching Plan

```
Task Batching Plan:
   Batch 1: task-001 (SS) + task-002 (S) -> combined score: 3
   Batch 2: task-003 (M) -> single task

   Total: 3 tasks in 2 batches
```

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
+---------------------------------------+
| 1. Read requirements.yaml             |  <- OK
| 2. Show tasks, ask confirmation       |  <- OK
| 3. Calculate batches                  |  <- OK
| 4. Launch Task with minimal info      |  <- OK (KEY STEP)
| 5. Check result                       |  <- OK
| 6. STOP on error, continue on success |  <- OK
+---------------------------------------+

NOT YOUR JOB:
+------------------------------------+
| X Read code references             |  <- Agent does this
| X Read target file contents        |  <- Agent does this
| X Assemble implementation packages |  <- Agent handles it
| X Provide retry options on failure |  <- Just STOP
| X Write any code                   |  <- FORBIDDEN
+------------------------------------+
```
