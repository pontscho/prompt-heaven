Plan Feature Command

# Schema

```yaml
original_request: string
goal: string
complete: boolean
requirements:
  - category: architecture|dependencies|data|security|interface|implementation
    question: string
    answer: string? # optional
    details: [string]      # technical implications
    options: [string]
    status: pending|answered
constraints:
  - type: technical|business|security
    description: string
    impact: string
success_criteria: [string]
implementation_plan:
  affected_files: [string]          # existing files to be modified
  new_files: [string]               # new files to be created
  reference_files: [string]         # SOURCE CODE files with similar patterns to follow
  tasks:
    - task_id: string               # unique identifier (e.g., "task-001")
      description: string           # what needs to be done
      file_path: string             # absolute path to file
      function_name: string?        # function to modify/create (optional)
      type: create|modify|delete|test
      status: pending|completed|cancel # task completion status (default: pending)
      implementation_details: string # specific technical approach
      code_references:              # similar CODE implementations in codebase
        - file: string              # path to source code file
          function: string          # function name (or equivalent: method, module function, etc.)
          note: string              # why this reference is relevant
      api_references: [string]      # DOCUMENTATION files (.md, .txt, etc.) in docs/ directory
      test_requirements: string     # how to verify this task
      dependencies: [string]        # task_ids that must complete first
```

## Field Descriptions

- `original_request`: The original input from the User, without any changes
- `goal`: A high level description of what the goal is based on the user prompt
- `complete`: Indicates that the requirement gathering has been fully completed or not.
- `requirements`: array of questions and answers with their current status
  - `question`: The requirement question text
  - `answer`: Response to the question (optional, only present when status is "answered")
  - `details`: Technical implications and additional context
  - `options`: Available choices for the question
  - `category`: Question domain - "architecture", "dependencies", "data", "security", "interface", "implementation"
  - `status`: Current state - "pending" or "answered"
- `constraints`: Technical, business, or security limitations that affect implementation
  - `type`: Constraint category - "technical", "business", "security"
  - `description`: Clear description of the constraint
  - `impact`: How this constraint affects the implementation
- `success_criteria`: Simple array of success criteria descriptions that define project success
- `implementation_plan`: Detailed, function-level task breakdown for implementation
  - `affected_files`: List of existing files that will be modified
  - `new_files`: List of new files that will be created
  - `reference_files`: **Source code files** in codebase with similar patterns to follow (any language: C, Lua, Python, etc.)
  - `tasks`: Array of implementation tasks (function-level granularity)
    - `task_id`: Unique identifier for dependency tracking
    - `description`: Clear description of what needs to be done
    - `file_path`: Absolute path to the file being modified/created
    - `function_name`: Specific function to modify or create (optional, can be method name, module function, etc.)
    - `type`: Task type - "create", "modify", "delete", "test"
    - `status`: Task completion status - "pending" (default), "completed" or "cancel"
    - `implementation_details`: Specific technical approach and requirements
    - `code_references`: Similar **code implementations** in codebase to follow as examples (specific source file + function/method pairs)
    - `api_references`: **Documentation files** in docs/ directory relevant to this task (e.g., .md, .txt)
    - `test_requirements`: How to verify this task works correctly
    - `dependencies`: Task IDs that must be completed before this task

# Schema language

English.

# Usage Guidelines

- Keep descriptions concise and clear
- Use arrays for lists to maintain order
- Mark individual requirement statuses as "pending" or "answered"
- Answered requirements must contain enough detail for future reference
- Focus on essentials, avoid feature creep

# Requirement gathering

Collaboratively discover comprehensive requirements with the User through efficient, iterative analysis.

IMPORTANT: output of this step is the sole input for task generation. IT MUST BE comprehensive and technically precise.

## Implementation document

The final output of the plan command is a YAML document that serves as input for the `implement` command.

- Create a comprehensive implementation plan in YAML format (requirements.yaml in project root)
- Add a detailed technical specification document to the `docs/` folder, naming the file according to the feature being implemented
- The YAML must include:
  - All gathered requirements with answers
  - All constraints and their impacts
  - Success criteria
  - **implementation_plan section with function-level tasks**
- Each task must be:
  - **Function-level granular** (not file-level or line-level)
  - Specific and actionable
  - Include exact file paths and function names
  - Reference similar code patterns in the codebase
  - Link to relevant API documentation
  - Define clear test requirements
  - Specify dependencies on other tasks
- The tasks must be ordered according to dependencies (tasks without dependencies first)
- The document must contain all information needed for autonomous implementation without requiring additional research

## Workflow

0. Don't forget read that fuckin' CLAUDE.md !
1. Search repository for existing patterns, similar implementations, and architectural decisions:
   - Use Glob and Grep to find similar **code patterns** (source code in any language: C, Lua, Python, etc.)
   - Search for relevant **documentation** (in docs/ directory)
   - Identify source code files with comparable functionality
   - Document these reference files (source code) and specific functions/methods that can serve as examples
   - Note why each reference is relevant (e.g., "similar error handling pattern", "same API structure")
   - Link documentation that explains APIs, protocols, or architecture
2. Think hard to determine complexity, approach (integration/implementation), and affected files
3. Collect a set of questions and put them in `requirements.yaml` (in project root) file based on #Schema
4. Prioritize questions in this order:
   a. Architecture & Approach: Core technical decisions
   b. Dependencies & Integration: External systems, libraries, APIs, interfaces, types
   c. Data & State Management: Storage, persistence, state handling
   d. Security & Performance: Authentication, authorization, scalability requirements, handling sensitive data
   e. Interface & UX: User interactions, API contracts
   f. Implementation Details: Specific technical approaches
5. Iterate over the all of these questions ONE BY ONE with the User:
   a. Review existing answers for gaps and dependencies
   b. Focus on architectural/foundational decisions before implementation details
   c. ASK the User specific, relevant, concise questions ONE AT A TIME:
      - number all options for easy answering
      - include technical context when relevant
      - suggest an answer based on the patterns and conventions identified
   d. Immediately update YAML with answer and refine the remaining questions. Add new questions if necessary!
   e. THINK HARD to determine if sufficient clarity exists for technical specification
6. After all questions has been discussed, verify if the output allows:
   - Unambiguous implementation approach
   - Complete dependency identification
   - Measurable success criteria
   - Risk/constraint awareness
   - Creation of clear, explicit technical tasks to achieve all aspects of the goal set by the User
   If any of these criteria are not met, go back to '2.'
7. Once all criteria are satisfied and mark `complete` is `true` in the YAML:
   a. Create the `implementation_plan` section with function-level tasks:
      - Identify all affected and new files
      - Break down implementation into function-level tasks
      - For each task, specify: file, function, implementation details, code references, test requirements
      - **IMPORTANT**: Set `status: pending` for all newly created tasks
      - **IMPORTANT**: Populate `code_references` with the similar **code implementations** found in step 1
      - For each code reference, specify: source file path, function/method name, and a note explaining why it's relevant
      - Add **source code files** with similar patterns to `reference_files` list
      - Link relevant **documentation files** in `api_references` for each task
      - Establish task dependencies and ordering
      - Ensure each task is independently testable
   b. Create a technical specification document in `docs/` folder with:
      - Architecture overview
      - Implementation approach
      - File and function organization
      - Testing strategy
      - References to requirements.yaml
   c. Verify the implementation_plan is complete and unambiguous

## Example Implementation Plan

Here's an example of a complete implementation_plan section for adding WebSocket ping/pong support:

```yaml
implementation_plan:
  affected_files:
    - /mnt/nvme/imaginarium/poluah/src/poluah/websocket-server.c
    - /mnt/nvme/imaginarium/poluah/src/poluah/websocket-server.h
  new_files:
    - /mnt/nvme/imaginarium/poluah/src/tests/integration/test-websocket-ping-pong.c
  reference_files:
    - /mnt/nvme/imaginarium/poluah/src/poluah/websocket-server.c  # existing timeout handling
    - /mnt/nvme/imaginarium/poluah/src/poluah-client2/poluah-client2-websocket.c  # client-side frame handling
  tasks:
    - task_id: task-001
      description: Add ping/pong frame type constants to WebSocket header
      file_path: /mnt/nvme/imaginarium/poluah/src/poluah/websocket-server.h
      function_name: null
      type: modify
      status: pending
      implementation_details: |
        Add WS_FRAME_PING (0x09) and WS_FRAME_PONG (0x0A) constants to the existing
        frame type enum. Follow the pattern of existing WS_FRAME_* constants.
      code_references:
        - file: /mnt/nvme/imaginarium/poluah/src/poluah/websocket-server.h
          function: null
          note: See existing WS_FRAME_TEXT and WS_FRAME_BINARY definitions
      api_references:
        - docs/websocket-protocol.md
      test_requirements: Verify constants are defined and have correct hex values
      dependencies: []

    - task_id: task-002
      description: Implement poluah_websocket_send_ping function
      file_path: /mnt/nvme/imaginarium/poluah/src/poluah/websocket-server.c
      function_name: poluah_websocket_send_ping
      type: create
      status: pending
      implementation_details: |
        Create new function that sends a ping frame with optional payload.
        Use poluah_websocket_send_frame() internally. Function signature:
        int poluah_websocket_send_ping(poluah_websocket_t *ws, const char *payload, size_t len)
        Return 0 on success, -1 on error.
      code_references:
        - file: /mnt/nvme/imaginarium/poluah/src/poluah/websocket-server.c
          function: poluah_websocket_send_frame
          note: Use this to build and send the ping frame
      api_references:
        - docs/websocket-api.md
      test_requirements: Send ping with payload, verify frame is correctly formatted
      dependencies: [task-001]

    - task_id: task-003
      description: Handle incoming ping frames and auto-respond with pong
      file_path: /mnt/nvme/imaginarium/poluah/src/poluah/websocket-server.c
      function_name: poluah_websocket_handle_frame
      type: modify
      status: pending
      implementation_details: |
        In poluah_websocket_handle_frame(), add case for WS_FRAME_PING.
        When ping is received, automatically send pong with same payload.
        Use poluah_websocket_send_frame() with WS_FRAME_PONG type.
      code_references:
        - file: /mnt/nvme/imaginarium/poluah/src/poluah/websocket-server.c
          function: poluah_websocket_handle_frame
          note: See existing switch statement for frame type handling
      api_references:
        - docs/websocket-protocol.md
      test_requirements: Send ping to server, verify pong response with matching payload
      dependencies: [task-001, task-002]

    - task_id: task-004
      description: Create integration test for ping/pong functionality
      file_path: /mnt/nvme/imaginarium/poluah/src/tests/integration/test-websocket-ping-pong.c
      function_name: test_ping_pong_basic
      type: create
      status: pending
      implementation_details: |
        Create new integration test file with test cases:
        1. test_ping_pong_basic - send ping, verify pong response
        2. test_ping_with_payload - send ping with data, verify echo in pong
        3. test_multiple_pings - verify server handles multiple pings correctly
        Use ctest framework, follow pattern from test-websocket-server-integration2.c
      code_references:
        - file: /mnt/nvme/imaginarium/poluah/src/tests/integration/poluah-websocket-client-integration-test.c
          function: null
          note: Follow overall test structure and WebSocket client setup
      api_references:
        - docs/testing-guide.md
      test_requirements: All test cases must pass
      dependencies: [task-003]
```
