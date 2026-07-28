---
name: mcp-clangd
description: >
  NEVER use the built-in `Grep`, `Glob`, or `Read`-and-search tools for C/C++ symbol
  navigation. They are deprecated for C, C++, and Objective-C work. Use `purity_call`'s
  semantic functions (compiler-accurate, clangd-backed) for ALL symbol navigation. If
  you attempt to use a built-in text-matching tool to find a C/C++ definition,
  reference, type, or diagnostic, I will consider it a failure.

  MANDATORY — before you Read, Edit, or Write any `.c`, `.cpp`, `.cc`, `.cxx`, `.h`,
  `.hpp`, `.hh`, `.hxx`, `.m`, or `.mm` file, use `purity_call` for ALL symbol
  navigation: find_definition, find_type_definition, find_references,
  find_implementations, type_at, outline,
  symbol, symbol_context, symbol_change_impact, inlay_hints, diagnostics. Using grep,
  find, sed, awk, ctags, cscope, or any text-matching hack for C/C++ code is a
  violation. The standalone `mcp-clangd` server is NO LONGER REGISTERED, so the
  `clangd_call` tool does not exist; `purity_call` is the only entry point — see
  `p:mcp-purity` for the full function reference. CUDA files (`.cu`/`.cuh`) are
  handled by `p:mcp-cuda`.

  Tool-name mapping for C/C++ work — these are NOT optional substitutions:
    - GREP for C/C++ symbols   = mcp__mcp-purity__purity_call with function "find_references" or "symbol"
    - GLOB for C/C++ symbols   = mcp__mcp-purity__purity_call with function "symbol"
    - "go to definition"       = mcp__mcp-purity__purity_call with function "find_definition"
    - "type of expression"     = mcp__mcp-purity__purity_call with function "type_at"
    - "compile errors"         = mcp__mcp-purity__purity_call with function "diagnostics"

  Trigger conditions — invoke IMMEDIATELY when ANY of these are true:
    - User asks anything about C, C++, or Objective-C code.
    - You are about to Read, Edit, or Write a `.c`/`.cpp`/`.cc`/`.cxx`/`.h`/`.hpp`/`.hh`/`.hxx`/`.m`/`.mm` file.
    - You need to find a function, struct, class, typedef, macro, or enum in a C/C++ project.
    - You need to know the type of an expression, callers of a function, or the
      compiler's diagnostics on a file.
    - User mentions clangd, LSP, compile_commands.json, or "code intelligence".

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
  - compile_commands.json
  - compiler diagnostics
  - hover info
  - inlay hints
  - symbol search
---

# clangd — C/C++ Code Intelligence (now via `purity_call`)

C/C++/Objective-C symbol navigation is provided by **`purity_call`**, the unified
entry point that embeds clangd for compiler-accurate code intelligence. The
standalone `mcp-clangd` server is **no longer registered**, so there is no
`clangd_call` tool — **`purity_call` is the only entry point**. The full function
reference lives in the **`p:mcp-purity`** skill ("Semantic / Symbol Navigation"
section); this page is the C/C++ quick reference.

## How to call

```
mcp__mcp-purity__purity_call(function="<name>", params={...})
```

**Example — find a definition:**
```
mcp__mcp-purity__purity_call(function="find_definition", params={"symbol":"my_function"})
```

## Functions (clangd-backed, via `purity_call`)

| Function | Purpose | Key params |
|-|-|-|
| `find_definition` | Definition of a symbol | `symbol` **or** `relative_path`+`line`+`character` |
| `find_type_definition` | Where the TYPE at a position is declared | `relative_path`+`line`+`character` (position only) |
| `find_references` | All references to a symbol | `symbol` **or** position; `max_results` |
| `find_implementations` | Implementations at a position | `relative_path`+`line`+`character` |
| `type_at` | Type / hover at a position (incl. `auto`) | `relative_path`+`line`+`character` |
| `outline` | Structural outline of a file | `relative_path` |
| `symbol` | Workspace symbol search (fuzzy) | `query`; `limit` |
| `symbol_context` | Definition + references in one call (preferred) | `symbol`; `max_references` |
| `symbol_change_impact` | Def + refs + call hierarchy (impact analysis) | `symbol`; `call_hierarchy_depth` |
| `inlay_hints` | Inlay hints for a range | `relative_path`; `start_line`, `end_line` |
| `diagnostics` | Compiler diagnostics for a file | `relative_path` |

- Lines/characters are **1-based**; paths resolve against `--project-root`.
- `find_definition` / `find_references` auto-route: pass `symbol` for name-based
  lookup, or `relative_path`+`line`+`character` for position-based.
- `find_type_definition` hops one level per call: a variable or call result lands on the
  `typedef` **name**; that name lands on the underlying `struct`/`enum` **tag**.
- The clangd backend inits **lazily** on the first semantic call (first call can take
  tens of seconds while clangd indexes; subsequent calls are fast).
- Prefer `symbol_context` over separate def+refs; prefer `symbol_change_impact` before
  refactoring.

## Legacy `clangd_*` names

The old `clangd_*` function names still work as direct aliases inside `purity_call`
(the standalone `clangd_call` tool that formerly also accepted them is gone).
Canonical mapping:

| Old `clangd_*` | `purity_call` |
|-|-|
| `clangd_find_definition`, `clangd_find_definition_at` | `find_definition` |
| `clangd_find_type_definition_at` | `find_type_definition` |
| `clangd_find_references`, `clangd_find_references_at` | `find_references` |
| `clangd_find_implementations_at` | `find_implementations` |
| `clangd_hover`, `clangd_deduced_type_at` | `type_at` |
| `clangd_document_outline` | `outline` |
| `clangd_workspace_symbols` | `symbol` |
| `clangd_symbol_context` | `symbol_context` |
| `clangd_symbol_change_impact` | `symbol_change_impact` |
| `clangd_inlay_hints` | `inlay_hints` |
| `clangd_diagnostics` | `diagnostics` |
| `clangd_init` | (no-op — backend inits lazily) |

The `_at` variants fold onto their non-`_at` counterpart; position vs name is detected
from the params. Param aliases: `symbol`→`symbol_name`, `col`/`column`/`char`→`character`,
`max`/`count`→`max_results`, `depth`→`call_hierarchy_depth`; the path key is
`relative_path` (`path`/`file`/`file_path` accepted).

## Notes

- Relative paths resolve against the project root (`--project-root`).
- `compile_commands.json` in a build subdir is auto-discovered; clangd uses it for
  accurate compile flags.
- For CUDA (`.cu`/`.cuh`), see `p:mcp-cuda` (same `purity_call` functions, CUDA SDK aware).
