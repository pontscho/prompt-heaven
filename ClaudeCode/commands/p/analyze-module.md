# Module/File Deep Analysis Command

You are performing a **deep code analysis** of a specific module or file. This is NOT a code review - you are analyzing and documenting existing code to understand and explain how it works.

## Your Task

1. **Identify the target**: Ask the user which file/module to analyze (if not already specified)

2. **Deep Analysis** - Perform a thorough examination:
   - **Purpose & Responsibility**: What is the main purpose of this module?
   - **Data Structures**: Key structs, enums, types defined and their roles
   - **Function Inventory**: List all major functions with brief purpose
   - **Core Algorithms**: Explain key algorithms or complex logic
   - **State Management**: How does it manage state? What are the state transitions?
   - **Dependencies**: What other modules/headers does it depend on?
   - **Memory Management**: Memory allocation patterns, ownership rules
   - **Error Handling**: How are errors detected and propagated?
   - **Threading/Concurrency**: Any multi-threading considerations?
   - **Performance Notes**: Critical performance aspects or bottlenecks

3. **Generate Documentation**:
   - **Determine output location**:
     - Check if `docs/analysis/` exists → use it
     - Otherwise check if `docs/` exists → create `docs/analysis/`
     - If no `docs/` directory → ask user where to save the analysis
   - Filename format: `analysis-{module-name}.md`
   - Include code references with line numbers (e.g., `file.c:123`)
   - Use diagrams (ASCII art) if helpful for understanding flow
   - Keep it technical but clear

4. **Summary**: Provide a brief verbal summary of the key findings in the chat

## Output Format

The documentation should follow this structure:

```markdown
# Module Analysis: {Module Name}

**File**: `path/to/file.c`
**Analysis Date**: {date}
**Lines of Code**: {count}

## Overview

Brief description of the module's purpose and role in the system.

## Data Structures

### `struct foo`
- **Purpose**: ...
- **Key Fields**: ...
- **Usage**: ...

## Function Reference

### Core Functions

#### `function_name()` (line: XX)
- **Purpose**: ...
- **Parameters**: ...
- **Returns**: ...
- **Key Logic**: ...

## Algorithms & Logic

Detailed explanation of key algorithms...

## Dependencies

- Module A: Used for X
- Module B: Provides Y

## Memory Management

How memory is allocated, freed, and managed...

## Error Handling

How errors are handled...

## Performance Considerations

Any performance-critical sections or potential bottlenecks...

## Notes & Observations

Additional insights, quirks, or important details...
```

## Important Notes

- This is ANALYSIS, not review - don't suggest improvements unless asked
- Be thorough but avoid unnecessary verbosity
- Focus on UNDERSTANDING the code, not judging it
- Include specific line references for important code sections
- If the module is large, ask the user if they want to focus on specific aspects

## Example Usage

```
/analyze-module src/server/request-handler.c
/analyze-module lib/utils/parser.py
/analyze-module pkg/database/connection.go
```

---

**Start by asking the user which module/file to analyze (if not already clear from context).**
