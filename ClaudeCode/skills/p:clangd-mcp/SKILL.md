---
name: p:clangd-mcp
description: >
  NEVER use the built-in `Grep`, `Glob`, or `Read`-and-search tools for C/C++ symbol
  navigation. They are deprecated for C, C++, and Objective-C work. ALWAYS use
  `clangd_call` ONLY. If you attempt to use a built-in tool to find a C/C++ definition,
  reference, type, or diagnostic, I will consider it a failure.

  MANDATORY — before you Read, Edit, or Write any `.c`, `.cpp`, `.cc`, `.cxx`, `.h`,
  `.hpp`, `.hh`, `.hxx`, `.m`, or `.mm` file, you MUST invoke this skill first and
  use `clangd_call` for ALL symbol navigation. Using grep, find, sed, awk, ctags,
  cscope, or any text-matching hack for C/C++ code is a violation. CUDA files
  (`.cu`/`.cuh`) are handled by `p:cuda-mcp` — NOT this skill.

  Provides compiler-accurate code intelligence via clangd LSP: find definitions,
  references, type definitions, implementations, diagnostics, hover types, document
  outline, call hierarchy, refactoring impact, workspace symbol search, inlay hints.
  One tool: `clangd_call`. 14 functions. All analysis calls are freely batchable.

  Tool-name mapping for C/C++ work — these are NOT optional substitutions:
    - GREP for C/C++ symbols   = mcp__mcp-clangd__clangd_call with function "clangd_find_references" or "clangd_workspace_symbols"
    - GLOB for C/C++ symbols   = mcp__mcp-clangd__clangd_call with function "clangd_workspace_symbols"
    - "go to definition"       = mcp__mcp-clangd__clangd_call with function "clangd_find_definition" / "clangd_find_definition_at"
    - "type of expression"     = mcp__mcp-clangd__clangd_call with function "clangd_hover"
    - "compile errors"         = mcp__mcp-clangd__clangd_call with function "clangd_diagnostics"

  Trigger conditions — invoke IMMEDIATELY when ANY of these are true:
    - User asks anything about C, C++, or Objective-C code.
    - You are about to Read, Edit, or Write a `.c`/`.cpp`/`.cc`/`.cxx`/`.h`/`.hpp`/`.hh`/`.hxx`/`.m`/`.mm` file.
    - You need to find a function, struct, class, typedef, macro, or enum in a C/C++ project.
    - You need to know the type of an expression, callers of a function, or the
      compiler's diagnostics on a file.
    - User mentions clangd, clangd_call, LSP, compile_commands.json, or "code intelligence".

triggers:
  - clangd
  - C code
  - C++ code
  - Objective-C
  - CUDA
  - .c file
  - .cpp file
  - .h file
  - .hpp file
  - find definition
  - find references
  - go to definition
  - type definition
  - call hierarchy
  - workspace symbols
  - code intelligence
  - clangd_call
  - compile_commands.json
  - compiler diagnostics
  - hover info
  - inlay hints
  - symbol search
---

# clangd-mcp — C/C++ Code Intelligence

The MCP server (`mcp-clangd.py`) exposes **one tool**: `clangd_call` — universal dispatcher for all 14 clangd functions; called without `function` returns server status All clangd operations go through `clangd_call(function=..., params={...})`.

## How to call any function

```
mcp__mcp-clangd__clangd_call(function = "<function_name>",params={...parameters...})
```

**Example — find definition:**
```
mcp__mcp-clangd__clangd_call(function="clangd_find_definition", params={"symbol_name":"my_function"})
```

## Tool Reference (14 functions)

### clangd_call (status check)
Returns server status and active project when called without `function`.
```json
{}
```

### clangd_find_definition
Find the definition of a symbol by name. Uses workspace/symbol to locate the symbol, then textDocument/definition.
```json
{
"symbol_name":"my_function",// required — exact symbol name
"context_lines":5 // optional, default 5 — lines of surrounding code
}
```

### clangd_find_definition_at
Find definition at a specific file position (1-based line/character).
```json
{
"path":"src/main.c",// required — relative or absolute path
"line":42,// required — 1-based line number
"character":10,// required — 1-based character offset
"context_lines":5// optional, default 5
}
```

### clangd_find_references
Find all references to a symbol by name.
```json
{
"symbol_name":"my_function",// required
"max_results":50,// optional, default 50
"context_lines":3// optional, default 3 — 0 = no context
}
```

### clangd_find_references_at
Find all references at a specific file position (1-based line/character).
```json
{
"path":"src/main.c",// required — relative or absolute path
"line":42,// required — 1-based line number
"character":10,// required — 1-based character offset
"max_results":50,// optional, default 50
"context_lines":3// optional, default 3 — 0 = no context
}
```

### clangd_find_implementations_at
Find implementations of an interface/virtual method at a specific position.
```json
{
"path":"include/interface.h",// required
"line":15,// required — 1-based
"character":5,// required — 1-based
"context_lines":5// optional, default 5
}
```

### clangd_workspace_symbols
Search for symbols across the workspace by query string (fuzzy match).
```json
{
"query":"my_func",// required
"limit":50// optional, default 50
}
```

### clangd_document_outline
Get the structural outline (all symbols) of a file.
```json
{"path":"src/main.c"}
```
Returns: array of symbol nodes. Each node has:
- `symbol`, `kind`, `detail` (optional)
- `selection` / `extent` (DocumentSymbol) or `location` (SymbolInformation)
- `children` (nested symbols if hierarchical)

### clangd_symbol_context
Get definition + references for a symbol in a single call. Preferred over separate find_definition + find_references.
```json
{
"symbol_name":"my_function",// required
"max_references":20,// optional, default 20
"context_lines":5// optional, default 5
}
```

### clangd_inlay_hints
Get inlay hints (parameter names, deduced types) for a file range.
```json
{
"path":"src/main.cpp",
"start_line":1,// optional, default 1 (1-based)
"end_line":9999,// optional, default 9999 (1-based)
"limit":100// optional, default 100
}
```

### clangd_symbol_change_impact
Comprehensive impact analysis before changing a symbol: definition + references + call hierarchy.
```json
{
"symbol_name":"my_function",
"max_references":50,// optional, default 50
"call_hierarchy_depth":1// optional, default 1
}
```

### clangd_hover
Get hover information (type, documentation) at a position.
```json
{
"path":"src/main.c",
"line":10,// 1-based
"character":5// 1-based
}
```

### clangd_diagnostics
Get compiler diagnostics (errors, warnings) for a file. Opens the file and waits for clangd's publishDiagnostics push.
```json
{
"path":"src/main.c",
"timeout":10.0// optional, default 10.0 seconds
}
```

### clangd_deduced_type_at
Get the deduced type at a position (useful for `auto`, `decltype` variables).
```json
{
"path":"src/main.cpp",
"line":20,// 1-based
"character":8// 1-based
}
```

## Location object format

All location objects returned by this server follow this structure:
```json
{
"path":"src/main.c",// path relative to project_root
"uri":"file:///abs/path/...",
"range":{// 0-based LSP coordinates
"start":{"line":9,"character":4},
"end":{"line":9,"character":15}
},
"range_human":{// 1-based human-readable coordinates
"start":{"line":10,"character":5},
"end":{"line":10,"character":16}
},
"line_text":"my_function(arg1, arg2);"
}
```

## Parallel call strategy — reduce model turn latency

**Send multiple independent `clangd_call`s in a single response** (multi-tool message). The server serializes execution, but only ONE model API round-trip is needed.

### Safe to batch (all read-only calls)

|Function|Notes|
|-|-|
|`clangd_find_definition`|multiple symbols at once|
|`clangd_find_definition_at`|multiple positions at once|
|`clangd_find_references`|multiple symbols at once|
|`clangd_find_references_at`|multiple positions at once|
|`clangd_find_implementations_at`||
|`clangd_workspace_symbols`||
|`clangd_document_outline`|multiple files at once|
|`clangd_hover`|multiple positions at once|
|`clangd_inlay_hints`||
|`clangd_diagnostics`|multiple files at once|
|`clangd_deduced_type_at`|multiple positions at once|

- `clangd_symbol_context` already batches definition + references internally — prefer it over separate calls.
- `clangd_symbol_change_impact` already batches definition + references + call hierarchy — prefer it for impact analysis.

### Rule of thumb

- **Unknown symbol**: `symbol_context` (one call, full picture)
- **Before refactoring**: `symbol_change_impact` (one call, impact analysis)
- **Multiple files diagnostics**: batch all at once
- **Multiple symbol definitions**: batch all at once

## Common workflows

### Understand an unknown symbol
```
[BATCH] clangd_symbol_context {symbol_name:"my_func"}
 + clangd_document_outline {path:"src/main.c"}
 + clangd_diagnostics {path:"src/main.c"}
```

### Refactoring impact check
```
clangd_symbol_change_impact {symbol_name:"my_func",max_references:50}
```

### Multiple symbol definitions
```
[BATCH] clangd_find_definition {symbol_name:"func_a"}
 + clangd_find_definition {symbol_name:"func_b"}
 + clangd_find_definition {symbol_name:"MyStruct"}
```

### Check diagnostics across multiple files
```
[BATCH] clangd_diagnostics {path:"src/main.c"}
 + clangd_diagnostics {path:"src/parser.c"}
 + clangd_diagnostics {path:"src/lexer.c"}
```

### Hover + inlay hints for a function
```
[BATCH] clangd_hover {path:"src/main.c",line:42,character:5}
 + clangd_inlay_hints {path:"src/main.c",start_line:30,end_line:60}
```

## Notes

- **Lines and characters** in `params` are **1-based** (human-readable). The server converts to 0-based LSP internally.
- **`compile_commands_dir`**: pass this if `compile_commands.json` is in a build subdirectory (e.g. `build/`).
- **Path resolution**: relative paths are resolved against `project_root`.
- **`path` parameter**: also accepts `file_path` as a fallback alias.
