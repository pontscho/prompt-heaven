---
name: p:mcp-cuda
description: >
  NEVER use the built-in `Grep`, `Glob`, or `Read`-and-search tools for CUDA symbol
  navigation. They are deprecated for CUDA (`.cu`, `.cuh`) work. Use `purity_call`'s
  semantic functions (compiler-accurate, clangd-backed, CUDA SDK aware) for ALL symbol
  navigation. If you attempt to use a built-in text-matching tool to find a CUDA
  definition, reference, type, or diagnostic, I will consider it a failure.

  MANDATORY — before you Read, Edit, or Write any `.cu` or `.cuh` file, use
  `purity_call` for ALL symbol navigation: find_definition, find_references,
  find_implementations, type_at, outline, symbol, symbol_context, symbol_change_impact,
  inlay_hints, diagnostics. Using grep, find, sed, awk, ctags, cscope, or any
  text-matching hack for CUDA code is a violation. The standalone `cuda_call` tool
  still exists and works in parallel, but `purity_call` is the unified entry point —
  see `p:mcp-purity` for the full function reference. For plain C/C++ files
  (`.c`/`.cpp`/`.h`/`.hpp`) use `p:mcp-clangd`.

  `purity_call`'s backend is CUDA SDK aware: it auto-discovers `CUDA_PATH` / `nvcc` /
  `/usr/local/cuda-*` and detects `sm_xx` arch from `compile_commands.json` or
  CMakeCache.txt when it spins up clangd for a CUDA project.

  Tool-name mapping for CUDA work — these are NOT optional substitutions:
    - GREP for CUDA symbols    = mcp__mcp-purity__purity_call with function "find_references" or "symbol"
    - GLOB for CUDA symbols    = mcp__mcp-purity__purity_call with function "symbol"
    - "go to definition"       = mcp__mcp-purity__purity_call with function "find_definition"
    - "type of expression"     = mcp__mcp-purity__purity_call with function "type_at"
    - "compile errors"         = mcp__mcp-purity__purity_call with function "diagnostics"

  Trigger conditions — invoke IMMEDIATELY when ANY of these are true:
    - User asks anything about CUDA code, kernels, `__global__`, `__device__`,
      `__host__`, `__shared__`, thrust, cub, or CUDA SDK headers.
    - You are about to Read, Edit, or Write a `.cu` or `.cuh` file.
    - You need to find a kernel, device function, host function, or symbol in a
      CUDA project.
    - You need to know the type of an expression in a `.cu` file, callers of a
      kernel, or compiler diagnostics on a CUDA file.
    - User mentions cuda, cuda_call, nvcc, sm_xx, CUDA_PATH, CUDA_HOME, or "CUDA
      code intelligence".

triggers:
  - cuda
  - CUDA code
  - .cu file
  - .cuh file
  - kernel
  - __global__
  - __device__
  - __host__
  - __shared__
  - nvcc
  - sm_86
  - CUDA_PATH
  - CUDA_HOME
  - thrust
  - cub
  - cuda_call
  - cuda code intelligence
  - find definition
  - find references
  - go to definition
  - compile_commands.json
---

# cuda — CUDA Code Intelligence (now via `purity_call`)

CUDA symbol navigation is provided by **`purity_call`**, the unified entry point that
embeds clangd (CUDA SDK aware: auto-discovers `CUDA_PATH`/`nvcc`/`/usr/local/cuda-*`
and `sm_xx` arch). The standalone `cuda_call` tool still exists and works in parallel,
but **prefer `purity_call`**. The full function reference lives in the **`p:mcp-purity`**
skill ("Semantic / Symbol Navigation" section); this page is the CUDA quick reference.

## How to call

```
mcp__mcp-purity__purity_call(function="<name>", params={...})
```

**Example — find a kernel definition:**
```
mcp__mcp-purity__purity_call(function="find_definition", params={"symbol":"forward_moe"})
```

## Functions (clangd-backed, CUDA aware, via `purity_call`)

| Function | Purpose | Key params |
|-|-|-|
| `find_definition` | Definition of a symbol/kernel | `symbol` **or** `relative_path`+`line`+`character` |
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
- The clangd backend inits **lazily** on the first semantic call; for a CUDA project it
  discovers the CUDA SDK and `sm_xx` arch automatically. The first call can take tens
  of seconds while clangd indexes.
- Prefer `symbol_context` over separate def+refs; prefer `symbol_change_impact` before
  refactoring.

## Legacy `cuda_*` names

The old `cuda_*` function names still work — both through the standalone `cuda_call`
tool and as direct aliases inside `purity_call`. Canonical mapping:

| Old `cuda_*` | `purity_call` |
|-|-|
| `cuda_find_definition`, `cuda_find_definition_at` | `find_definition` |
| `cuda_find_references`, `cuda_find_references_at` | `find_references` |
| `cuda_find_implementations_at` | `find_implementations` |
| `cuda_hover`, `cuda_deduced_type_at` | `type_at` |
| `cuda_document_outline` | `outline` |
| `cuda_workspace_symbols` | `symbol` |
| `cuda_symbol_context` | `symbol_context` |
| `cuda_symbol_change_impact` | `symbol_change_impact` |
| `cuda_inlay_hints` | `inlay_hints` |
| `cuda_diagnostics` | `diagnostics` |
| `cuda_init` | (no-op — backend inits lazily) |

The `_at` variants fold onto their non-`_at` counterpart; position vs name is detected
from the params. Param aliases: `symbol`→`symbol_name`, `col`/`column`/`char`→`character`,
`max`/`count`→`max_results`, `depth`→`call_hierarchy_depth`; the path key is `relative_path`.

## Notes

- A CUDA-mode backend uses a `.cu`-only compile DB; in a mixed C/C++ + CUDA project the
  C/C++ coverage may be reduced (a known Phase-0 limitation).
- `compile_commands.json` in a build subdir is auto-discovered.
- For plain C/C++ files (`.c`/`.cpp`/`.h`/`.hpp`), see `p:mcp-clangd`.
