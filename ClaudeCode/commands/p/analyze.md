# Smart Code Analysis Command

You are performing an **intelligent code analysis**. Based on what the user wants to analyze, you will automatically determine the appropriate analysis type and perform it.

## Your Task

### Step 1: Understand the Target

Examine what the user wants to analyze (from their command or context):

**If the target is a specific file path** (contains `.c`, `.h`, `.lua`, `.py`, `.js`, `.ts`, `.go`, `.rs`, or file path like `src/...`):
- → Use **MODULE ANALYSIS** approach
- Example: `src/server/request-handler.c`, `lib/utils/parser.py`

**If the target is a header file OR mentions "API", "interface", "bindings", "public functions"**:
- → Use **API ANALYSIS** approach
- Example: `memory.h`, `HTTP Server API`, `Python bindings`

**If the target is a multi-word component/subsystem name** (like "HTTP server", "WebSocket implementation"):
- → Use **SUBSYSTEM ANALYSIS** approach
- Example: `HTTP server`, `Memory management`, `Request routing`

**If unclear or ambiguous**:
- → **ASK** the user which type of analysis they want:
  - Module: Deep dive into a specific file
  - Subsystem: Architecture of multiple related modules
  - API: User-facing reference documentation

### Step 2: Perform the Analysis

Based on your determination, follow the appropriate analysis approach:

---

## MODULE ANALYSIS

**When**: Single file/module deep dive

**Process**:
1. Read and thoroughly examine the target file
2. Analyze:
   - Purpose & Responsibility
   - Data Structures (structs, enums, types)
   - Function Inventory
   - Core Algorithms
   - State Management
   - Dependencies
   - Memory Management patterns
   - Error Handling
   - Threading/Concurrency
   - Performance Notes

3. **Determine output location**:
   - Check if `docs/analysis/` exists → use it
   - Otherwise check if `docs/` exists → create `docs/analysis/`
   - If no `docs/` directory → ask user where to save the analysis
   - Filename format: `analysis-{module-name}.md`

**Output Format**:
```markdown
# Module Analysis: {Module Name}

**File**: `path/to/file.c`
**Analysis Date**: {date}
**Lines of Code**: {count}

## Overview
...

## Data Structures
...

## Function Reference
...

## Algorithms & Logic
...

## Dependencies
...

## Memory Management
...

## Error Handling
...

## Performance Considerations
...

## Notes & Observations
...
```

---

## SUBSYSTEM ANALYSIS

**When**: Multi-module component architecture

**Process**:
1. Use the **Task tool with Explore agent** to map all relevant modules
2. Analyze:
   - High-Level Overview
   - Component Breakdown
   - Data Flow
   - Control Flow
   - Component Interactions
   - State Management
   - Threading Model
   - External Interfaces
   - Configuration
   - Initialization/Cleanup

3. **Determine output location**:
   - Check if `docs/analysis/` exists → use it
   - Otherwise check if `docs/` exists → create `docs/analysis/`
   - If no `docs/` directory → ask user where to save the analysis
   - Filename format: `subsystem-{name}.md`

**Output Format**:
```markdown
# Subsystem Analysis: {Subsystem Name}

**Analysis Date**: {date}

## Overview
...

## Architecture

### Component Diagram
```
[ASCII diagram]
```

### Components
...

## Data Flow

### Data Flow Diagram
...

## Key Operations

### Sequence Diagrams
...

## State Management
...

## Threading Model
...

## External Dependencies
...

## Configuration
...

## Initialization & Cleanup
...

## Error Handling
...

## Performance Characteristics
...

## Notes & Observations
...
```

---

## API ANALYSIS

**When**: User-facing API reference documentation

**Process**:
1. Identify all public functions, types, macros
2. Analyze:
   - Overview and use cases
   - Public Functions
   - Data Types
   - Constants/Macros
   - Usage Patterns
   - Initialization/Cleanup
   - Error Handling
   - Thread Safety
   - Memory Ownership
   - Examples
   - Limitations

3. **Determine output location**:
   - Check if `docs/api/` exists → use it
   - Otherwise check if `docs/` exists → create `docs/api/`
   - If no `docs/` directory → ask user where to save the documentation
   - Filename format: `api-{name}.md`

**Output Format**:
```markdown
# API Reference: {API Name}

**Header**: `path/to/header.h`

## Overview
...

## Quick Start
```c
// Minimal example
```

## Data Types
...

## Constants & Macros
...

## Functions

### Function Name
```c
signature
```

**Description**: ...
**Parameters**: ...
**Returns**: ...
**Example**: ...
**Notes**: ...

## Usage Patterns
...

## Memory Management
...

## Error Handling
...

## Thread Safety
...

## Performance Notes
...

## Examples
...

## Limitations
...

## Related APIs
...
```

---

## Important Guidelines

### For ALL Analysis Types:
- This is **ANALYSIS**, not code review - document, don't critique
- Include specific line numbers for references (e.g., `file.c:123`)
- Use ASCII diagrams where helpful
- Be thorough but concise
- Focus on understanding, not judging
- Create the output file automatically (don't just output to chat)
- Provide a brief summary in chat after creating the doc

### Decision Logic Summary:
```
Input Analysis:
├─ Has file extension (.c, .h, .lua)? → MODULE
├─ Contains "API" or "interface"? → API
├─ Is header file (.h)? → API
├─ Multi-word component name? → SUBSYSTEM
└─ Unclear? → ASK USER
```

## Example Usage

```bash
/analyze src/server/request-handler.c
# → Automatically does MODULE analysis

/analyze HTTP server
# → Automatically does SUBSYSTEM analysis

/analyze memory API
# → Automatically does API analysis

/analyze routing
# → Ambiguous - asks user: module, subsystem, or API?
```

## Tip

If the user provides a file path, you can automatically detect it. If they just say "analyze X", you might need to search for relevant files first to understand the scope.

---

**Now proceed with analyzing the user's target. If they haven't specified what to analyze yet, ask them!**
