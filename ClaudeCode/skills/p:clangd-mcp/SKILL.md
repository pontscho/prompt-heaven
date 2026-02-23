---
name: p:clangd-mcp
description: >
  C/C++ code intelligence via clangd LSP. One tool in tools/list: clangd_call.
  13 functions: init, find_definition, find_definition_at, find_references,
  find_implementations_at, workspace_symbols, document_outline, symbol_context,
  inlay_hints, symbol_change_impact, hover, diagnostics, deduced_type_at.
triggers:
  - clangd
  - C code analysis
  - C++ code analysis
  - find definition
  - find references
  - go to definition
  - code intelligence
  - clangd_call
  - clangd_init
  - compiler diagnostics
  - hover info
  - inlay hints
  - call hierarchy
  - symbol search
---

# SKILL: clangd-mcp — C/C++ Code Intelligence

The MCP server (`mcp-clangd.py`) exposes **one tool in `tools/list`**:

- `clangd_call` — universal dispatcher for all 13 clangd functions; called without `function` returns server status

All clangd operations go through `clangd_call(function=..., params={...})`.

## How to call any function

```
mcp__mcp-clangd__clangd_call(
  function = "<function_name>",
  params   = { ...parameters... }
)
```

**Example — initialize:**
```
mcp__mcp-clangd__clangd_call(function="clangd_init", params={
  "project_root": "/path/to/project"
})
```

**Example — find definition:**
```
mcp__mcp-clangd__clangd_call(function="clangd_find_definition", params={
  "symbol_name": "my_function"
})
```

---

## Workflow

```
clangd_init { project_root }     ← ALWAYS first
  → [any analysis tools]         ← freely batchable after init
```

`clangd_init` must complete before any other function is called.

---

## Tool Reference (13 functions)

### clangd_call (status check)
Returns server status and active project when called without `function`.
```json
{}
```

---

### clangd_init
Initialize clangd for a project. Launches clangd, performs LSP handshake, waits for background indexing.
```json
{
  "project_root": "/path/to/project",   // required
  "clangd_path": "clangd",              // optional, default "clangd"
  "compile_commands_dir": "/path/build" // optional; dir containing compile_commands.json
}
```
Returns: `{ "status": "ok", "message": "...", "project_root": "..." }`

If already initialized: `{ "status": "already initialized", "project_root": "..." }`

---

### clangd_find_definition
Find the definition of a symbol by name. Uses workspace/symbol to locate the symbol, then textDocument/definition.
```json
{
  "symbol_name": "my_function",   // required — exact symbol name
  "context_lines": 5              // optional, default 5 — lines of surrounding code
}
```
Returns: array of `{ symbol, location, context }` objects.

---

### clangd_find_definition_at
Find definition at a specific file position (1-based line/character).
```json
{
  "path": "src/main.c",     // required — relative or absolute path
  "line": 42,               // required — 1-based line number
  "character": 10,          // required — 1-based character offset
  "context_lines": 5        // optional, default 5
}
```
Returns: array of `{ location, context }` objects.

---

### clangd_find_references
Find all references to a symbol by name.
```json
{
  "symbol_name": "my_function",  // required
  "max_results": 50,             // optional, default 50
  "context_lines": 3             // optional, default 3 — 0 = no context
}
```
Returns: `{ symbol, count, references: [{ symbol, location, context }] }`

---

### clangd_find_implementations_at
Find implementations of an interface/virtual method at a specific position.
```json
{
  "path": "include/interface.h",  // required
  "line": 15,                     // required — 1-based
  "character": 5,                 // required — 1-based
  "context_lines": 5              // optional, default 5
}
```
Returns: array of `{ location, context }` objects.

---

### clangd_workspace_symbols
Search for symbols across the workspace by query string (fuzzy match).
```json
{
  "query": "my_func",   // required
  "limit": 50           // optional, default 50
}
```
Returns: `{ query, count, symbols: [{ symbol, kind, container, location }] }`

---

### clangd_document_outline
Get the structural outline (all symbols) of a file.
```json
{
  "path": "src/main.c"   // required
}
```
Returns: array of symbol nodes. Each node has:
- `symbol`, `kind`, `detail` (optional)
- `selection` / `extent` (DocumentSymbol) or `location` (SymbolInformation)
- `children` (nested symbols if hierarchical)

---

### clangd_symbol_context
Get definition + references for a symbol in a single call. Preferred over separate find_definition + find_references.
```json
{
  "symbol_name": "my_function",  // required
  "max_references": 20,          // optional, default 20
  "context_lines": 5             // optional, default 5
}
```
Returns: `{ symbol, definition, references }`

---

### clangd_inlay_hints
Get inlay hints (parameter names, deduced types) for a file range.
```json
{
  "path": "src/main.cpp",    // required
  "start_line": 1,           // optional, default 1 (1-based)
  "end_line": 9999,          // optional, default 9999 (1-based)
  "limit": 100               // optional, default 100
}
```
Returns: array of `{ label, kind, position: { lsp, human }, tooltip }`

`kind` is `"Parameter"` or `"Type"`.

---

### clangd_symbol_change_impact
Comprehensive impact analysis before changing a symbol: definition + references + call hierarchy.
```json
{
  "symbol_name": "my_function",      // required
  "max_references": 50,              // optional, default 50
  "call_hierarchy_depth": 1          // optional, default 1
}
```
Returns: `{ symbol, definition, references, reference_summary: { count, files }, call_hierarchy }`

---

### clangd_hover
Get hover information (type, documentation) at a position.
```json
{
  "path": "src/main.c",   // required
  "line": 10,             // required — 1-based
  "character": 5          // required — 1-based
}
```
Returns: `{ text, location }`

`text` contains the full hover markdown (type signature, documentation).

---

### clangd_diagnostics
Get compiler diagnostics (errors, warnings) for a file. Opens the file and waits for clangd's publishDiagnostics push.
```json
{
  "path": "src/main.c",   // required
  "timeout": 10.0         // optional, default 10.0 seconds
}
```
Returns: `{ path, count, diagnostics: [{ message, severity, code, source, location }] }`

`severity`: `"Error"`, `"Warning"`, `"Information"`, `"Hint"`

---

### clangd_deduced_type_at
Get the deduced type at a position (useful for `auto`, `decltype` variables).
```json
{
  "path": "src/main.cpp",   // required
  "line": 20,               // required — 1-based
  "character": 8            // required — 1-based
}
```
Returns: `{ type, raw, location }`

`type` is the inferred clean type string; `raw` is the full hover text.

---

## Location object format

All location objects returned by this server follow this structure:
```json
{
  "path": "src/main.c",         // path relative to project_root
  "uri": "file:///abs/path/...",
  "range": {                    // 0-based LSP coordinates
    "start": { "line": 9, "character": 4 },
    "end":   { "line": 9, "character": 15 }
  },
  "range_human": {              // 1-based human-readable coordinates
    "start": { "line": 10, "character": 5 },
    "end":   { "line": 10, "character": 16 }
  },
  "line_text": "    my_function(arg1, arg2);"
}
```

---

## Parallel call strategy — reduce model turn latency

**Send multiple independent `clangd_call`s in a single response** (multi-tool message).
The server serializes execution, but only ONE model API round-trip is needed.

### Safe to batch (read-only — after init)

| Function | Notes |
|---|---|
| `clangd_find_definition` | multiple symbols at once |
| `clangd_find_definition_at` | multiple positions at once |
| `clangd_find_references` | multiple symbols at once |
| `clangd_find_implementations_at` | |
| `clangd_workspace_symbols` | |
| `clangd_document_outline` | multiple files at once |
| `clangd_hover` | multiple positions at once |
| `clangd_inlay_hints` | |
| `clangd_diagnostics` | multiple files at once |
| `clangd_deduced_type_at` | multiple positions at once |

### Must be sequential

```
clangd_init                    ← always first
  → [any analysis tools]       ← freely batchable
```

- `clangd_symbol_context` already batches definition + references internally — prefer it over separate calls.
- `clangd_symbol_change_impact` already batches definition + references + call hierarchy — prefer it for impact analysis.

### Rule of thumb

- **Unknown symbol**: `symbol_context` (one call, full picture)
- **Before refactoring**: `symbol_change_impact` (one call, impact analysis)
- **Multiple files diagnostics**: batch all at once
- **Multiple symbol definitions**: batch all at once

---

## Common workflows

### Understand an unknown symbol (2 turns)
```
Turn 1: clangd_init { project_root }
Turn 2: [BATCH] clangd_symbol_context { symbol_name: "my_func" }
              + clangd_document_outline { path: "src/main.c" }
              + clangd_diagnostics { path: "src/main.c" }
```

### Refactoring impact check (2 turns)
```
Turn 1: clangd_init { project_root }
Turn 2: clangd_symbol_change_impact { symbol_name: "my_func", max_references: 50 }
```

### Multiple symbol definitions (2 turns)
```
Turn 1: clangd_init { project_root }
Turn 2: [BATCH] clangd_find_definition { symbol_name: "func_a" }
              + clangd_find_definition { symbol_name: "func_b" }
              + clangd_find_definition { symbol_name: "MyStruct" }
```

### Check diagnostics across multiple files (2 turns)
```
Turn 1: clangd_init { project_root }
Turn 2: [BATCH] clangd_diagnostics { path: "src/main.c" }
              + clangd_diagnostics { path: "src/parser.c" }
              + clangd_diagnostics { path: "src/lexer.c" }
```

### Hover + inlay hints for a function (2 turns)
```
Turn 1: clangd_init { project_root }
Turn 2: [BATCH] clangd_hover { path: "src/main.c", line: 42, character: 5 }
              + clangd_inlay_hints { path: "src/main.c", start_line: 30, end_line: 60 }
```

---

## Notes

- **Lines and characters** in `params` are **1-based** (human-readable). The server converts to 0-based LSP internally.
- **`compile_commands_dir`**: pass this if `compile_commands.json` is in a build subdirectory (e.g. `build/`).
- **Indexing**: `clangd_init` waits up to 60 seconds for background indexing. Subsequent calls will have accurate symbol data.
- **Path resolution**: relative paths are resolved against `project_root`.
