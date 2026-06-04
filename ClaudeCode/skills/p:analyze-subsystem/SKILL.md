# Subsystem/Component Analysis Command

You are performing a **subsystem-level code analysis**. This involves mapping out how multiple modules work together to form a larger component or subsystem. This is NOT a code review - you are documenting the architecture and interactions.

## Your Task

1. **Identify the subsystem**: Ask the user which subsystem/component to analyze (e.g., "HTTP server", "WebSocket implementation", "Memory management", etc.)

2. **Exploration Phase** - Map the subsystem:
   - Identify all files/modules that are part of this subsystem
   - Understand the boundaries and interfaces
   - Map dependencies (internal and external)

3. **Architecture Analysis**:
   - **High-Level Overview**: What does this subsystem do?
   - **Component Breakdown**: What are the major modules/files and their roles?
   - **Data Flow**: How does data flow through the subsystem?
   - **Control Flow**: What is the typical execution path?
   - **Interactions**: How do components communicate with each other?
   - **State Management**: How is state shared or isolated?
   - **Threading Model**: Is it single-threaded, multi-threaded, async?
   - **External Interfaces**: What APIs does it expose? What does it consume?
   - **Configuration**: How is the subsystem configured?
   - **Initialization/Cleanup**: Lifecycle management

4. **Generate Documentation**:
   - **Determine output location**:
     - Check if `docs/analysis/` exists → use it
     - Otherwise check if `docs/` exists → create `docs/analysis/`
     - If no `docs/` directory → ask user where to save the analysis
   - Filename format: `subsystem-{name}.md`
   - Include architecture diagrams (ASCII art)
   - Include sequence diagrams for key operations
   - Reference specific files and line numbers where relevant

5. **Summary**: Provide a brief verbal summary in the chat

## Output Format

```markdown
# Subsystem Analysis: {Subsystem Name}

**Analysis Date**: {date}
**Version/Commit**: {git hash}

## Overview

High-level description of what this subsystem does and its role in the project.

## Architecture

### Component Diagram

```
┌─────────────────┐
│   Component A   │
│  (file.c:123)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐      ┌─────────────────┐
│   Component B   │─────▶│   Component C   │
└─────────────────┘      └─────────────────┘
```

### Components

#### Component A (`path/to/file.c`)
- **Role**: ...
- **Key Functions**: ...
- **Responsibilities**: ...

## Data Flow

Describe how data moves through the subsystem:

1. Input from X
2. Processing by Y
3. Output to Z

### Data Flow Diagram

```
[Client] ──request──▶ [Parser] ──parsed──▶ [Handler] ──response──▶ [Client]
```

## Key Operations

### Operation: Handle HTTP Request

**Sequence**:
1. `function_a()` receives request (file.c:100)
2. `function_b()` parses headers (parser.c:50)
3. `function_c()` routes to handler (router.c:200)
4. ...

### Sequence Diagram

```
Client          Parser          Handler
  │               │               │
  ├──request────▶ │               │
  │               ├──parse──────▶ │
  │               │               ├─process
  │               │ ◀───result────┤
  │ ◀──response───┤               │
```

## State Management

How state is managed across components...

## Threading Model

Single-threaded/multi-threaded, synchronization mechanisms...

## External Dependencies

- Module/Library X: Used for Y
- System API Z: Provides W

## Configuration

Configuration options and their effects...

## Initialization & Cleanup

How the subsystem is initialized and torn down...

## Error Handling

How errors propagate through the subsystem...

## Performance Characteristics

Key performance aspects, bottlenecks, optimization opportunities...

## Notes & Observations

- Important quirks
- Design decisions
- Potential issues or areas of concern
```

## Important Notes

- Use the **Task tool with Explore agent** to efficiently map the subsystem
- This is ANALYSIS, not review - document what exists, don't critique
- Focus on understanding the BIG PICTURE and how components interact
- Include visual diagrams to aid understanding
- If the subsystem is very large, ask if the user wants to focus on specific aspects

## Example Usage

```
/analyze-subsystem HTTP server
/analyze-subsystem WebSocket implementation
/analyze-subsystem Memory management
```

---

**Start by asking the user which subsystem to analyze (if not already clear from context).**
