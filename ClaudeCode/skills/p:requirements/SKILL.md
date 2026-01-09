---
name: p:requirements
description: Automatically checks requirements.yaml to show project tasks, implementation plans, dependencies, and task status. Supports batch updating task statuses. Use when user asks about what to implement, what tasks need to be done, task planning, project requirements, mentions requirements.yaml, or wants to update task status. trigger-conditions, for example Implementation tasks or features to implement; What tasks need to be done; Project requirements or specifications; Task planning or task lists; What should I work on next?; What are the pending tasks?; Show me the requirements; Mark tasks as completed/pending; Update task status; Show the pending tasks; Show the task states. Any mention of requirements.yaml.
allowed-tools: Read, Grep, Glob, Bash
---

# Requirements Check Skill

## Trigger Conditions

Activate this skill when the user discusses or asks about:
- Implementation tasks or features to implement
- What tasks need to be done
- Project requirements or specifications
- Task planning or task lists
- "What should I work on next?"
- "What are the pending tasks?"
- "Show me the requirements"
- Updating task status (marking tasks as completed, pending, in_progress or cancel)
- "Mark task-XXX as completed"
- "I finished task-XXX"
- Batch updating multiple tasks
- Any mention of "requirements.yaml"

## Skill Purpose

This skill automatically checks the `requirements.yaml` file in the project root to understand what tasks are defined, their status, dependencies, and implementation details. It also provides functionality to update task statuses (single or batch updates) using the update_tasks.py script.

## Instructions

When this skill is activated:

1. **Display task status table**
   - Use the provided Python script to display tasks:
     ```bash
     python3 ~/.claude/skills/p:requirements/show_tasks.py requirements.yaml
     ```
   - This will show a formatted table with all tasks, their status, and progress summary
   - The script outputs: Task ID, Status (✅ completed or ⏳ pending, ❌ cancel, 🚧 in_progress), Description, and overall progress
   - Never process the output of this script, just display it.

1.25. **Display raw YAML blocks for specific tasks**
   - Use the show_task_details.py script to extract and display the raw YAML blocks for one or more tasks:
     ```bash
     python3 ~/.claude/skills/p:requirements/show_task_details.py task-001 task-002 task-003
     ```
   - This displays the complete raw YAML block for each task including all fields (task_id, description, status, type, file_path, function_name, implementation_details, dependencies, test_requirements, code_references, api_references)
   - Use this when:
     - You need to read detailed task information to implement or understand a task
     - User asks for details about specific tasks
     - Planning implementation and need to see implementation_details
     - Understanding dependencies between tasks
     - Need to check test requirements or API references
   - Examples:
     - Single task: `python3 ~/.claude/skills/p:requirements/show_task_details.py task-017`
     - Multiple tasks: `python3 ~/.claude/skills/p:requirements/show_task_details.py task-017 task-018 task-019`
   - The script automatically finds requirements.yaml in the current directory or parent directories
   - Output is the raw YAML exactly as it appears in requirements.yaml - use this to understand implementation details

1.5. **Update task status (single or batch)**
   - Use the update_tasks.py script to update task status in requirements.yaml:
     ```bash
     python3 ~/.claude/skills/p:requirements/update_tasks.py <status> <task1> <task2> ...
     ```
   - Valid statuses: `pending`, `completed`, `in_progress`, `cancel`
   - Examples:
     - Single task: `python3 ~/.claude/skills/p:requirements/update_tasks.py completed task-017`
     - Multiple tasks: `python3 ~/.claude/skills/p:requirements/update_tasks.py completed task-017 task-018 task-019`
     - Batch update: `python3 ~/.claude/skills/p:requirements/update_tasks.py pending task-020 task-021 task-022 task-023`
   - The script will:
     - Update all specified tasks to the given status
     - Preserve YAML formatting and indentation
     - Report which tasks were updated successfully
     - Warn about tasks that were not found
   - Use this when the user has completed tasks or wants to update task status

1.6. **Automatic task status update workflow**
   - **IMPORTANT**: Automatically update task status when a task is completed
   - When working on a task from requirements.yaml:
     1. Mark as `in_progress` when starting work on the task
     2. Implement the task according to implementation_details
     3. Run tests if test_requirements are specified
     4. Mark as `completed` ONLY when:
        - Implementation is fully done
        - Tests pass (if applicable)
        - Build succeeds (if applicable)
        - Code quality checks pass (clang-tidy, etc. if specified)
     5. Automatically run: `python3 ~/.claude/skills/p:requirements/update_tasks.py completed task-XXX`
   - **Example automatic workflow**:
     ```
     User: "Implement task-017"
     Assistant:
       1. Run: python3 ~/.claude/skills/p:requirements/update_tasks.py in_progress task-017
       2. Read task details from requirements.yaml
       3. Implement the task
       4. Run tests
       5. If all successful, run: python3 ~/.claude/skills/p:requirements/update_tasks.py completed task-017
     ```
   - **Never mark a task as completed if**:
     - Tests are failing
     - Build fails
     - Implementation is incomplete
     - User explicitly says not to mark it as completed
   - After updating status, optionally show updated task list to confirm progress

2. **Read the requirements file for detailed information**
   - Read `/mnt/nvme/imaginarium/poluah/requirements.yaml`
   - This file contains the complete project requirements specification

3. **Analyze the structure**
   The requirements.yaml contains:
   - `original_request`: The original Hungarian task description
   - `goal`: High-level project goals
   - `complete`: Overall completion status (true/false)
   - `requirements`: Array of requirement questions with answers and options
   - `constraints`: Technical and business constraints
   - `success_criteria`: Criteria for successful completion
   - `implementation_plan`: Detailed implementation plan with:
     - `affected_files`: Files to be modified
     - `new_files`: Files to be created
     - `reference_files`: Files to use as reference
     - `tasks`: Detailed task list with:
       - `task_id`: Unique task identifier
       - `description`: What the task involves
       - `file_path`: Target file
       - `function_name`: Function to implement (if applicable)
       - `type`: create/modify/delete
       - `implementation_details`: Detailed implementation instructions
       - `code_references`: Related code to reference
       - `api_references`: Documentation references
       - `test_requirements`: Testing criteria
       - `dependencies`: Task dependencies (other task_ids)

4. **Present relevant information**
   Based on the user's question, present:
   - If asking about what to implement: Show pending tasks with their descriptions
   - If asking about a specific feature: Find related tasks and requirements
   - If planning work: Show task dependencies and suggest a logical order
   - If asking about requirements: Show the requirements section
   - Always reference specific task IDs for clarity

5. **Filter tasks by phase**
   Tasks are organized in phases (PHASE 0, 1, 2, etc.):
   - PHASE 0: Infrastructure Setup
   - PHASE 1: Opening Handshake, Data Framing, Masking
   - PHASE 2: Control Frames (Close, Ping, Pong)
   - PHASE 3: UTF-8 Validation
   - PHASE 4: Fragmentation
   - PHASE 5: Reserved Bits Handling
   - PHASE 6: Extension Negotiation
   - PHASE 7: Additional Test Cases
   - Infrastructure tasks: poluah-wsproof Application
   - Build system tasks
   - Documentation tasks
   - Future enhancements

6. **Provide context**
   - Show task dependencies so the user knows what needs to be done first
   - Reference related documentation files from `api_references`
   - Highlight constraints that might affect implementation

## Example Interactions

**User:** "What tasks do I need to implement?"
**Response:** Read requirements.yaml and list all tasks with their task_ids, descriptions, and current status.

**User:** "Show me Phase 1 tasks"
**Response:** Filter and display all tasks with PHASE 1 in their description or task_id pattern.

**User:** "What's the next task after task-001-handshake-send?"
**Response:** Check dependencies, find tasks that depend on task-001-handshake-send, and suggest the next logical task (task-001-handshake-recv).

**User:** "Tell me about the handshake implementation"
**Response:** Find all tasks related to handshake, show their implementation_details and code_references.

**User:** "Show me details for task-005"
**Response:** Run `python3 ~/.claude/skills/p:requirements/show_task_details.py task-005` to display the raw YAML block with complete implementation details, dependencies, test requirements, and code references.

**User:** "What do tasks 010, 011, and 012 involve?"
**Response:** Run `python3 ~/.claude/skills/p:requirements/show_task_details.py task-010 task-011 task-012` to show the raw YAML blocks for all three tasks.

**User:** "Mark task-017 as completed"
**Response:** Run `python3 ~/.claude/skills/p:requirements/update_tasks.py completed task-017` to update the task status, then show confirmation.

**User:** "I finished task-020, task-021, and task-022"
**Response:** Run `python3 ~/.claude/skills/p:requirements/update_tasks.py completed task-020 task-021 task-022` to update all three tasks at once.

**User:** "Set tasks 023 through 026 back to pending"
**Response:** Run `python3 ~/.claude/skills/p:requirements/update_tasks.py pending task-023 task-024 task-025 task-026` for batch status update.

**User:** "Implement task-017"
**Response:**
1. Read task-017 details from requirements.yaml
2. Run `python3 ~/.claude/skills/p:requirements/update_tasks.py in_progress task-017`
3. Implement test-methods.c with 13 HTTP method tests
4. Run build and tests
5. If all pass, automatically run `python3 ~/.claude/skills/p:requirements/update_tasks.py completed task-017`
6. Show confirmation and updated progress

**User:** "Work on task-019 and task-020"
**Response:**
1. Run `python3 ~/.claude/skills/p:requirements/update_tasks.py in_progress task-019 task-020`
2. Implement both tasks
3. Run tests for each
4. When task-019 is done: `python3 ~/.claude/skills/p:requirements/update_tasks.py completed task-019`
5. When task-020 is done: `python3 ~/.claude/skills/p:requirements/update_tasks.py completed task-020`
6. Show final status

## Important Notes

- Always check if requirements.yaml exists before reading
- The file is YAML format, parse it carefully
- Task dependencies are critical - never suggest implementing a task before its dependencies are complete
- Reference specific task_ids when discussing tasks for clarity
- The implementation_details field contains crucial information for each task
- **Automatic status updates**: Always update task status when working on tasks from requirements.yaml:
  - Mark `in_progress` when starting
  - Mark `completed` when fully done (tests pass, build succeeds)
  - Use batch updates for multiple tasks
- **Status update discipline**: Only mark completed when ALL criteria are met (implementation done, tests pass, build succeeds)
- After status updates, optionally show the updated task list to confirm progress to the user
