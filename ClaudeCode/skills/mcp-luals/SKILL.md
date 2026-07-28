---
name: mcp-luals
description: >
  NEVER use the built-in `Grep`, `Glob`, or `Read`-and-search tools for Lua symbol
  navigation. They are deprecated for Lua work. ALWAYS use `purity_call`'s semantic
  functions (type-aware, lua-language-server-backed) ONLY. If you attempt to use a
  built-in tool to find a Lua definition, reference, type, or diagnostic, I will
  consider it a failure.

  MANDATORY — before you Read, Edit, or Write any `.lua` file, you MUST invoke this
  skill first and use `purity_call` for ALL symbol navigation. Using grep, find, sed,
  awk, or any text-matching hack for Lua code is a violation.

  Provides type-aware Lua code intelligence via lua-language-server LSP: find
  definitions, references, implementations, diagnostics, hover types, document
  outline, workspace symbol search, inlay hints, symbol change impact, symbol
  context.
  One tool: `purity_call`. The standalone `mcp-luals` server is NO LONGER REGISTERED,
  so the `luals_call` tool does not exist; its functionality was folded into
  `purity_call`, which accepts all 14 `luals_*` function names plus the canonical
  short names. All analysis calls are freely batchable.

  Tool-name mapping for Lua work — these are NOT optional substitutions:
    - GREP for Lua symbols     = mcp__mcp-purity__purity_call with function "luals_find_references" or "luals_workspace_symbols"
    - GLOB for Lua symbols     = mcp__mcp-purity__purity_call with function "luals_workspace_symbols"
    - "go to definition"       = mcp__mcp-purity__purity_call with function "luals_find_definition" / "luals_find_definition_at"
    - "type of expression"     = mcp__mcp-purity__purity_call with function "luals_hover"
    - "lint / errors"          = mcp__mcp-purity__purity_call with function "luals_diagnostics"

  Trigger conditions — invoke IMMEDIATELY when ANY of these are true:
    - User asks anything about Lua code.
    - You are about to Read, Edit, or Write a `.lua` file.
    - You need to find a function, table field, local, or upvalue in a Lua project.
    - You need to know the type of an expression, callers of a function, or the
      LSP's diagnostics on a Lua file.
    - User mentions luals, lua-language-server, .luarc.json, or "Lua code
      intelligence".

triggers:
  - lua-language-server
  - Lua code
  - Lua code analysis
  - .lua file
  - luals
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

# mcp-luals — Lua Code Intelligence (now via `purity_call`)

Full API reference for Lua code intelligence via lua-language-server.

## Overview

The standalone `mcp-luals` server is **no longer registered**, so there is no
`luals_call` tool. Lua code intelligence is served by **`purity_call`**, the unified
entry point that embeds a lua-language-server backend and routes `.lua` paths to it.
`purity_call` is the only entry point.

```
mcp__mcp-purity__purity_call(function="<name>", params={...})
```

Called without `function`, `purity_call` returns server status and the full function
inventory. All line/character numbers in **params** are **1-based** (human), and
results report positions as `path:line:character`, also 1-based.

`purity_call` accepts **both** naming schemes for every function below:

- the `luals_*` legacy names used throughout this page, and
- the canonical short names shared with the C/C++/CUDA backends
  (`find_definition`, `find_type_definition`, `find_references`,
  `find_implementations`, `type_at`, `outline`, `symbol`, `symbol_context`,
  `symbol_change_impact`, `inlay_hints`, `diagnostics`).

The backend is chosen from the file extension, so the canonical short names give you
Lua results for `.lua` paths. Prefer the short names in mixed-language work; the
`luals_*` names are kept so existing prompts and habits keep working.

### Param names

The canonical path key is **`relative_path`** (aliases `path`, `file`, `file_path`,
`filepath` all resolve to it). The canonical symbol key is **`symbol_name`** (aliases
`symbol`, `name`). Other aliases: `col`/`column`/`char` → `character`,
`max`/`count` → `max_results`, `max_refs` → `max_references`,
`context`/`ctx_lines` → `context_lines`, `depth` → `call_hierarchy_depth`.

### Backend lifecycle

The lua-language-server backend initialises **lazily** on the first semantic call, so
the first call can take a while as it indexes. `luals_init` is accepted but is a
**no-op** — you never need to call it. To force a restart/reindex, use
`restart_lsp`.

## Navigation

### `luals_find_definition`

Find where a symbol is defined, by name.

```json
{"function":"luals_find_definition","params":{"symbol_name":"MyClass","context_lines":5}}
```

|Param|Required|Description|
|-|-|-|
|`symbol_name`|yes|Exact symbol name to look up|
|`context_lines`|no|Lines of context around definition (default: 5)|

Returns the definition location plus a source excerpt with the definition line
marked.

### `luals_find_definition_at`

Find definition at a specific file position.

```json
{"function":"luals_find_definition_at","params":{"relative_path":"src/foo.lua","line":42,"character":15,"context_lines":5}}
```

|Param|Required|Description|
|-|-|-|
|`relative_path`|yes|File path (relative to project root or absolute)|
|`line`|yes|Line number (1-based)|
|`character`|yes|Character offset (1-based)|
|`context_lines`|no|Lines of context (default: 5)|

`luals_find_definition` and `luals_find_definition_at` share one handler: pass
`symbol_name` for name-based lookup, or `relative_path`+`line`+`character` for
position-based. Either name accepts either param shape.

### `luals_find_type_definition_at`

Find where the **type** of the value at a position is declared
(`textDocument/typeDefinition`) — one hop past `luals_find_definition`. **Position
only**: there is no by-name spelling.

```json
{"function":"luals_find_type_definition_at","params":{"relative_path":"src/foo.lua","line":42,"character":10,"context_lines":5}}
```

|Param|Required|Description|
|-|-|-|
|`relative_path`|yes|File path|
|`line`|yes|Line number (1-based)|
|`character`|yes|Character offset (1-based)|
|`context_lines`|no|Lines of context (default: 5)|

lua-language-server lands on the annotated `function ...` declaration line at
character 1 — not the identifier column `luals_find_definition_at` reports. Returns an
error rather than a guess when the value carries no resolvable type.

### `luals_find_implementations_at`

Find implementations at a position (`textDocument/implementation`).

```json
{"function":"luals_find_implementations_at","params":{"relative_path":"src/foo.lua","line":10,"character":8,"context_lines":5}}
```

|Param|Required|Description|
|-|-|-|
|`relative_path`|yes|File path|
|`line`|yes|Line number (1-based)|
|`character`|yes|Character offset (1-based)|
|`context_lines`|no|Lines of context (default: 5)|

## References

### `luals_find_references`

Find all references to a symbol across the workspace. Name-based queries are
supplemented with a Lua text scan to cover dynamic-dispatch patterns the LSP can
miss.

```json
{"function":"luals_find_references","params":{"symbol_name":"processData","max_results":50,"context_lines":3}}
```

|Param|Required|Description|
|-|-|-|
|`symbol_name`|yes (or position)|Exact symbol name|
|`relative_path`, `line`, `character`|alternative|Position-based lookup (also `luals_find_references_at`)|
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
|`strict`|no|Exact-name matching instead of fuzzy|

### `luals_document_outline`

Get the full symbol outline of a Lua file (hierarchical).

```json
{"function":"luals_document_outline","params":{"relative_path":"src/player.lua"}}
```

|Param|Required|Description|
|-|-|-|
|`relative_path`|yes|File path|

## Compound Queries

### `luals_symbol_context`

Get definition **and** references for a symbol in a single call. Use before reading
unfamiliar code.

```json
{"function":"luals_symbol_context","params":{"symbol_name":"EventBus","max_references":20,"context_lines":5}}
```

|Param|Required|Description|
|-|-|-|
|`symbol_name`|yes|Exact symbol name|
|`relative_path`|no|Restrict the lookup to one file|
|`max_references`|no|Max references to include (default: 20)|
|`context_lines`|no|Context lines for definition (default: 5)|

### `luals_symbol_change_impact`

Definition + all references for impact analysis before renaming or changing a symbol.

> Note: lua-language-server does not support call hierarchy, so for Lua this returns
> definition + references only (no call tree). `call_hierarchy_depth` is accepted but
> has no effect on Lua files. Use `luals_find_references` with `context_lines` to
> understand callers manually.

```json
{"function":"luals_symbol_change_impact","params":{"symbol_name":"serialize","max_references":50}}
```

|Param|Required|Description|
|-|-|-|
|`symbol_name`|yes|Symbol to analyse|
|`relative_path`|no|Restrict the lookup to one file|
|`max_references`|no|Max references (default: 50)|

## Hover & Type Info

### `luals_hover`

Get hover information (deduced type, documentation) at a position.

```json
{"function":"luals_hover","params":{"relative_path":"src/foo.lua","line":15,"character":10}}
```

|Param|Required|Description|
|-|-|-|
|`relative_path`|yes|File path|
|`line`|yes|Line number (1-based)|
|`character`|yes|Character offset (1-based)|

Returns the deduced type signature plus any LuaDoc annotations attached to the
symbol. Canonical short name: `type_at`.

## Inlay Hints

### `luals_inlay_hints`

Get inlay hints (parameter names, type annotations) for a file range. Hints are
enabled automatically when the backend starts.

```json
{"function":"luals_inlay_hints","params":{"relative_path":"src/foo.lua","start_line":1,"end_line":100,"limit":100}}
```

|Param|Required|Description|
|-|-|-|
|`relative_path`|yes|File path|
|`start_line`|no|Start line (1-based, default: 1)|
|`end_line`|no|End line (1-based, default: 9999)|
|`limit`|no|Max hints (default: 100)|

lua-language-server hint types: parameter names, local variable types, function
return types, array indices, await markers.

## Diagnostics

### `luals_diagnostics`

Get diagnostics (errors, warnings, hints) for a Lua file.

```json
{"function":"luals_diagnostics","params":{"relative_path":"src/foo.lua","timeout":10.0}}
```

|Param|Required|Description|
|-|-|-|
|`relative_path`|yes|File path|
|`timeout`|no|Seconds to wait for diagnostics push (default: 10.0)|

Severity values: `"Error"`, `"Warning"`, `"Information"`, `"Hint"`.

## Legacy `luals_*` → canonical name mapping

Both columns are live keys on `purity_call`; the right column is preferred.

| `luals_*` name | Canonical `purity_call` name |
|-|-|
| `luals_find_definition`, `luals_find_definition_at` | `find_definition` |
| `luals_find_type_definition_at` | `find_type_definition` |
| `luals_find_references`, `luals_find_references_at` | `find_references` |
| `luals_find_implementations_at` | `find_implementations` |
| `luals_hover` | `type_at` |
| `luals_document_outline` | `outline` |
| `luals_workspace_symbols` | `symbol` |
| `luals_symbol_context` | `symbol_context` |
| `luals_symbol_change_impact` | `symbol_change_impact` |
| `luals_inlay_hints` | `inlay_hints` |
| `luals_diagnostics` | `diagnostics` |
| `luals_init` | (no-op — backend inits lazily; use `restart_lsp` to reindex) |

The `_at` variants fold onto their non-`_at` counterpart; position vs name is detected
from the params.

## Result Format

Results are returned as rendered Markdown, not raw JSON. Locations appear as
`path:line:character` (1-based), followed by a source excerpt with the target line
marked, e.g.:

```
# Definition: `square` (1)

## `src/mathlib.lua:9:12`
    -- @return number
>>> function M.square(x)
        return x * x
```

## Workflow Recommendations

**Before reading unfamiliar code:**
```
purity_call(function="luals_symbol_context", params={"symbol_name":"ClassName"})
```

**Understand a file:**
```
purity_call(function="luals_document_outline", params={"relative_path":"src/module.lua"})
```

**Before renaming a function:**
```
purity_call(function="luals_symbol_change_impact", params={"symbol_name":"myFunc"})
```

**Check for errors:**
```
purity_call(function="luals_diagnostics", params={"relative_path":"src/foo.lua"})
```

**Understand what a variable holds:**
```
purity_call(function="luals_hover", params={"relative_path":"src/foo.lua","line":42,"character":10})
```

**Navigate to a type declaration from a variable:**
```
purity_call(function="luals_find_type_definition_at", params={"relative_path":"src/foo.lua","line":42,"character":10})
```

*Fallback only* — if that errors because the value has no resolvable type, read the
inferred type name off hover and look it up by name:
```
purity_call(function="luals_hover", params={"relative_path":"src/foo.lua","line":42,"character":10})
purity_call(function="luals_find_definition", params={"symbol_name":"<type name from hover>"})
```
