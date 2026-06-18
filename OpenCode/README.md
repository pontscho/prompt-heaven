# OpenCode Prompt Heaven

A comprehensive collection of Claude Code agents, commands, and skills designed to assist with various software engineering tasks. This repository provides pre-configured tools for code exploration, analysis, planning, and implementation across multiple programming languages and domains.

## Table of Contents

- [Agents](#agents)
- [Commands](#commands)
- [Skills](#skills)
- [Usage Guide](#usage-guide)
- [Quick Reference](#quick-reference)

---

## Agents

Agents are specialized AI assistants that handle complex, multi-step tasks autonomously. Each agent is configured with specific permissions, constraints, and workflows.

### p:code-explore

**Purpose:** Searches GitHub repositories for code patterns and implementations.

**What it does:**
- Uses grep.app to search for code patterns across millions of public GitHub repositories
- Generates targeted search queries based on your topic
- Parses results to extract repository URLs and relevant code snippets
- Fetches file contents from interesting repositories
- Creates detailed code reports with multiple examples and implementation comparisons

**When to use:**
- Learning how a particular pattern is implemented in real projects
- Finding examples of API usage or library integration
- Researching best practices for a specific coding pattern
- Comparing different approaches to solving the same problem

**Example:**
```
Research how WebSocket heartbeat/ping-pong implementations are done in production projects.
```

**Output:** A structured report with repository links, code examples, and analysis of different implementation approaches.

---

### p:explore

**Purpose:** Combined web and code search for comprehensive research.

**What it does:**
- Runs parallel searches using both web search (DuckDuckGo) and code search (GitHub)
- Combines documentation resources with practical code examples
- Creates unified reports with both conceptual understanding and implementation patterns
- Handles errors gracefully if one search source fails

**When to use:**
- Researching a new technology or framework
- Learning both the theory and practice of a concept
- Finding tutorials alongside code examples
- When you need both "what is it" and "how to use it" information

**Example:**
```
Explore Python dataclasses best practices - both documentation and real-world usage.
```

**Output:** A combined report with web resources (tutorials, docs) and code examples from GitHub.

---

### p:web-explore

**Purpose:** Deep web research using DuckDuckGo search.

**What it does:**
- Performs web searches using DuckDuckGo (privacy-focused search engine)
- Generates multiple search queries to cover different angles of a topic
- Fetches and parses content from relevant URLs
- Creates detailed reports with key findings and source attribution

**When to use:**
- Finding documentation, tutorials, and technical articles
- Researching technology comparisons
- Looking for official documentation and guides
- Finding solutions to technical problems discussed online

**Example:**
```
Search for recent developments in WebSocket protocol extensions and browser support.
```

**Output:** A comprehensive report with key findings from multiple web sources, properly attributed.

---

## Commands

Commands are slash commands that provide specialized functionality for common development tasks. They are prefixed with `/` and provide intelligent, context-aware automation.

### /analyze

**Purpose:** Smart code analysis that automatically detects the appropriate analysis type.

**What it does:**
- Analyzes your input to determine the correct analysis approach:
  - **Module Analysis** for single files
  - **API Analysis** for interfaces/headers
  - **Subsystem Analysis** for multi-module components
- Automatically routes to the appropriate specialized command
- Creates comprehensive documentation files

**When to use:**
- Quick analysis without needing to know which specific command to use
- When you're unsure what type of analysis you need
- General code exploration and documentation tasks

**Example:**
```
/analyze src/server/request-handler.c
/analyze HTTP server
/analyze memory API
```

**Output:** Markdown documentation file saved to `docs/analysis/` or `docs/api/` depending on analysis type.

---

### /analyze-module

**Purpose:** Deep-dive analysis of a single file or module.

**What it does:**
- Performs comprehensive analysis of a single source file
- Documents purpose, data structures, functions, algorithms
- Maps dependencies and external interfaces
- Identifies memory management patterns
- Analyzes error handling and threading model
- Creates a detailed reference document

**When to use:**
- Understanding what a specific file does
- Documenting algorithms and data structures
- Mapping internal dependencies
- Preparing for refactoring
- Learning unfamiliar code

**Example:**
```
/analyze-module lib/utils/parser.py
/analyze-module src/core/memory-manager.c
```

**Output:** `docs/analysis/analysis-{module-name}.md` containing:
- Overview and purpose
- Data structures and types
- Function reference
- Algorithms and logic
- Dependencies
- Memory management patterns
- Error handling
- Performance considerations

---

### /analyze-subsystem

**Purpose:** Architecture analysis of multi-module components.

**What it does:**
- Maps all modules in a component/subsystem
- Analyzes data flow between components
- Documents control flow and interactions
- Creates architectural diagrams (ASCII)
- Identifies state management approaches
- Documents threading/concurrency models

**When to use:**
- Understanding system architecture
- Mapping complex component interactions
- Documenting how multiple modules work together
- Preparing for major refactoring
- Onboarding to a new codebase

**Example:**
```
/analyze-subsystem HTTP server
/analyze-subsystem WebSocket implementation
/analyze-subsystem Memory management
```

**Output:** `docs/analysis/subsystem-{name}.md` containing:
- Architecture overview with component diagrams
- Data flow diagrams
- Sequence diagrams for key operations
- State management documentation
- Threading model analysis
- External dependencies

---

### /analyze-api

**Purpose:** User-facing API reference documentation.

**What it does:**
- Identifies all public functions, types, macros
- Creates comprehensive API documentation
- Includes usage examples and code snippets
- Documents error handling and thread safety
- Explains memory ownership semantics
- Provides migration guides for version changes

**When to use:**
- Documenting public libraries
- Creating API reference documentation
- Explaining how to use interfaces
- Preparing library releases
- Onboarding users to a new API

**Example:**
```
/analyze-api HTTP Server API
/analyze-api memory management functions
/analyze-api src/include/api.h
```

**Output:** `docs/api/api-{name}.md` containing:
- Overview and quick start guide
- Data types documentation
- Constants and macros
- Function reference with examples
- Usage patterns
- Memory management guide
- Error handling documentation
- Thread safety guarantees
- Complete code examples

---

### /task-plan

**Purpose:** Collaborative feature planning and requirements gathering.

**What it does:**
- Explores codebase to find similar patterns and conventions
- Guides iterative requirements gathering through structured questions
- Organizes questions by category (architecture, dependencies, data, security, implementation)
- Creates comprehensive implementation plan in YAML format
- Generates technical specification document
- Produces function-level task breakdown with dependencies

**When to use:**
- Planning new features or functionality
- Breaking down complex implementations
- Ensuring complete requirements before coding
- Creating implementation roadmaps
- Documenting technical decisions

**Workflow:**
1. Explores codebase for existing patterns
2. Generates prioritized requirements questions
3. Iterates with user to gather answers
4. Creates YAML implementation plan with tasks
5. Generates technical specification document

**Output:**
- `requirements.yaml` - Complete implementation plan
- `docs/{feature}-spec.md` - Technical specification

---

### /feature-plan

**Purpose:** Comprehensive feature design and architecture planning.

**What it does:**
- Explores codebase thoroughly for patterns and conventions
- Analyzes existing tests, security patterns, error handling
- Evaluates multiple implementation approaches with pros/cons
- Designs complete solution architecture
- Creates detailed implementation plan in English
- Documents non-functional requirements, error handling, testing strategy
- Plans monitoring, observability, and documentation updates

**When to use:**
- Planning significant new features
- Architecture review and design
- Complex multi-component features
- When detailed technical planning is needed
- Design documentation for team collaboration

**Output:** `docs/feature-implementation-plan.md` containing:
- Requirements summary with success criteria
- Architecture analysis
- Alternative approaches evaluation
- Implementation strategy
- Data model/API changes
- Backwards compatibility and migration
- Step-by-step implementation plan
- Error handling and edge cases
- Testing strategy
- Monitoring and observability
- Documentation requirements

---

### /implement

**Purpose:** Execute implementation plans from requirements.yaml.

**What it does:**
- Reads implementation plan from YAML file
- Validates plan completeness and dependencies
- Executes tasks in dependency order
- Follows code reference patterns from plan
- Runs quality checks (clang-tidy, build, tests)
- Automatically updates task status in YAML
- Provides progress tracking and error recovery

**When to use:**
- Executing planned feature implementations
- Resuming interrupted implementations
- Automated task execution from requirements.yaml
- Following structured development workflow

**Example:**
```
/p:implement
/p:implement --plan ./docs/feature-plan.yaml
/p:implement --continue
```

**Key Features:**
- Automatic task status tracking
- Code reference pattern enforcement
- Language-specific quality checks
- Build verification after each task
- Test execution and validation
- Resume capability for interrupted work

---

### /project-explore

**Purpose:** Quick project structure exploration and overview.

**What it does:**
- Analyzes project structure and organization
- Identifies main components and their relationships
- Maps build system and configuration
- Identifies key files and their purposes
- Provides quick context for new projects

**When to use:**
- Exploring a new codebase
- Understanding project organization
- Finding relevant files for a task
- Quick project assessment

**Output:** Structured overview of project structure, components, and key files.

---

## Skills

Skills provide specialized knowledge and guidelines for specific programming languages, frameworks, or domains. They are activated automatically when working with relevant code or tasks.

### p:c / p:c-cpp-guidelines

**Purpose:** C and C++ coding best practices and guidelines.

**What it covers:**
- Memory management (malloc/free, new/delete, smart pointers)
- Code style (tab indentation, snake_case, const correctness)
- Doxygen documentation with @ parameters
- Error handling patterns
- Performance optimization
- Code quality verification (clang-tidy, cppcheck, ASan)

**When to use:**
- Writing or editing C/C++ source files
- Implementing memory allocation or resource management
- Documenting functions and structures
- Reviewing C/C++ code quality
- Setting up static analysis

**Key Patterns:**
- Early returns for error handling
- NULL pointer safety
- RAII for resource cleanup (C++)
- Tab indentation, snake_case naming
- Doxygen documentation style

---

### p:Lua

**Purpose:** Lua coding guidelines and best practices.

**What it covers:**
- Code style (tab indentation, camelCase naming)
- Doxygen-style documentation with -- comments
- Table utility functions (isempty, deepcopy, merge)
- Module patterns and organization
- Error handling and nil checking
- Logging conventions (polua.log)

**When to use:**
- Writing or editing Lua files
- Implementing Lua modules
- Working with tables and data structures
- Documenting Lua functions
- Following Lua project conventions

**Key Patterns:**
- camelCase for variables and functions
- Tab indentation
- Table utility functions
- Module return pattern
- Doxygen-style -- comments

---

### p:cmake

**Purpose:** Modern CMake best practices for build configuration.

**What it covers:**
- Target-based CMake configuration
- Cross-platform build support
- Static linking patterns (Linux, macOS, Windows)
- Dependency management (pkg-config, find_library)
- Compiler flag management
- Generator expressions for conditional builds

**When to use:**
- Writing or modifying CMakeLists.txt
- Setting up cross-platform builds
- Configuring static linking
- Managing library dependencies
- Optimizing build configurations

**Key Patterns:**
- target_link_libraries with PRIVATE/PUBLIC/INTERFACE
- Platform-specific configurations
- Fallback dependency finding strategies
- Generator expressions for conditional logic
- Static linking verification

---

### p:static-linking

**Purpose:** Building and verifying statically linked binaries.

**What it covers:**
- Platform-specific static linking configurations
- Linux full static linking
- macOS hybrid static linking (static third-party, dynamic system)
- Windows /MT runtime configuration
- Verification tools and techniques
- Troubleshooting common issues

**When to use:**
- Creating standalone portable binaries
- Configuring static builds for distribution
- Verifying static linking success
- Troubleshooting linking errors
- Cross-platform deployment preparation

**Key Tools:**
- `build-static.py` - Automated static build script
- `verify-static-linking.py` - Post-build verification
- Platform-specific linker flags

---

### p:code-analysis

**Purpose:** Code analysis commands and documentation guidelines.

**What it covers:**
- Module analysis (/analyze-module)
- Subsystem analysis (/analyze-subsystem)
- API documentation (/analyze-api)
- Smart analysis routing (/analyze)
- Output file organization
- Documentation best practices

**When to use:**
- Understanding how to use analysis commands
- Learning what each analysis type produces
- Creating code documentation
- Planning documentation strategy

**Quick Reference:**
| Target Type | Command | Output |
|-------------|---------|--------|
| Single file | /analyze-module | Module analysis |
| Multi-module | /analyze-subsystem | Architecture doc |
| Public API | /analyze-api | API reference |

---

### p:requirements

**Purpose:** Requirements.yaml management and task tracking.

**What it covers:**
- Reading and parsing requirements.yaml
- Task status display and updates
- Dependency tracking between tasks
- Batch task status updates
- Implementation progress tracking
- Task detail extraction

**When to use:**
- Viewing project tasks and status
- Updating task completion status
- Checking implementation progress
- Understanding task dependencies
- Resuming interrupted implementations

**Key Commands:**
```bash
# Show all tasks
python3 ~/.claude/scripts/task-show-all.py requirements.yaml

# Show task details
python3 ~/.claude/scripts/task-show-details.py task-001

# Update task status
python3 ~/.claude/scripts/task-update.py completed task-001
```

**Task Statuses:**
- `pending` - Not started
- `in_progress` - Currently being worked on
- `completed` - Finished and verified
- `cancel` - Cancelled/deferred

---

### task-implementation-plan.py (Script)

**Purpose:** Efficient extraction of implementation plan data.

**What it does:**
- Extracts only essential implementation data from requirements.yaml
- Provides token-efficient access to tasks and dependencies
- Outputs compact YAML without full requirements context
- Supports custom YAML file paths

**When to use:**
- Getting just the implementation tasks
- Executing /p:implement command
- Avoiding loading full requirements.yaml
- Token-efficient task access

**Usage:**
```bash
~/.claude/scripts/task-implementation-plan.py [path_to_yaml]
```

**Output:** Compact YAML with:
- Complete flag
- Context summary (captured patterns from planning)
- Success criteria
- Implementation plan (affected files, new files, tasks with pattern_excerpts)

---

### p:project-docs

**Purpose:** Project documentation writing standards.

**What it covers:**
- Markdown structure guidelines
- RFC/specification compliance
- API documentation format
- Test roadmap documentation
- Implementation specification writing
- Conformance test suite documentation

**When to use:**
- Writing project documentation
- Creating RFCs or specifications
- Documenting APIs
- Planning implementation roadmaps
- Writing test documentation

---

### p:writer-agent

**Purpose:** Claude Code agent prompt writing best practices.

**What it covers:**
- Effective agent prompt structure
- Permission and constraint definition
- Workflow design patterns
- Output format specification
- Agent configuration best practices

**When to use:**
- Creating custom agents
- Modifying existing agent prompts
- Designing agent workflows
- Optimizing agent performance

---

### p:writer-skill

**Purpose:** Creating and improving Claude Code skills.

**What it covers:**
- SKILL.md file structure
- Frontmatter configuration
- Skill activation conditions
- Instruction formatting
- Skill quality guidelines

**When to use:**
- Creating new skills
- Improving existing skills
- Configuring skill triggers
- Documenting skill capabilities

---

### p:ctest.h

**Purpose:** C unit testing with ctest.h framework.

**What it covers:**
- CTEST/CTEST2 macro usage
- Assertion macros
- Test organization
- Setup/teardown patterns
- Test file structure

**When to use:**
- Writing C unit tests
- Creating integration tests
- Testing C/C++ code
- Following project test conventions

**Key Macros:**
- CTEST() - Test case definition
- ASSERT_* - Assertions (EQ, NE, TRUE, FALSE, etc.)
- SETUP() / TEARDOWN() - Test fixture

---

## Usage Guide

### Getting Started

1. **Choose the right tool** based on your task:
   - Need code patterns? Use `/p:code-explore`
   - Need documentation? Use `/analyze-*` commands
   - Planning new features? Use `/task-plan` or `/feature-plan`
   - Implementing features? Use `/p:implement`
   - Writing code? Activate relevant skills for language guidance

2. **Check requirements.yaml** when implementing features:
   ```bash
   /p:requirements
   ```

3. **Follow the workflow** for structured development:
   - Plan → Implement → Verify → Document

### Typical Workflows

#### Code Exploration
```
1. /p:explore [topic]           # Research topic with web and code search
2. /analyze-module [file]       # Understand specific files
3. /analyze-subsystem [module]  # Understand architecture
```

#### Feature Implementation
```
1. /task-plan                   # Plan feature with requirements gathering
2. /p:implement                 # Execute implementation
3. /p:requirements              # Track progress
4. /analyze-api [api]           # Document public interfaces
```

#### Documentation
```
1. /analyze-module [file]       # Document a module
2. /analyze-subsystem [system]  # Document architecture
3. /analyze-api [interface]     # Document API
```

### Command Parameters

| Command | Parameters | Description |
|---------|------------|-------------|
| `/analyze` | `<target>` | Smart analysis with auto-detection |
| `/analyze-module` | `<file>` | Single file analysis |
| `/analyze-subsystem` | `<component>` | Multi-module analysis |
| `/analyze-api` | `<api-name>` | API documentation |
| `/task-plan` | None | Feature planning |
| `/feature-plan` | None | Detailed architecture planning |
| `/p:implement` | `--plan <path>` `--continue` `--task <id>` | Execute implementation |
| `/project-explore` | None | Project overview |

---

## Quick Reference

### Decision Tree: Which Command to Use?

```
What do you need?
│
├─ Code patterns from GitHub?
│  └─ /p:code-explore
│
├─ Combined web + code research?
│  └─ /p:explore
│
├─ Web research only?
│  └─ /p:web-explore
│
├─ Code documentation?
│  │
│  ├─ Single file?
│  │  └─ /analyze-module
│  │
│  ├─ Architecture (multiple files)?
│  │  └─ /analyze-subsystem
│  │
│  └─ Public API?
│     └─ /analyze-api
│
├─ Planning new feature?
│  │
│  ├─ Quick requirements gathering?
│  │  └─ /task-plan
│  │
│  └─ Detailed architecture?
│     └─ /feature-plan
│
└─ Implementing planned feature?
   └─ /p:implement
```

### Skill Activation

Skills activate automatically based on context:

| Skill | Activated When |
|-------|---------------|
| `p:c` | Working with .c, .cpp, .h, .hpp files |
| `p:Lua` | Working with .lua files |
| `p:cmake` | Working with CMakeLists.txt or cmake files |
| `p:code-analysis` | Using /analyze commands |
| `p:requirements` | Mentioning requirements.yaml or tasks |
| `p:static-linking` | Configuring static builds |

---

## File Structure

```
OpenCode/
├── agents/                    # Claude Code agents
│   ├── p:code-explore.md     # GitHub code search agent
│   ├── p:explore.md          # Combined web/code search
│   └── p:web-explore.md      # Web search agent
│
├── commands/                  # Slash commands (symlinks)
│   ├── p:analyze.md          # Smart analysis router
│   ├── p:analyze-module.md   # Module analysis
│   ├── p:analyze-subsystem.md # Subsystem analysis
│   ├── p:analyze-api.md      # API documentation
│   ├── p:task-plan.md        # Feature planning
│   ├── p:feature-plan.md     # Architecture planning
│   ├── p:implement.md        # Implementation execution
│   └── p:project-explore.md  # Project overview
│
└── skills/                    # Skills (symlinks to ClaudeCode/skills/)
    ├── p:c/                   # C/C++ guidelines
    ├── p:Lua/                 # Lua guidelines
    ├── p:cmake/               # CMake best practices
    ├── p:static-linking/      # Static linking guide
    ├── p:code-analysis/       # Analysis commands guide
    ├── p:requirements/        # Requirements management
    ├── p:project-docs/        # Documentation standards
    ├── p:writer-agent/        # Agent writing guide
    ├── p:writer-skill/        # Skill writing guide
    └── p:ctest.h/             # C testing framework
```

---

## Best Practices

1. **Always check requirements.yaml** before starting implementation work
2. **Use /analyze commands** to document code as you work
3. **Follow code reference patterns** from existing codebase
4. **Run quality checks** (clang-tidy, tests) after each change
5. **Update task status** as you progress through implementation
6. **Use skills** for language-specific conventions and patterns
7. **Document APIs** as you create them with /analyze-api
8. **Plan before implementing** using /task-plan or /feature-plan

---

## License

This repository contains Claude Code configuration files for prompt heaven - a collection of pre-configured agents, commands, and skills to enhance development workflow.
