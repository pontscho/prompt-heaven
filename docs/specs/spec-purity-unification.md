---
name: spec-purity-unification
type: spec
status: active
title: 'Implementation Plan: Unify mcp-clangd + mcp-cuda into mcp-purity (Phase 0)'
description: Phase 0 implementation plan for folding clangd + cuda into mcp-purity behind purity_call. Decision recorded in adr 0001.
sources:
  - Scripts/mcp-purity.py
  - Scripts/mcp-purity.py:LspBackend
  - Scripts/mcp-purity.py:ClangdClient
  - Scripts/mcp-purity.py:_route_filetype
  - Scripts/mcp-purity.py:_resolve_aliases
  - Scripts/mcp-purity.py:handle_find_definition
  - Scripts/_mcp_smoke_test.py:purity_semantic_checks
  - Scripts/mcp-clangd.py
  - Scripts/mcp-cuda.py
verified:
  commit: 095db60
  date: 2026-08-10
links:
  - scripts
  - 0001-purity-server-unification
  - spec-purity-luals-phase1
---

# Implementation Plan: Unify `mcp-clangd` + `mcp-cuda` into `mcp-purity` (Phase 0 — the skeleton)

> The decision, alternatives, and consequences behind this plan are recorded in the ADR: [[0001-purity-server-unification]]. This page is the living WHAT/HOW.

## Requirements Summary

Merge the **semantic code-navigation** capabilities of the `mcp-clangd` and `mcp-cuda` MCP servers into the existing `mcp-purity` server, behind the unchanged `purity_call` entry point. The server grows *inward*: it gains a layered backend architecture (sync file layer + async LSP backend layer + filetype routing) while its tool name, dispatcher tool, and the project-wide `purity_call` convention stay exactly as they are.

This is **Phase 0** — the *skeleton*. It establishes the abstract backend interface, folds clangd+cuda into a single "clangd-family" backend, adds the semantic function family to the `purity_call` dispatcher, unifies the duplicated grep core, fixes the truncated CUDA fallback bug, and keeps every legacy name working via aliases.

**Explicitly deferred (NOT this phase):**
- **Phase 1**: `luals` (lua-language-server) as a second LSP backend behind the same interface.
- **Phase 2**: retiring the standalone `clangd_call` / `cuda_call` tools and migrating the `p:mcp-clangd` / `p:mcp-cuda` skills and minion tool-lists.

### Success Criteria
- [ ] `purity_call` accepts the 10 new semantic functions: `find_definition`, `find_references`, `find_implementations`, `type_at`, `diagnostics`, `outline`, `symbol`, `symbol_context`, `inlay_hints`, `symbol_change_impact`.
- [ ] `find_definition` / `find_references` accept **either** `symbol` **or** `at` (path,line,character) and route to the correct internal handler by parameter shape.
- [ ] All legacy names resolve as aliases: `clangd_find_definition`, `clangd_find_definition_at`, `cuda_find_definition`, … all reach the unified canonical handler.
- [ ] The existing file-IO and search functions (`read_file`, `create_text_file`, `replace_content`, `replace_lines`, `insert_at_line`, `delete_lines`, `search_for_pattern`, `list_dir`, `glob`) behave **identically** to today (zero regression).
- [ ] `glob` is the canonical name for file-glob search; `find_file` remains as an alias.
- [ ] CUDA symbol-by-name lookup finds `static` / `__device__` symbols via the filesystem-grep fallback (the current CUDA truncation bug is gone).
- [ ] The C/C++ and CUDA code paths share one `ClangdClient` implementation (no byte-for-byte duplication).
- [ ] The server is **stdlib-only** (Python 3.9+, zero external dependencies) — verified by inspecting the final import block.
- [ ] The file layer answers immediately with no LSP init; the LSP backend lazily spins up only when a semantic function is called for a filetype that has sources in the project.
- [ ] A clangd subprocess crash no longer leaves request futures hanging until timeout — pending futures are failed promptly on reader EOF.
- [ ] `tools/list` still advertises exactly one tool (`purity_call`); the new functions are documented in its description, including a steering sentence on `search_for_pattern`.

### Scope

**In Scope:**
- Folding the clangd+cuda common LSP core (framing, `ClangdClient`, helpers, fallback) into `mcp-purity.py`.
- An abstract LSP-backend interface and a `clangd`-family backend (C/C++/CUDA in one backend, language as config).
- A backend map keyed by **LSP-backend type** (`clangd` now; `luals` later), selected by filetype routing.
- The 10 semantic functions wired into the `purity_call` dispatcher, with `symbol`/`at` parameter routing.
- A single DRY word-search core feeding the semantic A-class fallback.
- Extending the alias layer (legacy `clangd_*` / `cuda_*` names + `glob` canonical) and unifying `_resolve_aliases` precedence.
- Three bug fixes: CUDA `_symbol_to_location` truncation; clangd-crash future leak; `_resolve_aliases` precedence divergence.
- Lazy init for the LSP layer; promoting the `tools/call` dispatch to async with executor-wrapped sync file handlers.
- Updating the `purity_call` tool *description* (not the skill files) to document the semantic functions and steer symbol intent away from raw grep.

**Out of Scope:**
- `luals` backend (Phase 1).
- Retiring `clangd_call` / `cuda_call` tools; rewriting `p:mcp-clangd` / `p:mcp-cuda` skills and minion tool-lists (Phase 2).
- Any change to the project-wide `purity_call` convention in global `CLAUDE.md`, skills, or minion definitions.
- New semantic capabilities beyond what clangd/cuda already implement.
- Multi-project support in one server process (still one project per server, as today).

### Assumptions & Constraints

**Assumptions:**
- `type_at` treats `hover` as primary and `deduced_type_at` as a complement (for `auto`/template-deduced types); the unified handler returns hover and augments with the deduced type where the LSP provides one.
- `symbol_change_impact` has a "mixed" fallback: the references part is grep-degradable (A-class), the call-hierarchy part is not (B-class) — it returns a partial-but-honest result if the LSP yields no call hierarchy.
- The backend map is effectively single-entry (`clangd`) in Phase 0, but the data structure is already a map (luals-ready).
- `_resolve_aliases` is unified onto the clangd-style "last-wins" semantics (`mcp-clangd.py:74`); the purity "first-wins" (`mcp-purity.py:175`) is the one that changes. Rationale: last-wins is the simpler mental model (a later explicit key overrides an earlier alias) and matches the larger of the two codebases being folded in.
- The unified server stays a **single file** (`mcp-purity.py`, ~3000 lines), matching the project's existing one-file-per-server convention and the single-script MCP registration.

**Constraints:**
- **Python 3.9+, standard library only, zero external dependencies** (hard constraint). The `# dependencies = []` header in `mcp-purity.py:4` must remain accurate.
- The `purity_call` tool name and dispatcher contract must not change (project-wide convention depends on it).
- The existing file-IO / search behavior must not regress.
- LSP communication must use only stdlib (`asyncio.create_subprocess_exec`, `json`, byte-level Content-Length framing) — already true of clangd/cuda today.

### Non-Functional Requirements
- **Performance**: file-layer latency unchanged (sync handlers, executor-wrapped); LSP init is lazy and must not block the MCP handshake or `tools/list`; long `search_for_pattern` / `os.walk` runs execute in the thread-pool executor so they do not freeze the event loop.
- **Security**: file operations remain confined to the `--project-root` sandbox (unchanged); the LSP backend spawns only the configured `clangd` binary; no new network or shell surface.
- **Scalability**: one project per server; the backend map allows N LSP-backend types within that one project (clangd now, luals later).
- **Accessibility**: N/A (developer tooling, no UI).

## Architecture Analysis (pre-Phase-0 snapshot)

This section and the one after it describe the three servers **as they were before Phase 0 was implemented** — the state the plan was written against. They are kept verbatim as the planning record; the line numbers throughout are archival and are not current positions. At the time of writing, all three servers were single-file Python 3.9 stdlib scripts under `Scripts/`, each registering exactly one MCP tool that dispatched to internal functions.

**`mcp-purity.py`, as it stood before Phase 0 (the reference architecture):**
- Dispatch is a **dict table** `HANDLERS` (`mcp-purity.py:776-790`), not an if/elif chain. Aliases (`ls`, `glob`, `grep`, `search`) appear *directly as keys* in `HANDLERS` **and** in a separate `FUNCTION_ALIASES` dict (`mcp-purity.py:118-123`) — redundant but not broken.
- Parameter aliasing has two layers: `PARAM_ALIASES` (global, `mcp-purity.py:68-93`) and `PARAM_ALIASES_BY_FUNC` (per-function, `mcp-purity.py:99-113`), resolved by `_resolve_aliases` (`mcp-purity.py:145-177`), which is **first-wins** (`if canonical not in resolved`, line 175).
- The dispatcher `handle_purity_call` (`mcp-purity.py:840-878`) is **sync**; all file handlers are sync `def`.
- The MCP loop `McpServer.run()` is **async** (`mcp-purity.py:980`) but only `await`s the blocking stdin read via `run_in_executor` (`mcp-purity.py:985`); `_handle_message` and the `tools/call` path (`mcp-purity.py:1000`, `1043`, `1063`) are sync. Entry: `asyncio.run(server.run())` (`mcp-purity.py:1135`).
- `tools/list` returns one tool `PURITY_CALL_TOOL` (`mcp-purity.py:1035`); only `initialize`, `ping`, `tools/list`, `tools/call` are handled.

**`mcp-clangd.py`, as it stood before Phase 0 (the LSP reference):**
- `ClangdClient` (`mcp-clangd.py:263-394`) owns the full LSP lifecycle: `start()` (subprocess + reader loop + `initialize` handshake + indexing wait + `_prime_index`), `stop()`, `_reader_loop()`, `_send()`, `_request()`.
- JSON-RPC framing: `encode_lsp_message` (`mcp-clangd.py:107-112`), `read_lsp_message` (`mcp-clangd.py:115-148`) — Content-Length header + UTF-8 body.
- Request/response correlation via integer id + `loop.create_future()` stored in `_pending`, awaited with `asyncio.wait_for` (`mcp-clangd.py:455-466`).
- All handlers are `async def`; the dispatcher `handle_clangd_call` is `async` (`mcp-clangd.py:1557`); auto-init task + 90s `asyncio.wait_for(asyncio.shield(...))` gate (`mcp-clangd.py:1584-1591`, `1940-1948`).
- Module-level singleton `_client` (`mcp-clangd.py:611`), `_require_client` (`mcp-clangd.py:614-617`).
- Fallback: `_find_files_with_word` (`mcp-clangd.py:726-748`), `_fallback_workspace_symbols` (`mcp-clangd.py:762-801`), `_symbol_to_location` 3-tier cascade (`mcp-clangd.py:808-901`).
- `_resolve_aliases` is **last-wins** (`mcp-clangd.py:74`).

**`mcp-cuda.py`, as it stood before Phase 0:**
- The entire common LSP core is **byte-for-byte copy-paste** from clangd (framing `mcp-cuda.py:457-495`, helpers `502-575`, `ClangdClient` body, fallback). The only functional divergence is the **truncated** `_symbol_to_location` (`mcp-cuda.py:1130-1187`) — it lacks tiers 2 and 3 of the clangd cascade.
- CUDA-specific layer (the abstraction boundary): `_find_cuda_sdk` (`mcp-cuda.py:117-193`), `_detect_cuda_arch` (`196-226`), `NVCC_STRIP_FLAGS/PREFIXES` (`233-251`), `_expand_rsp_file` (`254-264`), `_translate_compile_commands` (`267-346`), `_prepare_compile_commands` (`349-395`), `_generate_minimal_compile_commands` (`398-427`), `_has_cuda_sources` (`430-450`), CUDA `ClangdClient.start` extras (`608-682`), CUDA `_prime_index` (`684-703`), CUDA `handle_init` (`1194-1242`, skip at `1228-1230`).
- `_detect_language` differs: clangd (`mcp-clangd.py:236-240`) `.c`→"c" else "cpp"; cuda (`mcp-cuda.py:578-584`) `.cu`/`.cuh`→"cuda", `.c`→"c", else "cpp".

**Testing patterns**: not yet surveyed. The plan's testing strategy assumes a stdlib `unittest`-based approach and an MCP-protocol smoke test; the existing test infrastructure under the repo must be confirmed before/within the validation loop (see Testing Strategy → note).

**Error handling pattern (purity)**: dispatcher returns `{"error": str(exc)}` for `ValueError`/`FileNotFoundError`/`OSError`, `{"error": "Internal error in '...': Type: msg"}` otherwise; success returns `{"__raw_text__": "..."}` (`mcp-purity.py:863-878`).

**Logging**: `logging` module, stderr (clangd writes clangd stderr through to `sys.stderr`).

## Captured Information (pre-Phase-0 snapshot, for the implementation phase)

**ARCHIVED SNAPSHOT — this is the pre-Phase-0 state.** This section carried the concrete code so the implementation agent need not re-read the source files. Every line number below refers to the files **as they stood before Phase 0 was implemented**; it is preserved as the historical planning record and does NOT describe current positions. For where this code lives today, use the frontmatter `sources:` anchors.

### File Locations
| Purpose | File Path | Location/Line |
|---------|-----------|---------------|
| Target: unified server | `Scripts/mcp-purity.py` | grow inward across the file |
| Dispatcher dict to extend | `Scripts/mcp-purity.py` | `HANDLERS` at `:776-790` |
| Function-alias dict to extend | `Scripts/mcp-purity.py` | `FUNCTION_ALIASES` at `:118-123` |
| Alias resolver to unify | `Scripts/mcp-purity.py` | `_resolve_aliases` at `:145-177` |
| Dispatcher to make async | `Scripts/mcp-purity.py` | `handle_purity_call` `:840-878`, `_handle_tool_call` `:1043-1063` |
| MCP loop (already async) | `Scripts/mcp-purity.py` | `McpServer.run` `:980`, stdin executor `:985` |
| Tool description to extend | `Scripts/mcp-purity.py` | `PURITY_CALL_TOOL` (used at `tools/list` `:1035`) |
| Source: LSP core | `Scripts/mcp-clangd.py` | framing `:107-148`, `ClangdClient` `:263-466`, fallback `:726-901` |
| Source: CUDA language config | `Scripts/mcp-cuda.py` | `:117-450`, `:608-703`, `:1194-1242` |
| Source: truncated fallback (bug ref) | `Scripts/mcp-cuda.py` | `_symbol_to_location` `:1130-1187` |

### Imports/Includes (current — must stay stdlib-only)
```python
# mcp-purity.py:25-33 (current)
import argparse, asyncio, fnmatch, json, logging, os, re, sys
from typing import Any, Callable, Dict, List, Optional

# To add for the LSP layer (all stdlib):
import pathlib   # used by clangd path<->uri helpers
# asyncio already imported; subprocess via asyncio.create_subprocess_exec
# For CUDA config (from mcp-cuda.py:26-37, all stdlib):
import glob as glob_mod   # aliased to avoid colliding with the `glob` function name
import shutil             # which()/copy for SDK + cache dir
# NOTE: `tempfile` is imported in mcp-cuda.py:36 (NOT :33 — that line is `import shutil`) but appears unused — do NOT carry it over. [inspector L1]
```

### The unification boundary: common core vs. CUDA config
**Common LSP core (fold once into the unified server — identical in clangd & cuda):**
`encode_lsp_message`, `read_lsp_message`, `uri_to_path`, `path_to_uri`, `SYMBOL_KIND_MAP`, `DEFINITION_KINDS`, `symbol_kind_name`, `extract_code_range`, `extract_surrounding_code`, `_relative_path`, and the entire `ClangdClient` body (`__init__`, `_reader_loop`, `_send`, `_request`, `_notify`, `open_document`, `workspace_symbol`, `document_symbol`, `definition`, `references`, `implementation`, `hover`, `inlay_hints`, `prepare_call_hierarchy`, `call_hierarchy_incoming/outgoing`, `get_diagnostics`, `_abs_uri`, `_abs_path`), plus `_FALLBACK_EXTS`, `_FALLBACK_SKIP_DIRS`, `_find_files_with_word`, `_fallback_workspace_symbols`, and the `_symbol_to_location` cascade.

**CUDA-only config (becomes the language-config of the clangd-family backend):**
`_find_cuda_sdk`, `_detect_cuda_arch`, `NVCC_STRIP_FLAGS`, `NVCC_STRIP_PREFIXES`, `_expand_rsp_file`, `_translate_compile_commands`, `_prepare_compile_commands`, `_generate_minimal_compile_commands`, `_has_cuda_sources`, the CUDA `start()` extras (cuda_path/cuda_arch + `_prepare_compile_commands` before `initialize`), the CUDA `_prime_index` filter (`.cu`/`.cuh`), and `_detect_language`'s `.cu`/`.cuh`→"cuda" branch.

### Reference: the LSP request primitive (copy/adapt from clangd)
```python
# mcp-clangd.py:455-466 — request/response correlation
async def _request(self, method, params, timeout=10.0):   # live default is 10.0 (mcp-clangd.py:455), NOT 15.0 [inspector I1]
    req_id = self._next_id
    self._next_id += 1
    fut = self._loop.create_future()
    self._pending[req_id] = fut
    await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
    try:
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        self._pending.pop(req_id, None)
        return {"error": {"message": f"timeout waiting for {method}"}}
```

### Bug fix #1 — CUDA `_symbol_to_location` truncation (the missing tiers)
The CUDA version (`mcp-cuda.py:1183-1187`) ends after the workspace/symbol retry loop. The clangd version (`mcp-clangd.py:872-901`) continues with two more tiers that must be present in the unified single handler:
```python
# Tier 2: document_symbol on the preferred file
if abs_preferred:
    try:
        doc_syms = await client.document_symbol(abs_preferred)
        file_uri = pathlib.Path(abs_preferred).as_uri()
        for sym in _outline_flatten(doc_syms):
            if sym.get("name") == symbol_name:
                sel = sym.get("selectionRange") or sym.get("range") or {}
                return _make_entry(file_uri, sel.get("start", {}))
    except Exception:
        pass

# Tier 3: grep project tree, then document_symbol on each candidate
if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol_name):
    candidates = _find_files_with_word(client.project_root, symbol_name, limit=10)
    if abs_preferred:
        candidates = [c for c in candidates if c != abs_preferred]
    for fpath in candidates:
        try:
            doc_syms = await client.document_symbol(fpath)
        except Exception:
            continue
        file_uri = pathlib.Path(fpath).as_uri()
        for sym in _outline_flatten(doc_syms):
            if sym.get("name") == symbol_name and symbol_kind_name(sym.get("kind", 0)) in DEFINITION_KINDS:
                sel = sym.get("selectionRange") or sym.get("range") or {}
                return _make_entry(file_uri, sel.get("start", {}))
return None
```
Because the unified server has **one** `_symbol_to_location`, the fix is automatic: there is no CUDA-specific copy to truncate.

### Bug fix #2 — clangd-crash future leak
`_reader_loop` exits on EOF (`mcp-clangd.py:401-404`) but does not fail outstanding futures, so callers block until timeout. On EOF, drain `_pending`:
```python
# add to _reader_loop EOF handling
for req_id, fut in list(self._pending.items()):
    if not fut.done():
        fut.set_exception(RuntimeError("clangd backend terminated unexpectedly"))
    self._pending.pop(req_id, None)
```

### Bug fix #3 — `_resolve_aliases` precedence + param-alias merge
Unify onto **last-wins** (clangd `mcp-clangd.py:74`: `resolved[canonical] = value`). The purity resolver (`mcp-purity.py:145-177`) currently guards with `if canonical not in resolved` (first-wins, line 175) — change it to overwrite. Add a unit test pinning the chosen semantics.

**[inspector M1] — more than precedence diverges:** purity's resolver signature is `(params, function)` with a two-tier `PARAM_ALIASES_BY_FUNC` + `PARAM_ALIASES` (`mcp-purity.py:145-177`); clangd's is `(params)` with a single flat `PARAM_ALIASES` (`mcp-clangd.py:56-76`). The semantic handlers depend on clangd's param aliases — `symbol→symbol_name`, `col/column/char→character`, `max→max_results`, `depth→call_hierarchy_depth` (`mcp-clangd.py:32-53`). These MUST be merged into purity's `PARAM_ALIASES` (or a semantic `PARAM_ALIASES_BY_FUNC` block). Keep purity's `(params, function)` signature. Without this merge the semantic handlers' param contract breaks. (CUDA-only init param aliases `arch`/`gpu_arch`/`cuda_sdk`/`sdk_path` at `mcp-cuda.py:44-63` are intentionally DROPPED — lazy init removes the init params; [inspector L-new-1].)

### Dispatcher pattern (extend the dict table)

**CRITICAL [inspector C1] — `FUNCTION_ALIASES` does NOT route.** The dispatcher looks up the handler with the RAW function name: `handler = HANDLERS.get(function)` (`mcp-purity.py:858`), NOT the canonicalized one. `_canonical_function`/`FUNCTION_ALIASES` is consulted only for param-alias resolution and error hints (`:846`, `:850`, `:867`), never for routing. Today aliases work ONLY because `glob`/`grep`/`search`/`ls` are ALSO direct `HANDLERS` keys (`:780-789`) — that redundancy is load-bearing, not dead. Therefore: every legacy `clangd_*`/`cuda_*` name AND every canonical semantic name MUST be a DIRECT `HANDLERS` key. (Alternative: change `:858` to look up `canonical_func` and consolidate aliases into `FUNCTION_ALIASES` — pick ONE, do not split.) Add a test asserting `purity_call function="clangd_find_definition"` actually dispatches.

```python
# mcp-purity.py:776-790 — extend HANDLERS with canonical semantic names + legacy aliases AS DIRECT KEYS
HANDLERS = {
    # ... existing file/search handlers ...
    "find_definition":       handle_find_definition,      # symbol OR at
    "find_references":       handle_find_references,       # symbol OR at
    "find_implementations":  handle_find_implementations,  # at
    "type_at":               handle_type_at,
    "diagnostics":           handle_diagnostics,
    "outline":               handle_outline,
    "symbol":                handle_symbol,
    "symbol_context":        handle_symbol_context,
    "inlay_hints":           handle_inlay_hints,
    "symbol_change_impact":  handle_symbol_change_impact,
    "glob":                  handle_find_file,             # glob now canonical
    "find_file":             handle_find_file,             # alias kept
    # legacy aliases route to the unified canonical handlers:
    "clangd_find_definition": handle_find_definition, "clangd_find_definition_at": handle_find_definition,
    "cuda_find_definition":   handle_find_definition, "cuda_find_definition_at":   handle_find_definition,
    # ... (full legacy set for all 10 functions, both clangd_ and cuda_ prefixes) ...
}
```

### `symbol`/`at` parameter routing (Decision B→1)
The canonical `find_definition` handler inspects the **post-alias** resolved params: if `line`/`character` present → position path (former `*_definition_at`); else if `symbol_name` present → name path (`_symbol_to_location`). **[inspector M2]** the merged alias layer renames `symbol`→`symbol_name` (clangd `PARAM_ALIASES`, `mcp-clangd.py:36`), so the router MUST check the post-alias key `symbol_name`, not `symbol`. Keep the two internal handlers; only the dispatch entry is unified. Note: `glob` as a *function* name (canonical file-search) is a different namespace from `glob` as a *param* alias → `paths_include_glob` (`mcp-purity.py:86`); document the distinction so they are not conflated.

### Backend map + lazy init (Decision 2)
```python
# Module-level: replace the single _client singleton with a type-keyed map
_backends: Dict[str, "ClangdClient"] = {}   # key: "clangd" (Phase 0); "luals" (Phase 1)

def _require_backend(filetype: str) -> "ClangdClient":
    backend_type = _route_filetype(filetype)   # ".c/.cpp/.cu/.cuh" -> "clangd"
    client = _backends.get(backend_type)
    if client is None or client.process is None:
        raise RuntimeError(f"LSP backend '{backend_type}' not initialized for filetype '{filetype}'")
    return client
```
Lazy init **[inspector H2 — this is NEW logic, not reuse]**: the gate at `mcp-clangd.py:1584-1591` only WAITS for an already-started init task; it does NOT start one (clangd starts init eagerly at `run()` from `--project-root`, `mcp-clangd.py:1940-1948`). The unified server must author the lazy TRIGGER: on first semantic call for a filetype, if `_backends[type]` is absent, start init (guarded `_has_cuda_sources`-style by whether the project has sources of that type), then reuse ONLY the `asyncio.wait_for(asyncio.shield(init_task), timeout=90)` WAIT. New correctness surface to handle explicitly: (a) once-only init guard per backend type; (b) concurrent first-calls racing to init → single in-flight init task per type; (c) init-failure caching (do not re-spawn a crashing clangd on every call); (d) per-backend init state stored in the `_backends` map. The file layer never triggers init.

### sync→async illesztés (Decision 1→2)
Promote `_handle_tool_call` / the `tools/call` path to `async`. For sync file handlers, wrap in the executor:
```python
result = await loop.run_in_executor(None, lambda: sync_handler(params, project_root, strict))
```
Semantic handlers are `async` and are `await`ed directly. The MCP loop is already async (`mcp-purity.py:980`), and the executor pattern already exists (`mcp-purity.py:985`).

### Error Handling Pattern (keep purity's)
```python
# mcp-purity.py:863-878
except (ValueError, FileNotFoundError, OSError) as exc:
    return {"error": str(exc)}
except Exception as exc:
    return {"error": f"Internal error in '{function}': {type(exc).__name__}: {exc}"}
```
Semantic B-class functions (`type_at`, `diagnostics`, `outline`, `find_implementations`) return an honest error when the LSP yields nothing / has no index — NOT a grep fallback.

### Build System Entry
No build system — these are standalone scripts. MCP registration points at `Scripts/mcp-purity.py` (unchanged). No new file, no new registration entry.

## Decision & Alternatives

The decision — layered in-place unification, chosen over a thin facade (Option 1) and a full async rewrite (Option 2) — with full pros/cons and consequences is recorded in the ADR: [[0001-purity-server-unification]].

## Implementation Strategy

Grow `mcp-purity.py` inward in dependency order: bring the common LSP core in first, define the backend abstraction, fold CUDA in as language-config, fix the bugs that the unification exposes/enables, then wire the dispatcher and aliases, and finally flip the dispatch to async.

### Data Model / API Changes
- No persistent data model. The "API" is the `purity_call` function set, extended by 10 semantic functions + `glob` canonicalization + legacy aliases. Backwards compatible: every existing name keeps working.

### Backwards Compatibility & Migration
- File-IO / search functions unchanged. Legacy `clangd_*` / `cuda_*` function names become aliases inside `purity_call`. The standalone `clangd_call` / `cuda_call` tools keep running in parallel (Phase 0); their retirement is Phase 2. Rollback = revert `mcp-purity.py` (single file).

### New Dependencies
None. Hard stdlib-only constraint; `# dependencies = []` stays accurate.

### Configuration Changes
- No new env vars required. CUDA SDK discovery / arch detection reuse the existing `_find_cuda_sdk` / `_detect_cuda_arch` logic (env `CUDA_PATH`, `PATH`, `/usr/local/cuda-*`, CMakeCache). The compile_commands cache dir (`.cache/mcp-cuda/…`) is reused.

## Step-by-Step Implementation Plan
1. **Fold the common LSP core** into `mcp-purity.py`: framing (`encode_lsp_message`/`read_lsp_message`), path/uri helpers, `SYMBOL_KIND_MAP`/`DEFINITION_KINDS`, `extract_*`, and the full `ClangdClient` class. Add `import pathlib`. Verify stdlib-only.
2. **Define the abstract backend interface** the dispatcher uses: `start()`, `definition`, `references`, `implementation`, `hover`, `document_symbol`, `inlay_hints`, `get_diagnostics`, call-hierarchy, plus `project_root` / `process` attributes. `ClangdClient` is its first implementation.
3. **Fold the CUDA language-config**: `_find_cuda_sdk`, `_detect_cuda_arch`, NVCC strip tables, `_expand_rsp_file`, `_translate_compile_commands`, `_prepare_compile_commands`, `_generate_minimal_compile_commands`, `_has_cuda_sources`; merge the CUDA `start()` extras and `_prime_index` filter; merge `_detect_language` to cover `.c/.cpp/.cu/.cuh`.
4. **Fix bug #1 (CUDA fallback)** by ensuring the single `_symbol_to_location` carries all 3 tiers (it does, by construction).
5. **Unify the DRY grep core**: keep one `_find_files_with_word` feeding the A-class semantic fallback (`_fallback_workspace_symbols` + `_symbol_to_location` tier 3). Leave the full-featured `search_for_pattern` separate (its `document_symbol` post-processing is LSP-specific and does not fold into the file grep).
6. **Backend map + lazy init**: replace the `_client` singleton with `_backends: Dict[str, ClangdClient]`, add `_require_backend(filetype)` + `_route_filetype`, and author the NEW lazy-init trigger (once-only guard, concurrent-first-call coalescing, init-failure caching — [inspector H2]) reusing ONLY the shielded 90s WAIT gate. Site the project-root mismatch warning (`mcp-clangd.py:921-935`) here, since there is no `handle_init` anymore ([inspector M3]).
7. **Wire semantic handlers** into `HANDLERS` with canonical names; implement `symbol`/`at` parameter routing on POST-alias keys ([inspector M2]) in `find_definition`/`find_references`; merge `hover`+`deduced_type_at` into `type_at`. Create the canonical `handle_find_implementations` (wrap/rename `handle_find_implementations_at` — no name-based variant exists in source, [inspector L2]), and the canonical `outline`/`symbol` handlers (← source `handle_document_outline`/`handle_workspace_symbols`, [inspector L3]).
8. **Extend the alias layer**: add all legacy `clangd_*` / `cuda_*` names as DIRECT `HANDLERS` keys (NOT only `FUNCTION_ALIASES` — that dict does not route, [inspector C1]); make `glob` canonical with `find_file` as alias. Enumerate the exact legacy→canonical map (incl. `*_at`, `workspace_symbols`→`symbol`, `document_outline`→`outline`, `hover`/`deduced_type_at`→`type_at`) and decide the fate of `clangd_init`/`cuda_init` (no canonical equivalent under lazy init → make them no-op/deprecation-notice handlers, not errors). Also extend `HANDLER_ACCEPTED_PARAMS` (`mcp-purity.py:797-833`) and `HANDLER_DESCRIPTIONS`/`--list` (`:1089-1099`) for the 10 functions. **Fix bug #3 + merge clangd `PARAM_ALIASES`**: unify `_resolve_aliases` onto last-wins and fold in clangd's param aliases.
9. **Fix bug #2 (crash future leak)**: drain `_pending` with `set_exception` on `_reader_loop` EOF.
10. **Flip dispatch to async**: make `_handle_tool_call` / `tools/call` async; `await` semantic handlers; executor-wrap sync file handlers. **Rewrite the `purity_call` tool description** ([inspector H1 — this is a REVERSAL, not a one-line addition]): the live description (`mcp-purity.py:885-956`) currently declares "purity is NOT for symbol navigation," "purity is the WRONG tool," marks symbol use a "VIOLATION," and routes C/C++/CUDA/Lua to `clangd_call`/`cuda_call`/`luals_call` as "MANDATORY." That ~40-line block must be rewritten so it advertises the new semantic functions WITHOUT self-contradiction — while NOT yet declaring `clangd_call`/`cuda_call` retired (they run in parallel in Phase 0). Thread the needle: "purity_call now ALSO does symbol navigation via `find_definition`/`find_references`/`type_at`; `search_for_pattern` remains free-text search over any filetype; `clangd_call`/`cuda_call` still exist (parallel, Phase 0)."

## Error Handling & Edge Cases

### Error Scenarios
- **No LSP index / empty result (A-class)**: `find_definition`/`find_references`/`symbol`/`symbol_context` fall back to the grep net (trigger = empty OR error OR no index).
- **No LSP index (B-class)**: `type_at`/`diagnostics`/`outline`/`find_implementations` return an explicit honest error, not grep.
- **clangd subprocess crash**: `_reader_loop` EOF fails all pending futures with `RuntimeError` (bug #2), so callers get a prompt error instead of a 15s timeout.
- **CUDA SDK absent**: `cuda` init skip path (`_has_cuda_sources`) — backend simply not spun up; `.cu` semantic calls return an honest "no CUDA backend" error.
- **Project-root mismatch**: keep the clangd-style warning (`mcp-clangd.py:921-935`); since there is no `handle_init` under lazy init, site it in the lazy-init path / `_require_backend` ([inspector M3]). The CUDA path currently lacks this warning entirely (`mcp-cuda.py:1205-1207`).

### Edge Cases
- `find_definition` called with neither `symbol` nor `at` → validation error.
- Header file (`.h`) ambiguity (C vs C++ vs CUDA): `_detect_language` defaults to "cpp" unless `.cu/.cuh`; compile_commands governs actual flags.
- Mixed C + CUDA project: one clangd-family backend whose (CUDA-translated) compile_commands covers both.
- `symbol_change_impact` with no call hierarchy from LSP → return references-based partial result, flagged as partial.
- Concurrent first semantic calls for the same filetype → coalesce into a single in-flight init task; do not spawn two clangd processes ([inspector H2]).
- Repeated calls after an init failure → cache the failure for a backoff window; do not re-spawn a crashing clangd on every call ([inspector H2]).

### Validation
- Parameter shape validation for `symbol` vs `at` routing.
- Identifier regex (`[A-Za-z_][A-Za-z0-9_]*`) gates the grep fallback (matches current clangd behavior).
- File operations stay within `--project-root` (unchanged sandbox).
- **[security P1, OWASP A01 / CWE-22]** Semantic path params (`find_definition at=`, `type_at`/`diagnostics`/`outline` `file=`) MUST resolve within `--project-root` too — the LSP layer must not be coaxed into reading a file outside the sandbox via an absolute or `..` path. Apply the same confinement check the file-IO layer already uses.

### Known Security Limitations
- **[security P2, OWASP A03 / CWE-1333 — ReDoS, LOW]** `search_for_pattern` compiles a user-supplied regex (`re.compile`); a pathological pattern can cause catastrophic backtracking. Pre-existing behavior, not introduced by this change. The async flip (Step 10) runs it in the executor so it no longer freezes the event loop, but it still ties up one worker thread. Full mitigation (regex timeout / complexity guard) is out of scope for Phase 0; documented for awareness.
- **Positive baseline (INFO):** stdlib-only (zero external deps) and `asyncio.create_subprocess_exec` (argv list, never `shell=True`) keep the dependency and shell-injection surface at zero; `json` (not `pickle`/`yaml.load`) keeps all deserialization (MCP frames, LSP JSON-RPC, `compile_commands.json`) safe.

## Testing Strategy

> **Note**: the existing test infrastructure under the repo has not yet been surveyed. Confirm it before implementation; if none exists for the `Scripts/` servers, create a stdlib `unittest` module. Do NOT assume specific test file paths until confirmed.

### Unit Tests
- `_route_filetype` / `_detect_language`: extension → backend / languageId mapping (`.c`, `.cpp`, `.cu`, `.cuh`, `.h`).
- `_resolve_aliases`: last-wins precedence (pins bug #3).
- Function-alias resolution: every legacy `clangd_*` / `cuda_*` name and `glob`/`find_file` reach the right handler.
- `_translate_compile_commands`: nvcc JSON → clang JSON (flag stripping, `-x cuda` injection) — pure function, table-driven.
- `symbol`/`at` parameter routing selects the correct internal path.

### Integration Tests
- MCP-protocol smoke test: feed `initialize` → `tools/list` → `tools/call` JSON-RPC frames on stdin, assert responses on stdout; confirm `tools/list` advertises exactly `purity_call`.
- File-layer no-regression: `read_file`, `search_for_pattern`, `glob`, `list_dir` produce identical output to the current server on a fixture tree.
- Semantic path on a small C fixture with `compile_commands.json`: `find_definition`/`find_references`/`outline`.
- CUDA fixture (only if a CUDA SDK is available in the environment): `find_definition` on a `static __device__` symbol must succeed via the fallback (regression test for bug #1).

### Manual Testing
- [ ] Start the unified server on a real C/C++ project; verify file ops are instant (no init wait) and semantic calls lazily spin up clangd.
- [ ] Kill the clangd subprocess mid-request; verify the caller gets a prompt error, not a 15s hang (bug #2).
- [ ] Verify legacy `clangd_find_definition` / `cuda_find_definition` names still work via `purity_call`.

### Security Testing
- [ ] File operations cannot escape `--project-root`.
- [ ] No new shell/network surface introduced; only the configured `clangd` binary is spawned.

### Edge Case Testing
- [ ] `find_definition` with neither `symbol` nor `at` → validation error.
- [ ] `.cu` semantic call with no CUDA SDK → honest error, no crash.

## Monitoring & Observability

### Logging
- Reuse the `logging` module to stderr. Log backend lazy-init (start, index-ready, prime), fallback activations (which tier resolved a symbol), and clangd-crash detection.

### Metrics/Telemetry
- N/A (local developer tool). Optionally log fallback-vs-LSP resolution counts at debug level for diagnosing index gaps.

### Alerts
- N/A.

### Debugging
- Debug-level log line on every fallback tier transition (workspace_symbol → document_symbol → grep) to make index gaps visible.

## Documentation Updates Required

### Code Documentation
- [ ] Docstrings for the abstract backend interface and the 10 new semantic handlers.
- [ ] Comment the `symbol`/`at` routing and the backend-map/lazy-init logic.

### External Documentation
- [ ] Update the `purity_call` tool **description** (in `mcp-purity.py`) to document the semantic functions + the `search_for_pattern` steering sentence.
- [ ] (Phase 2, out of scope here) `p:mcp-purity` skill, `p:mcp-clangd` / `p:mcp-cuda` skills, minion tool-lists.

### New Documentation
- [ ] None required for Phase 0 beyond the tool description.

## Dependencies & Sequencing
- Steps 1→2→3 are sequential (core before abstraction before CUDA-config).
- Step 4 is implied by step 3 (single handler).
- Steps 5, 6 depend on 1-2; step 7 depends on 2 + 6; step 8 depends on 7; step 9 is independent (can be done with step 1); step 10 last (touches the dispatch path everything else registers into).

## Potential Challenges
- **sync/async illesztés**: ensure long file-layer operations (`search_for_pattern`, `os.walk`) run in the executor so they never freeze the event loop while an LSP request is in flight.
- **clangd-crash handling**: the future-drain must be correct (no double-pop, no set-exception on done futures).
- **File size**: ~3000 lines in one file; keep clear section banners (FILE LAYER / LSP CORE / CUDA CONFIG / DISPATCH) to stay navigable. (Single-file decision is intentional — matches project convention.)
- **CUDA test coverage** depends on SDK availability in the environment; gate CUDA integration tests behind an SDK presence check.
- **Shared clangd index during Phase 0 parallel running** ([inspector]): the standalone `mcp-clangd`/`mcp-cuda` servers and the in-purity clangd both target the same project and may both write `.cache/clangd/index/`. Two clangd processes on one project can contend on the index. Verify clangd's index locking tolerates this, or point the in-purity backend at a distinct index dir during the Phase 0 parallel window.

## Critical Files for Implementation
- `Scripts/mcp-purity.py` — the unified target; gains the LSP core, CUDA config, backend map, semantic handlers, async dispatch, alias extensions, and the tool-description update.
- `Scripts/mcp-clangd.py` — source of the common LSP core and the 3-tier fallback (reference, not modified in Phase 0).
- `Scripts/mcp-cuda.py` — source of the CUDA language-config and the truncated-fallback bug reference (reference, not modified in Phase 0).

## Post-Implementation Checklist
- [ ] All outputs are in English
- [ ] Final import block verified stdlib-only; `# dependencies = []` accurate
- [ ] File-IO / search functions produce identical output to the pre-change server (no regression)
- [ ] All 10 semantic functions reachable via canonical names + legacy aliases
- [ ] `glob` canonical, `find_file` alias both work
- [ ] CUDA `static`/`__device__` symbol lookup succeeds via fallback (bug #1)
- [ ] clangd-crash yields prompt error, not timeout (bug #2)
- [ ] `_resolve_aliases` last-wins, unit-tested (bug #3)
- [ ] File layer answers with no LSP init; LSP lazily spins up
- [ ] `tools/list` advertises exactly `purity_call`; description documents semantic functions + steering sentence
- [ ] Unit + integration tests passing
- [ ] Manual tests (instant file ops, crash handling, legacy aliases) verified
- [ ] Security: sandbox confinement intact; no new shell/network surface
- [ ] `clangd_call` / `cuda_call` still function in parallel (legacy, Phase 0)
