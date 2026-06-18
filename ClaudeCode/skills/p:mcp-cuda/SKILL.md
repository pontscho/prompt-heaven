---
name: p:mcp-cuda
description: >
  NEVER use the built-in `Grep`, `Glob`, or `Read`-and-search tools for CUDA symbol
  navigation. They are deprecated for CUDA (`.cu`, `.cuh`) work. ALWAYS use
  `cuda_call` ONLY. If you attempt to use a built-in tool to find a CUDA definition,
  reference, type, or diagnostic, I will consider it a failure.

  MANDATORY — before you Read, Edit, or Write any `.cu` or `.cuh` file, you MUST
  invoke this skill first and use `cuda_call` for ALL symbol navigation. Using
  grep, find, sed, awk, ctags, cscope, or any text-matching hack for CUDA code is
  a violation. For plain C/C++ files (`.c`/`.cpp`/`.h`/`.hpp`) use `p:mcp-clangd`
  instead; for CUDA files use THIS skill.

  Provides compiler-accurate CUDA code intelligence via clangd LSP with CUDA SDK
  awareness (auto-discovers `CUDA_PATH` / `nvcc` / `/usr/local/cuda-*`, detects
  `sm_xx` arch from `compile_commands.json` or CMakeCache.txt). Functions: find
  definitions, references, type definitions, implementations, diagnostics, hover
  types, document outline, refactoring impact, workspace symbol search, inlay
  hints, deduced types.
  One tool: `cuda_call`. 14 functions. All analysis calls are freely batchable.

  Tool-name mapping for CUDA work — these are NOT optional substitutions:
    - GREP for CUDA symbols    = mcp__mcp-cuda__cuda_call with function "cuda_find_references" or "cuda_workspace_symbols"
    - GLOB for CUDA symbols    = mcp__mcp-cuda__cuda_call with function "cuda_workspace_symbols"
    - "go to definition"       = mcp__mcp-cuda__cuda_call with function "cuda_find_definition" / "cuda_find_definition_at"
    - "type of expression"     = mcp__mcp-cuda__cuda_call with function "cuda_hover"
    - "deduced type (auto)"    = mcp__mcp-cuda__cuda_call with function "cuda_deduced_type_at"
    - "compile errors"         = mcp__mcp-cuda__cuda_call with function "cuda_diagnostics"

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

# cuda-mcp — CUDA Code Intelligence

The MCP server (`mcp-cuda.py`) exposes **one tool**: `cuda_call` — universal dispatcher for all 14 CUDA functions. Called without `function` returns server status (including detected CUDA SDK and arch). All operations go through `cuda_call(function=..., params={...})`.

Backed by clangd with `--cuda-path` / `--cuda-gpu-arch` flags. CUDA SDK and arch are auto-discovered on init; you only need to pass `project_root`.

## How to call any function

```
mcp__mcp-cuda__cuda_call(function="<function_name>", params={...parameters...})
```

**Example — find a kernel definition:**
```
mcp__mcp-cuda__cuda_call(function="cuda_find_definition", params={"symbol_name":"forward_moe"})
```

## Tool Reference (14 functions)

### cuda_call (status check)
Returns server status, project root, CUDA SDK path, and target arch when called without `function`.
```json
{}
```

### cuda_init
Initialize the clangd-CUDA session for a project. Auto-discovers CUDA SDK (CUDA_PATH → CUDA_HOME → nvcc on PATH → `/usr/local/cuda-*` → `/usr/local/cuda` → CMakeCache.txt) and auto-detects `sm_xx` arch from `compile_commands.json` or CMakeCache.txt (fallback `sm_86`).
```json
{
"project_root":"/abs/path/to/project",  // required
"cuda_path":"/usr/local/cuda-12.4",     // optional — explicit CUDA SDK
"cuda_arch":"sm_90",                    // optional — target arch
"compile_commands_dir":"build",         // optional — where compile_commands.json lives
"clangd_path":"clangd"                  // optional — clangd binary
}
```

### cuda_find_definition
Find the definition of a CUDA symbol by name.
```json
{
"symbol_name":"forward_moe",
"context_lines":5
}
```

### cuda_find_definition_at
Find definition at a file position (1-based line/character).
```json
{"path":"src/kernel.cu","line":42,"character":10,"context_lines":5}
```

### cuda_find_references
Find all references to a symbol by name.
```json
{"symbol_name":"forward_moe","max_results":50,"context_lines":3}
```

### cuda_find_references_at
Find references at a file position.
```json
{"path":"src/kernel.cu","line":42,"character":10,"max_results":50,"context_lines":3}
```

### cuda_find_implementations_at
Find implementations of an interface/virtual method at a position.
```json
{"path":"include/iface.cuh","line":15,"character":5,"context_lines":5}
```

### cuda_workspace_symbols
Fuzzy search for symbols across the workspace.
```json
{"query":"forward_","limit":50}
```

### cuda_document_outline
Structural outline of a CUDA file.
```json
{"path":"src/kernel.cu"}
```

### cuda_symbol_context
Definition + references for a symbol in one call. Preferred over separate `cuda_find_definition` + `cuda_find_references`.
```json
{"symbol_name":"forward_moe","max_references":20,"context_lines":5}
```

### cuda_inlay_hints
Inlay hints (parameter names, deduced types) for a file range.
```json
{"path":"src/kernel.cu","start_line":1,"end_line":9999,"limit":100}
```

### cuda_symbol_change_impact
Comprehensive impact analysis before changing a symbol: definition + references + call hierarchy.
```json
{"symbol_name":"forward_moe","max_references":50,"call_hierarchy_depth":1}
```

### cuda_hover
Hover info (type, documentation) at a position.
```json
{"path":"src/kernel.cu","line":10,"character":5}
```

### cuda_diagnostics
Compiler diagnostics (errors, warnings) for a file. Opens the file and waits for clangd's `publishDiagnostics` push.
```json
{"path":"src/kernel.cu","timeout":10.0}
```

### cuda_deduced_type_at
Deduced type at a position (useful for `auto`, `decltype`, lambda return types in device code).
```json
{"path":"src/kernel.cu","line":20,"character":8}
```

## Location object format

All location objects returned by this server follow this structure:
```json
{
"path":"src/kernel.cu",          // relative to project_root
"uri":"file:///abs/path/...",
"range":{                        // 0-based LSP
"start":{"line":9,"character":4},
"end":{"line":9,"character":15}
},
"range_human":{                  // 1-based human-readable
"start":{"line":10,"character":5},
"end":{"line":10,"character":16}
},
"line_text":"forward_moe<<<grid,block>>>(args);"
}
```

## Parallel call strategy

**Send multiple independent `cuda_call`s in a single response.** Server serializes execution, but only ONE model API round-trip is needed.

### Safe to batch (read-only)

|Function|Batch notes|
|-|-|
|`cuda_find_definition`|multiple symbols|
|`cuda_find_definition_at`|multiple positions|
|`cuda_find_references`|multiple symbols|
|`cuda_find_references_at`|multiple positions|
|`cuda_find_implementations_at`||
|`cuda_workspace_symbols`||
|`cuda_document_outline`|multiple files|
|`cuda_hover`|multiple positions|
|`cuda_inlay_hints`||
|`cuda_diagnostics`|multiple files|
|`cuda_deduced_type_at`|multiple positions|

- `cuda_symbol_context` batches def + refs internally — prefer it.
- `cuda_symbol_change_impact` batches def + refs + call hierarchy — prefer it for impact analysis.

## Common workflows

### Understand an unknown kernel
```
[BATCH] cuda_symbol_context {symbol_name:"forward_moe"}
 + cuda_document_outline {path:"src/kernel.cu"}
 + cuda_diagnostics {path:"src/kernel.cu"}
```

### Refactoring impact check
```
cuda_symbol_change_impact {symbol_name:"forward_moe", max_references:50}
```

### Multiple kernel definitions
```
[BATCH] cuda_find_definition {symbol_name:"forward_moe"}
 + cuda_find_definition {symbol_name:"backward_moe"}
 + cuda_find_definition {symbol_name:"top_k_router"}
```

### Diagnostics across CUDA translation units
```
[BATCH] cuda_diagnostics {path:"src/kernel.cu"}
 + cuda_diagnostics {path:"src/launcher.cu"}
 + cuda_diagnostics {path:"src/router.cu"}
```

## Notes

- **Lines and characters** in `params` are **1-based** (human-readable). Server converts to 0-based LSP internally.
- **`cuda_path` / `cuda_arch`**: auto-discovered on init from environment, `nvcc`, `compile_commands.json`, or CMakeCache.txt. Override only if detection picks wrong values.
- **`compile_commands_dir`**: pass this if `compile_commands.json` lives in a build subdirectory (e.g. `build/`).
- **Path resolution**: relative paths are resolved against `project_root`.
- **Plain C/C++ files (`.c`/`.cpp`/`.h`/`.hpp`)** are handled by `p:mcp-clangd`, NOT this skill.
