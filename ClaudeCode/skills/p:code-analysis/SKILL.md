---
name: p:code-analysis
description: Guide for using code analysis commands (/analyze-module, /analyze-subsystem, /analyze-api) to understand and document codebases. Use when analyzing code, understanding how components work, learning unfamiliar code, documenting system architecture, or creating API references. Activates when you need to understand, explain, or document existing code.
---

# Code Analysis Commands

Use specialized slash commands to analyze and document code.

## Available Commands

### `/analyze-module <file>`
Analyzes a single file/module in depth.

**Use for:**
- Understanding what a specific file does
- Documenting algorithms and data structures
- Mapping dependencies within a module

**Example:**
```bash
/analyze-module src/server/request-handler.c
/analyze-module lib/utils/parser.py
```

**Output:** `docs/analysis/analysis-{module-name}.md`

---

### `/analyze-subsystem <component>`
Analyzes how multiple modules work together.

**Use for:**
- Understanding system architecture
- Mapping data/control flow
- Documenting component interactions

**Example:**
```bash
/analyze-subsystem HTTP server
/analyze-subsystem WebSocket implementation
/analyze-subsystem Memory management
```

**Output:** `docs/analysis/subsystem-{name}.md`

---

### `/analyze-api <api-name>`
Creates user-facing API documentation.

**Use for:**
- Documenting public APIs
- Creating reference documentation
- Explaining how to use interfaces

**Example:**
```bash
/analyze-api HTTP Server API
/analyze-api memory functions
/analyze-api src/include/api.h
```

**Output:** `docs/api/api-{name}.md`

---

## Quick Reference

| What to Analyze | Command | Output |
|----------------|---------|--------|
| Single file | `/analyze-module path/to/file.c` | Module analysis |
| Multi-module component | `/analyze-subsystem "Component Name"` | Architecture doc |
| Public API/interface | `/analyze-api "API Name"` | API reference |

## When to Use

- **Before refactoring**: Understand existing structure
- **Onboarding**: Learn unfamiliar code
- **Documentation**: Create living documentation
- **Debugging**: Understand complex interactions

## Tips

- Commands create files automatically in `docs/analysis/` or `docs/api/`
- Include specific line numbers in analysis for traceability
- Use ASCII diagrams for complex flows
- Analysis is NOT code review - document, don't critique

## Smart Alternative

For automatic type detection, use `/analyze` which intelligently chooses the right analysis type based on your input.
