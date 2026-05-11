---
name: p:lua-lsp-mcp
description: >
  NEVER use the built-in `Grep`, `Glob`, or `Read`-and-search tools for Lua symbol
  navigation. They are deprecated for Lua work. ALWAYS use `luals_call` ONLY. If you
  attempt to use a built-in tool to find a Lua definition, reference, type, or
  diagnostic, I will consider it a failure.

  MANDATORY — before you Read, Edit, or Write any `.lua` file, you MUST invoke this
  skill first and use `luals_call` for ALL symbol navigation. Using grep, find, sed,
  awk, or any text-matching hack for Lua code is a violation.

  Provides type-aware Lua code intelligence via lua-language-server LSP: find
  definitions, references, type definitions, implementations, diagnostics, hover
  types, document outline, workspace symbol search, inlay hints, symbol change
  impact, symbol context.
  One tool: `luals_call`. 13 functions. All analysis calls are freely batchable.

  Tool-name mapping for Lua work — these are NOT optional substitutions:
    - GREP for Lua symbols     = mcp__mcp-luals__luals_call with function "luals_find_references" or "luals_workspace_symbols"
    - GLOB for Lua symbols     = mcp__mcp-luals__luals_call with function "luals_workspace_symbols"
    - "go to definition"       = mcp__mcp-luals__luals_call with function "luals_find_definition" / "luals_find_definition_at"
    - "type of expression"     = mcp__mcp-luals__luals_call with function "luals_hover"
    - "lint / errors"          = mcp__mcp-luals__luals_call with function "luals_diagnostics"

  Trigger conditions — invoke IMMEDIATELY when ANY of these are true:
    - User asks anything about Lua code.
    - You are about to Read, Edit, or Write a `.lua` file.
    - You need to find a function, table field, local, or upvalue in a Lua project.
    - You need to know the type of an expression, callers of a function, or the
      LSP's diagnostics on a Lua file.
    - User mentions luals, luals_call, lua-language-server, .luarc.json, or "Lua
      code intelligence".

triggers:
  - lua-language-server
  - Lua code
  - Lua code analysis
  - .lua file
  - luals
  - luals_call
  - find definition
  - find references
  - go to definition
  - Lua diagnostics
  - hover info
  - inlay hints
  - symbol search
  - document outline
  - type definition
  - workspace symbols
  - .luarc.json
---

# mcp-lua-lsp — Lua Code Intelligence

Full API reference for the mcp-lua-lsp MCP server — Lua code intelligence via lua-language-server.

## Overview

The server exposes one MCP tool: **`luals_call`** — a universal dispatcher for 13 Lua LSP functions.

```json
{"function":"<name>", "params":{...}}
```

Called without `function`, returns server status. All line/character numbers in **params** are **1-based** (human). All positions in results include both LSP 0-based (`range`) and human 1-based (`range_human`).

## Navigation

### `luals_find_definition`

Find where a symbol is defined, by name. Uses `workspace/symbol` + `textDocument/definition`.

```json
{"function":"luals_find_definition","params":{"symbol_name":"MyClass","context_lines":5}}
```

|Param|Required|Description|
|-|-|-|
|`symbol_name`|yes|Exact symbol name to look up|
|`context_lines`|no|Lines of context around definition (default: 5)|

Returns: list of `{symbol, location, context}`.

### `luals_find_definition_at`

Find definition at a specific file position.

```json
{"function":"luals_find_definition_at","params":{"path":"src/foo.lua","line":42,"character":15,"context_lines":5}}
```

|Param|Required|Description|
|-|-|-|
|`path`|yes|File path (relative to project root or absolute)|
|`line`|yes|Line number (1-based)|
|`character`|yes|Character offset (1-based)|
|`context_lines`|no|Lines of context (default:5)|

### `luals_find_type_definition_at`

Find the type definition at a position. Navigates to where a Lua class/type is declared (e.g. from a variable annotated with `---@type MyClass` to the `---@class MyClass` definition).

```json
{"function":"luals_find_type_definition_at","params":{"path":"src/foo.lua","line":10,"character":8,"context_lines":5}}
```

Same params as `luals_find_definition_at`.

### `luals_find_implementations_at`

Find implementations at a position (`textDocument/implementation`).

```json
{"function":"luals_find_implementations_at","params":{"path":"src/foo.lua","line":10,"character":8,"context_lines":5}}
```

Same params as `luals_find_definition_at`.

## References

### `luals_find_references`

Find all references to a named symbol across the workspace.

```json
{"function":"luals_find_references","params":{"symbol_name":"processData","max_results":50,"context_lines":3}}
```

|Param|Required|Description|
|-|-|-|
|`symbol_name`|yes|Exact symbol name|
|`max_results`|no|Maximum references to return (default: 50)|
|`context_lines`|no|Context lines per reference (default: 3, set 0 to disable)|

## Symbol Search

### `luals_workspace_symbols`

Search for symbols across the entire workspace by query string.

```json
{"function":"luals_workspace_symbols","params":{"query":"Player","limit":50}}
```

|Param|Required|Description|
|-|-|-|
|`query`|yes|Query string (substring match)|
|`limit`|no|Max results (default: 50)|

### `luals_document_outline`

Get the full symbol outline of a Lua file (hierarchical).

```json
{"function":"luals_document_outline","params":{"path":"src/player.lua"}}
```

## Compound Queries

### `luals_symbol_context`

Get definition **and** references for a symbol in a single call. Use before reading unfamiliar code.

```json
{"function":"luals_symbol_context","params":{"symbol_name":"EventBus","max_references":20,"context_lines":5}}
```

|Param|Required|Description|
|-|-|-|
|`symbol_name`|yes|Exact symbol name|
|`max_references`|no|Max references to include (default: 20)|
|`context_lines`|no|Context lines for definition (default: 5)|

### `luals_symbol_change_impact`

Definition + all references for impact analysis before renaming or changing a symbol.

> Note: lua-language-server does not support call hierarchy. Use `luals_find_references` with `context_lines` to understand callers manually.

```json
{"function":"luals_symbol_change_impact","params":{"symbol_name":"serialize","max_references":50}}
```

|Param|Required|Description|
|-|-|-|
|`symbol_name`|yes|Symbol to analyse|
|`max_references`|no|Max references (default: 50)|

## Hover & Type Info

### `luals_hover`

Get hover information (type, documentation) at a position.

```json
{"function":"luals_hover","params":{"path":"src/foo.lua","line":15,"character":10}}
```

## Inlay Hints

### `luals_inlay_hints`

Get inlay hints (parameter names, type annotations) for a file range. Hints are enabled automatically on init.

```json
{"function":"luals_inlay_hints","params":{"path":"src/foo.lua","start_line":1,"end_line":100,"limit":100}}
```

|Param|Required|Description|
|-|-|-|
|`path`|yes|File path|
|`start_line`|no|Start line (1-based, default: 1)|
|`end_line`|no|End line (1-based, default: 9999)|
|`limit`|no|Max hints (default: 100)|

lua-language-server hint types: Parameter names, local variable types, function return types, array indices, await markers.

## Diagnostics

### `luals_diagnostics`

Get diagnostics (errors, warnings, hints) for a Lua file.

```json
{"function":"luals_diagnostics","params":{"path":"src/foo.lua","timeout":10.0}}
```

|Param|Required|Description|
|-|-|-|
|`path`|yes|File path|
|`timeout`|no|Seconds to wait for diagnostics push (default: 10.0)|

Severity values: `"Error"`, `"Warning"`, `"Information"`, `"Hint"`.

## Location Object

All navigation results include a `location` object:

```json
{"path":"src/player.lua","uri":"file:///abs/path/src/player.lua","range":{"start":{"line":9,"character":0},"end":{"line":9,"character":6}},"range_human":{"start":{"line":10,"character":1},"end":{"line":10,"character":7}},"line_text":"function Player:new(name)"}
```

- `range` — LSP 0-based coordinates
- `range_human` — 1-based coordinates for display
- `line_text` — the raw source line at that location

## Workflow Recommendations

**Before reading unfamiliar code:**
```
luals_symbol_context {symbol_name:"ClassName"}
```

**Understand a file:**
```
luals_document_outline {path:"src/module.lua"}
```

**Before renaming a function:**
```
luals_symbol_change_impact {symbol_name:"myFunc"}
```

**Check for errors:**
```
luals_diagnostics {path:"src/foo.lua"}
```

**Understand what a variable holds:**
```
luals_hover {path:"src/foo.lua",line:42,character:10}
```

**Navigate to type definition from a variable:**
```
luals_find_type_definition_at {path:"src/foo.lua",line:42,character:10}
```
