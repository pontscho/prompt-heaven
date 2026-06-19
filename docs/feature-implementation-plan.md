# Implementation Plan: Phase 1 — luals as a Second LspBackend in mcp-purity

## Requirements Summary

Fold the `luals` (lua-language-server) semantic core from `Scripts/mcp-lua-lsp.py` into
`Scripts/mcp-purity.py` as a **second `LspBackend`**, sitting beside the Phase 0
clangd-family backend behind the existing abstract interface. After this phase, a
`purity_call` invocation that targets a `.lua` file (or uses a `luals_*` function name)
is served by an in-process lua-language-server backend, while the standalone
`mcp-lua-lsp.py` server stays registered and functional (exactly as `clangd_call` /
`cuda_call` survived Phase 2).

This is Phase 1 of the 3-phase `mcp-purity` consolidation roadmap. Phase 0 (clangd+cuda
fold) and Phase 2 (skills/agents convention redirect) are already complete and committed
(`4e19a5c`, `9f77b95`, `ab8b441`). The mission is **eliminating duplicated LSP-core code**,
so this phase extracts a shared base class rather than copy-pasting a third near-identical client.

> **Validation notes.**
> - *Iter 1 (inspector REVISE → addressed):* the first draft under-counted what Phase 0
>   ALREADY placed in `mcp-purity.py`. The semantic logic there lives in **module-level
>   helper functions that take a `client` argument** (`_infer_type@1752`,
>   `_def_by_name`/`_refs_by_name@2178/2222`, `_fallback_workspace_symbols@1837`,
>   `_symbol_to_location@1881`, `_collect_call_hierarchy@1980`, `_find_files_with_word@1800`)
>   — NOT polymorphic client methods. Design re-based: **per-language divergence becomes
>   polymorphic hooks/attributes on the backend, and the module-level helpers are edited to
>   consult those hooks.**
> - *Iter 2 (inspector REVISE → addressed):* widened `supplemental_references` to carry
>   `seen`+`remaining` with `uri:line` cross-source dedup (C-A); enumerated the shared LSP
>   wrappers and explicitly RETAINED call-hierarchy in `ClangdClient` (H-A/M-B); made
>   `_prime_index` consult `fallback_extensions` (M-C); documented skip-dir drift (M-D);
>   marked the grep supplement name-based-only (M-A); `_init_backend` gains an `if/elif`
>   (L-A); `LuaLsClient.type_definition` explicitly NOT folded (L-B).

### Success Criteria
- [ ] `purity_call(function="luals_find_definition", ...)` and the other `luals_*` aliases resolve and return correct results against a live lua-language-server.
- [ ] A canonical handler called on a `.lua` path (`find_definition`, `find_references`, `outline`, `diagnostics`, ...) routes to the luals backend via `_detect_language` → `_route_filetype` → `"luals"`.
- [ ] Path-less luals calls (`luals_workspace_symbols`; name-based `luals_find_definition`/`find_references`/`symbol_context`/`symbol_change_impact`) reach the luals backend via the dispatcher backend-hint, not the `"cpp"` default.
- [ ] **Name-based** `find_references` on Lua includes the dynamic-dispatch text-grep supplement, deduped against LSP hits on `uri:line` and capped (parity with the standalone luals server). **Positional (`_at`)** Lua references are LSP-only — the standalone has no positional supplement either, so this is parity. (inspector M-A)
- [ ] `symbol` on Lua uses a `.lua` documentSymbol fallback (parity with standalone luals).
- [ ] `symbol_change_impact` on Lua returns definition + references **without** a `call_hierarchy` (`partial: true`) and does NOT error.
- [ ] `type_at` on Lua returns hover-derived text (degraded, no C++-style clean type string) and does NOT error.
- [ ] The standalone `mcp-lua-lsp.py` server is byte-for-byte unchanged and still launches (`luals_call` remains registered and working).
- [ ] C/C++/CUDA behavior is regression-free: the Phase 0 live clangd test (`find_definition`/`find_references`/`outline`/`type_at`) still passes 4/4.
- [ ] `python3 Scripts/_mcp_smoke_test.py Scripts/mcp-purity.py` passes, including the new luals dispatch assertions.
- [ ] `python3 -m py_compile Scripts/mcp-purity.py Scripts/_mcp_smoke_test.py` is clean.

### Scope

**In Scope:**
- Extract a concrete shared base class `BaseLspClient(LspBackend)` in `mcp-purity.py` holding the byte-identical LSP machinery currently in `ClangdClient`.
- Re-base `ClangdClient` onto `BaseLspClient` (keep `start()`, CUDA config, AND the three call-hierarchy methods).
- Add `LuaLsClient(BaseLspClient)` folding the reusable luals core from `mcp-lua-lsp.py`.
- **Polymorphic divergence layer on the backend** (consulted by existing module-level helpers): `supports_call_hierarchy: bool`, `fallback_extensions: tuple`, `language_id`/`_language_id(path)`, `infer_type(text) -> str`, `async supplemental_references(symbol_name, seen, remaining, preferred_path=None) -> list`.
- Edit the module-level semantic helpers + handlers to consult those hooks: `handle_type_at` (call `client.infer_type`), `_refs_by_name` (merge `client.supplemental_references` with `uri:line` dedup + `remaining` cap), `_fallback_workspace_symbols` + `_symbol_to_location` (use `client.fallback_extensions`), `handle_symbol_change_impact` (guard call-hierarchy on `client.supports_call_hierarchy`), `handle_symbol` (drop hard-coded `"cpp"`).
- File-type routing for `.lua`: `_detect_language`, `_LUALS_FILETYPES`, `_route_filetype`.
- Lazy-init: introduce an `if/elif backend_type` split in `_init_backend`; widen `_backends` + helper/`_ensure_backend`/`_init_backend`/`_require_backend` annotations to `LspBackend`.
- Dispatcher backend-hint: derive a hint from the `luals_` function-name prefix, inject a reserved `params["_backend"]`, add a `_select_filetype(params, abs_path)` helper used by the path-less-capable handlers; keep the unknown-params validation clean.
- Register `luals_*` legacy aliases in `HANDLERS` (both bare and `_at` variants, mirroring `clangd_*`).
- Update `PURITY_CALL_TOOL` description to advertise luals.
- Smoke-test extension in `_mcp_smoke_test.py` (hermetic, JSON-RPC-observable only).

**Out of Scope:**
- De-registering the standalone `clangd_call` / `cuda_call` / `luals_call` tools (a later roadmap phase).
- A canonical `find_type_definition` handler / `luals_find_type_definition_at` alias. luals has a `type_definition` LSP wrapper (`mcp-lua-lsp.py:522-529`) and a `textDocument/typeDefinition` op, but there is NO canonical handler for it and mapping one is non-trivial. **`LuaLsClient.type_definition` is explicitly NOT folded** — do not copy it in. (inspector M3, L-B)
- Adding call-hierarchy support to Lua (lua-language-server does not provide it).
- A position→symbol-name resolver to give positional `find_references` the Lua grep supplement (none exists; out of scope, see M-A).
- Refactoring or deleting `mcp-lua-lsp.py`.
- An upward `.luarc.json` discovery walk (rely on luals' own cwd discovery).

### Assumptions & Constraints

**Assumptions:**
- The standalone `mcp-lua-lsp.py` server remains registered and runs unchanged; only its reusable core logic is *copied/folded* (not imported) into `mcp-purity.py`, mirroring the Phase 0 clangd/cuda copy.
- lua-language-server auto-discovers `.luarc.json` relative to its `cwd` (= `project_root`); no `--configpath` is passed by default. Matches the standalone server's default → no behavior regression for Lua users.
- The `luals_*` legacy aliases are added to `mcp-purity.py`'s `HANDLERS` so `purity_call(function="luals_*")` works in-process, continuing the Phase 0/2 pattern.
- Degraded Lua results (`symbol_change_impact` without call hierarchy, `type_at` raw hover) equal what the standalone luals server returns.
- Per-language divergence is small enough to express as a handful of polymorphic hooks/attributes on the backend (not a capability registry).
- Minor file-walk skip-dir drift (purity `_FALLBACK_SKIP_DIRS` vs the standalone luals skip set) is acceptable for Phase 1 (see M-D note).

**Constraints:**
- stdlib-only, single-file-per-server fleet convention — no new third-party dependencies.
- The `purity_call` `{function, params}` envelope and the canonical `relative_path` path key ([D3]) must not change. The reserved `_backend` params key is dispatcher-internal and stripped from unknown-params reporting.
- `RuntimeError` stays in the dispatcher clean-catch tuple ([D5]) — honest errors.
- No build system; verification is `_mcp_smoke_test.py` + `py_compile` + a one-shot live `p:minion-runner` run.

### Non-Functional Requirements
- **Performance**: First `.lua` call pays a one-time lua-language-server init + indexing cost (standalone waits up to 60s for indexing, then primes up to 10 files). The unified 90s init shield (`_ensure_backend`, `asyncio.shield` + `wait_for(timeout=90)`) covers it. Warm calls are sub-second. Backends init independently and lazily.
- **Security**: No new external input surface beyond Phase 0; path params confined under `--project-root` via `safe_path`. lua-language-server spawned as a child with `cwd=project_root`, no shell.
- **Scalability**: Two backends coexist keyed by `backend_type`; cost is one extra LSP child process when a `.lua` file is first touched.
- **Compatibility**: C/C++/CUDA paths must be byte-behavior-identical after re-basing `ClangdClient`.

## Architecture Analysis

### Phase 0 backend architecture (target: `Scripts/mcp-purity.py`)

- **Abstract interface** — `LspBackend` at `1259`. Convention-based (NOT an ABC): every method `raise NotImplementedError`. 13 async methods + `project_root`/`process` attrs. Docstring already anticipates "luals for Lua, Phase 1".
- **Concrete backend** — `ClangdClient(LspBackend)` at `1316`; `__init__` `1322-1335`; `start()` `1337`. Implements the three call-hierarchy methods at `1649` (`prepare_call_hierarchy`), `1661` (`call_hierarchy_incoming`), `1665` (`call_hierarchy_outgoing`). The seven LSP wrappers (`workspace_symbol`/`document_symbol`/`definition`/`references`/`implementation`/`hover`/`inlay_hints`) at `1584-1647`.
- **Module-level LSP transport (shared, reuse verbatim)**: `encode_lsp_message`, `read_lsp_message`, `uri_to_path`/`path_to_uri`, `_relative_path`, `extract_surrounding_code`, `_format_location`, `_location_from_payload`, `_flatten_hover` (`1738`).
- **THE SEMANTIC HELPER LAYER (critical — module-level, takes `client`)**:
  - `_infer_type(text) -> str` (`1752`) — C++-tuned. Called by `handle_type_at` at `2402` (`deduced = _infer_type(raw_text)`), where `raw_text = _flatten_hover(contents)` from `client.hover(...)` at `2397`.
  - `_def_by_name(client, ...)` (`2178`) → `_symbol_to_location` + `client.definition`. Called by `handle_find_definition` name-branch at `2325`.
  - `_refs_by_name(client, name, preferred_path, max_results, ctx)` (`2222`) → `client.workspace_symbol(name)`, then per definition-kind symbol `client.references(...)`, accumulating into `all_refs` with a `seen` set keyed **`uri:line:character`** (`2244`). Called by `handle_find_references` name-branch at `2355`. Clean merge point exists after the `for sym` loop.
  - `_refs_at(client, abs_path, line, char, ...)` (`2263`) — positional; no symbol name available.
  - `_fallback_workspace_symbols(client, query, limit)` (`1837`) → `_find_files_with_word(client.project_root, query, limit=20)` (default exts) + `client.document_symbol`. Called by `handle_symbol` at `2492` when LSP `workspace_symbol` is empty.
  - `_symbol_to_location(client, name, preferred_path, max_retries)` (`1881`) — 3-tier; Tier 3 (`1959`) calls `_find_files_with_word(..., limit=10)` (default exts). Preferred-path tiebreaker (`1917-1918`) is already backend-neutral.
  - `_collect_call_hierarchy(client, ...)` (`1980`) → `client.prepare_call_hierarchy` + `_expand_hierarchy_item` (`1999`). Called by `handle_symbol_change_impact` at `2595` inside `if isinstance(definition, list):` (`2583`); handler emits `"partial": not call_hierarchies` (`2614`).
  - `_find_files_with_word(root, word, exts=_FALLBACK_EXTS, limit)` (`1800`) — **`exts` parameterizable**; `_FALLBACK_EXTS` (`1793`) = C/C++/CUDA/ObjC, no `.lua`. `_FALLBACK_SKIP_DIRS` (`1794`) = `{build, vendor, third_party, third-party, node_modules, .git, .cache, .clangd, .ccache, _deps}`.
- **Registry** — `_backends: Dict[str, ClangdClient]` (`2066`), `_backend_init_tasks`, `_backend_init_failed`, `_INIT_FAILURE_BACKOFF=30.0`, `_CLANGD_FILETYPES={"c","cpp","cuda"}` (`2070`).
- **Routing** — `_detect_language` (`936-943`, no `.lua`), `_route_filetype` (`2073-2077`).
- **Lazy init** — `_require_backend` (`2080`), `_init_backend` (`2095-2112`, **unconditional** `ClangdClient()` at `2099` — no `backend_type` branch yet), `_ensure_backend` (`2115-2171`, 90s shield).
- **Handlers** — find_definition `2305`, find_references `2334`, find_implementations `2362`, type_at `2388`, diagnostics `2411`, outline `2440`, symbol `2478` (**hard-codes `"cpp"` at `2487`**), symbol_context `2512`, inlay_hints `2528`, symbol_change_impact `2562`.
- **Dispatcher** — `handle_purity_call` (`2772-2815`): `canonical_func=_canonical_function(function)` (`161`, only `ls/glob/grep/search`); `params=_resolve_aliases(raw_params, canonical_func)` (`165-199`, passes unknown keys through); **`handler=HANDLERS.get(function)` uses the RAW name** (`2792`); uniform `handler(params, project_root, strict)` at `2799`/`2801`; `HANDLER_ACCEPTED_PARAMS` unknown-params check in the except block (`2803-2811`), keyed on `canonical_func`.
- **Aliases** — `clangd_*` (`2661-2674`) and `cuda_*` (`2676-2689`) are DIRECT `HANDLERS` keys; BOTH bare and `_at` for definition/references; find_implementations is `_at`-only; `*_init`→`handle_lsp_init_noop`.
- **Tool** — `PURITY_CALL_TOOL` (`2822`), Lua line `2865`, `tools/list` `2974`.

### luals core (source: `Scripts/mcp-lua-lsp.py`)

- `LuaLsClient` `219-597`; `__init__` `222-233` byte-identical state to `ClangdClient`. The seven LSP wrappers `500-572` are byte-identical to clangd's `1584-1647`. `LuaLsClient` has **NO** call-hierarchy methods (`498-590`).
- `start()` `235-333` — divergent: binary `lua-language-server`, optional `--configpath`, `cwd=project_root`, `initialize` (rootUri, workspaceFolders, `initializationOptions.Lua.{hint,diagnostics,workspace}`, capabilities `281-322`), `initialized`, the `initializationOptions.Lua` push (`264-277`) + a SINGLE `workspace/didChangeConfiguration` re-push (`306-321`) — there is no second `didChangeConfiguration`, indexing wait (`323-328`), `_prime_index` (`335-357`, primes `.lua`, skip set `{build,out,dist,.git,node_modules,vendor}`). `processId=os.getpid()`.
- `_reader_loop` luals extras: answer `workspace/configuration` REQUEST with `[None]*len(items)` (`439-442`); discard `$/status/*` (`445`); `publishDiagnostics` (`428-436`); `$/progress` end → `_indexing_done` (`422-425`).
- `open_document` `492` hard-codes `languageId="lua"`.
- luals-only semantic divergence: `_find_text_references_lua` `1003-1052` (grep, `seen` keyed `uri:line` at `1038`, takes `existing_keys`+`max_remaining`, skip set at `1015` = `{build,out,dist,.git,node_modules,vendor}`); standalone `handle_find_references` `1055` builds `seen` keyed `uri:line` (`1087`) and passes `seen`+`remaining` into the grep (`1131-1133`); `handle_symbol_change_impact` `1324-1357` (no call hierarchy); `handle_hover` `1360-1386` (raw `_flatten_hover`, no `_infer_type`).
- Server-shell (leave behind): `_client`/`_require_client` `603-609`; `McpServer` `1742-1880`; `main` `1887-1933`; `handle_init` `839-874`; `ALL_HANDLERS`/`FUNCTION_ALIASES` `1425-1447`; `_md_*` `1507-1694`.

## Captured Information (for implementation phase)

### Step 0 — Helper inventory (do this FIRST)
| Helper @line | Lua divergence | Decision |
|---|---|---|
| `_infer_type@1752` | luals hover != C++ | Keep module-level; route via `client.infer_type`. Edit call site `2402`. |
| `_refs_by_name@2222` | luals grep supplement | Keep; merge `await client.supplemental_references(name, line_seen, remaining, preferred_path)` after the `for sym` loop, dedup on `uri:line`. Base returns `[]`. |
| `_refs_at@2263` | none (no name) | LSP-only for Lua (no position→name resolver; standalone has no positional supplement). |
| `_fallback_workspace_symbols@1837` | `.lua` exts | Keep; pass `exts=client.fallback_extensions`. |
| `_symbol_to_location@1881` Tier 3 (`1959`) | `.lua` exts | Keep; pass `exts=client.fallback_extensions`. Tiebreaker `1917-1918` already neutral. |
| `_collect_call_hierarchy@1980` | luals lacks CH | Keep; guard the HANDLER call at `2583` on `client.supports_call_hierarchy`. |
| `_find_files_with_word@1800` | `.lua` exts | Reuse via its `exts` kwarg. Do NOT fold a second same-named copy. Skip-dir drift accepted (M-D). |
| `_def_by_name@2178` / `_def_at@2202` | none | Reuse unchanged. |

### Backend divergence layer (new — on `BaseLspClient`, overridden by `LuaLsClient`)
```python
class BaseLspClient(LspBackend):
    supports_call_hierarchy: bool = True
    fallback_extensions: tuple = _FALLBACK_EXTS          # 12-ext grep set (C/C++/CUDA/ObjC)
    prime_extensions: tuple = (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".cu", ".cuh")  # clangd's EXISTING 8-ext prime set (NOT fallback_extensions)
    def _language_id(self, path: str) -> str:            # for open_document didOpen
        return _detect_language(path)
    def infer_type(self, text: str) -> str:
        return _infer_type(text)                         # C++ heuristic default
    async def supplemental_references(self, symbol_name: str, seen: set,
                                      remaining: int,
                                      preferred_path: Optional[str] = None) -> List[dict]:
        return []                                        # no-op default (clangd)
    # supports_call_hierarchy is consulted at the HANDLER (handle_symbol_change_impact), not here

class LuaLsClient(BaseLspClient):
    supports_call_hierarchy = False
    fallback_extensions = (".lua",)
    prime_extensions = (".lua",)
    def _language_id(self, path: str) -> str:
        return "lua"
    def infer_type(self, text: str) -> str:
        return text.strip()                              # raw hover (parity w/ standalone luals)
    async def supplemental_references(self, symbol_name, seen, remaining, preferred_path=None):
        # `seen` is the caller's uri:line set; returns <= `remaining` text hits not already on a seen line
        return await self._lua_text_references(symbol_name, seen, remaining)   # folded _find_text_references_lua
```
Note: luals `infer_type` returns the already-flattened hover text (degraded, documented). A light Lua-aware extractor MAY be added later; not required for Phase 1.

### Module-level helper edits (consult the hooks)
- `handle_type_at` `2402`: `deduced = _infer_type(raw_text)` → `deduced = client.infer_type(raw_text)`.
- `_refs_by_name` (after the `for sym` loop, before `return`): the per-ref `seen` is keyed `uri:line:character` (`2244`); build a **2-part** `line_seen = {f"{uri}:{line}"}` from the LSP refs already in `all_refs`, compute `remaining = max_results - len(all_refs)` (when `max_results > 0`; else pass `remaining=0` = uncapped, matching the standalone's `max_remaining > 0` gate), then `extra = await client.supplemental_references(symbol_name, line_seen, remaining, preferred_path)` and append each text hit whose `uri:line` is NOT in `line_seen`. This matches the standalone dedup/cap (`mcp-lua-lsp.py:1087,1131-1133`). Base returns `[]` → clangd unaffected. **Name-based only**; `_refs_at` (positional) stays LSP-only (M-A).
- `_fallback_workspace_symbols` `1843`: `_find_files_with_word(client.project_root, query, exts=client.fallback_extensions, limit=20)`.
- `_symbol_to_location` Tier 3 `1959`: `_find_files_with_word(client.project_root, symbol_name, exts=client.fallback_extensions, limit=10)`.
- `handle_symbol_change_impact` `2583`: `if isinstance(definition, list):` → `if isinstance(definition, list) and client.supports_call_hierarchy:` (the `"partial": not call_hierarchies` at `2614` handles the empty case). (inspector H3)
- `handle_symbol` `2487`: drop hard-coded `"cpp"`; use `_select_filetype(params, abs_path)`.

### Dispatcher backend-hint (inspector H1 — no handler-signature change)
```python
_PREFIX_BACKEND = {"luals_": "lua"}        # clangd_/cuda_ rely on path detection; no hint needed
def _backend_hint(function: str) -> Optional[str]:
    for prefix, ft in _PREFIX_BACKEND.items():
        if function.startswith(prefix):
            return ft
    return None
def _select_filetype(params: dict, abs_path: str) -> str:
    return params.get("_backend") or (_detect_language(abs_path) if abs_path else "cpp")
```
- In `handle_purity_call`, after `_resolve_aliases` and before the `handler(...)` call: `hint = _backend_hint(function); if hint: params["_backend"] = hint`.
- Path-less-capable handlers (`handle_symbol`, the name-branches of `handle_find_definition`/`find_references`/`symbol_context`/`symbol_change_impact`) acquire the backend via `await _ensure_backend(_select_filetype(params, abs_path), project_root)` — for `handle_symbol`, which has no path variable, pass `abs_path=""` (→ `params.get("_backend") or "cpp"`). Positional handlers may use it harmlessly.
- Unknown-params validation (`2803-2811`): `set(params.keys()) - accepted - {"_backend"}`.
- Path-based luals calls need NO hint: `_detect_language(".lua")→"lua"` routes once `_route_filetype` knows `"lua"`.

### `luals_*` alias keys (mirror clangd; both bare + `_at`; drop type_definition) (inspector M1, M3)
```python
"luals_find_definition": handle_find_definition,
"luals_find_definition_at": handle_find_definition,
"luals_find_references": handle_find_references,
"luals_find_references_at": handle_find_references,
"luals_find_implementations_at": handle_find_implementations,
"luals_workspace_symbols": handle_symbol,
"luals_document_outline": handle_outline,
"luals_symbol_context": handle_symbol_context,
"luals_symbol_change_impact": handle_symbol_change_impact,
"luals_inlay_hints": handle_inlay_hints,
"luals_hover": handle_type_at,
"luals_diagnostics": handle_diagnostics,
"luals_init": handle_lsp_init_noop,
# luals_find_type_definition_at: OUT OF SCOPE (no canonical handler)
```

### Annotation widening (inspector L1) — `ClangdClient` → `LspBackend`
`_backends@2066`, `_require_backend@2080`, `_init_backend@2095`, `_ensure_backend@2115`, and `client: ClangdClient` params on `_fallback_workspace_symbols@1837`, `_symbol_to_location@1881`, `_collect_call_hierarchy@1980`, `_expand_hierarchy_item@1999`, `_def_by_name@2178`, `_def_at@2202`, `_refs_by_name@2222`, `_refs_at@2263`. Runtime-neutral.

### luals `_reader_loop` divergences to preserve (`mcp-lua-lsp.py:428-445`)
- `publishDiagnostics` → capture into `self._diagnostics[uri]`, set `_diag_events[uri]`.
- `workspace/configuration` server→client REQUEST → reply `[None]*len(items)`.
- `$/status/report|refresh|click` → discard.
- `$/progress` end → set `_indexing_done`.
Implement via base `_reader_loop` calling protected hooks `_handle_server_request(msg)` (the slot is the `else: "Unhandled notification"` at `mcp-purity.py:1534-1535`; the existing `window/workDoneProgress/create` handling at `1507-1511` proves the request-reply pattern) and `_handle_unknown_notification(msg)` (default ignore), overridden by `LuaLsClient`. Fallback (acceptable): `LuaLsClient` overrides `_reader_loop` wholesale. (inspector I-A)

### Error Handling Pattern (existing, follow it)
- Handlers return `{"error": "..."}`; `_ensure_backend` raises `ValueError` for unroutable filetype and records init failures (30s backoff); `RuntimeError` in the dispatcher clean-catch tuple. luals `initialize` failure raises `RuntimeError`. Add a `shutil.which("lua-language-server")` guard in the luals init branch for an honest missing-binary error.

### Test Pattern (`_mcp_smoke_test.py`) — JSON-RPC-observable ONLY (inspector H2)
The harness drives the server as a subprocess over JSON-RPC and inspects text responses; it does NOT import the module (`_mcp_smoke_test.py:56-138,149-201`). Committed smoke asserts ONLY:
- the empty-`function` inventory response lists the `luals_*` names;
- `luals_find_definition` (+ representative subset) returns a NON-"unknown function" response (dispatch resolves — a backend/init error is acceptable and distinguishable);
- `luals_bogus` returns the "unknown function" error.
Introspection asserts (`_detect_language`, `_route_filetype`, `supports_call_hierarchy`, `fallback_extensions`) move to the live `p:minion-runner` step (which can `python3 -c "import..."`). No new committed in-process test file.

### Constants and Configuration
- Reused: `_INIT_FAILURE_BACKOFF=30.0`, shield `timeout=90.0`, luals `initialize` `timeout=30.0`, luals indexing wait `timeout=60.0`.
- New: `_LUALS_FILETYPES={"lua"}`, `_PREFIX_BACKEND={"luals_":"lua"}`, luals binary default `"lua-language-server"`, `LuaLsClient.fallback_extensions=(".lua",)`.

### Skip-dir parity note (inspector M-D)
Reusing `_find_files_with_word` / a `fallback_extensions`-driven `_prime_index` inherits `mcp-purity.py`'s `_FALLBACK_SKIP_DIRS` (`1794`), which differs from the standalone luals skip set (`mcp-lua-lsp.py:1015,342` = `{build,out,dist,.git,node_modules,vendor}`): purity adds `third_party`/`.cache`/`.clangd`/`.ccache`/`_deps` and lacks `out`/`dist`. **Accepted minor drift** for Phase 1 (affects which dirs the `.lua` grep/prime walks, not correctness). A `fallback_skip_dirs` attribute could unify it later if a real Lua project trips on `out`/`dist`.

### Resource Ownership Rules
- Each backend owns its child process + reader task; created lazily, stored in `_backends[backend_type]`, stopped via inherited `stop()`. luals and clangd never share a process. Folded core is *copied*, not imported.

## Alternative Approaches Evaluated

### Option 1: Separate `LuaLsClient(LspBackend)`, no shared base
**Pros:** lowest risk; does not touch committed `ClangdClient`. **Cons:** duplicates ~90% byte-identical machinery; contradicts the de-dup mission.

### Option 2: Extract shared `BaseLspClient(LspBackend)` — CHOSEN
**Pros:** realizes the de-dup mission; divergent surface is small. **Cons:** re-bases committed `ClangdClient` → regression surface; mitigated by the Phase 0 live clangd test + smoke.

### Option 3: Central capability registry + explicit "not supported" errors
**Pros:** explicit capability declaration. **Cons:** more machinery; returns errors where the standalone luals returns degraded-but-useful results.

### Recommended Approach: Option 2 + backend divergence hooks consulted by module-level helpers (Q2) + cwd-only config (Q3) + prefix backend-hint via reserved `_backend` (Q5)
**Rationale:** Option 2 is the only structure consistent with the mission. Because the 10 handlers delegate to module-level helpers that take `client`, divergence is expressed as a small set of polymorphic hooks/attributes those helpers consult — keeping handlers/helpers backend-agnostic (Phase 0 invariant) and matching standalone luals degraded behavior. cwd-only config matches standalone exactly. The reserved `_backend` hint is a single, signature-preserving mechanism that fixes path-less routing and the `handle_symbol` hardcode.

## Implementation Strategy

Class hierarchy after this phase:
```
LspBackend (abstract, NotImplementedError stubs)        # unchanged, 1259
   └── BaseLspClient (shared machinery + divergence hooks)  # NEW
         ├── ClangdClient  (start + CUDA + RETAINS call-hierarchy; inherits C++ hook defaults)
         └── LuaLsClient   (start + lua _language_id + reader hooks + luals divergence hooks; NO call-hierarchy)
```

### Data Model / API Changes
- `_backends: Dict[str, ClangdClient]` → `Dict[str, LspBackend]` (+ helper annotations, L1).
- New `_LUALS_FILETYPES`, `_PREFIX_BACKEND`; new `"lua"` languageId, `"luals"` backend_type.
- New reserved `_backend` params key (dispatcher-internal).
- New `luals_*` `HANDLERS` keys. No `purity_call` wire-envelope change.

### Backwards Compatibility & Migration
- C/C++/CUDA must be behavior-identical post re-base; verified by the Phase 0 live clangd test (4/4) + smoke.
- Standalone `mcp-lua-lsp.py`: untouched, still registered. Phase 0 plan preserved at `docs/adr-new-purity-server.md`.
- In-process only; no data migration.

### New Dependencies
- None (stdlib-only). lua-language-server is an existing external runtime; spawned the same way as the standalone server. Smoke stays hermetic.

### Configuration Changes
- None required. Optional `--configpath` supported by `LuaLsClient.start(config_path=...)` but not passed by default.

## Step-by-Step Implementation Plan

1. **Helper inventory (Step 0 above).** Confirm each module-level helper's per-language decision against the live code before writing any class.
2. **Extract `BaseLspClient(LspBackend)`** before `ClangdClient` (`1316`). MOVE to the base the byte-identical machinery (verified clangd `1584-1647` == luals `500-572`): `__init__` state; `_send`/`_request`/`_notify`; `_reader_loop` (refactored to call `_handle_server_request`/`_handle_unknown_notification` no-op hooks); the **seven LSP wrappers** `workspace_symbol`/`document_symbol`/`definition`/`references`/`implementation`/`hover`/`inlay_hints`; `get_diagnostics`; `open_document` (use `self._language_id(path)`); `stop`; `_abs_uri`; `_abs_path`. Make `_prime_index` a base method that globs `self.prime_extensions` (NOT `fallback_extensions` — clangd's prime set is 8 exts, distinct from the 12-ext grep set, so reusing the grep set would silently make clangd prime `.m/.mm/.hh/.hxx`) and keep clangd's existing prime skip-check `{build,out,dist,.git}` over `Path(root).parts` (a THIRD skip set, distinct from `_FALLBACK_SKIP_DIRS` and the grep skip set) so each subclass primes its own file type WITHOUT changing clangd's current prime walk (NOT byte-shared: clangd `1430-1451` primes C/C++, luals `335-357` primes `.lua`; inspector M-C/M2/H1). **RETAIN in `ClangdClient`** the three call-hierarchy methods `prepare_call_hierarchy`/`call_hierarchy_incoming`/`call_hierarchy_outgoing` (`1649/1661/1665`) — `LuaLsClient` deliberately lacks them; `supports_call_hierarchy=False` guards the sole caller (`2583`). Add the divergence layer (`supports_call_hierarchy`, `fallback_extensions`, `_language_id`, `infer_type`, `supplemental_references`). (inspector H-A/M-B/M-C)
3. **Re-base `ClangdClient(BaseLspClient)`** (`1316`): delete now-inherited methods (the seven wrappers, transport, reader-loop, etc.); keep `start()` (+CUDA) and the three call-hierarchy methods. It inherits the C++ hook defaults unchanged.
4. **Add `LuaLsClient(BaseLspClient)`** after `ClangdClient`: fold `start()` (binary, `--configpath`, the `initializationOptions.Lua` push (`264-277`) + the single `workspace/didChangeConfiguration` re-push (`306-321`), indexing wait + prime). Override `_language_id`→`"lua"`, `_handle_server_request` (answer `workspace/configuration`), `_handle_unknown_notification` (drop `$/status/*`), `supports_call_hierarchy=False`, `fallback_extensions=(".lua",)`, `infer_type` (raw), `supplemental_references` (fold `_find_text_references_lua` as `_lua_text_references`, accepting `seen`+`remaining`). Do NOT fold `type_definition` (L-B).
5. **Edit module-level helpers/handlers to consult hooks** (Step 0 list): `handle_type_at:2402`, `_refs_by_name` (uri:line dedup + remaining), `_fallback_workspace_symbols:1843`, `_symbol_to_location:1959`, `handle_symbol_change_impact:2583`, `handle_symbol:2487`.
6. **`_detect_language` `.lua` branch** (`936-943`): `if suffix == ".lua": return "lua"` before the cpp fallthrough.
7. **Routing**: add `_LUALS_FILETYPES={"lua"}` after `_CLANGD_FILETYPES` (`2070`); extend `_route_filetype` (`2073-2077`) with `if filetype in _LUALS_FILETYPES: return "luals"`.
8. **Lazy init**: widen `_backends` + helper annotations (L1); in `_init_backend` (`2095-2112`, currently UNCONDITIONAL `ClangdClient()` at `2099`) introduce an `if/elif backend_type` split — `if backend_type == "clangd": <existing clangd+CUDA path>` / `elif backend_type == "luals":` (with `shutil.which("lua-language-server")` guard) `client = LuaLsClient(); await client.start(project_root); return client`. (inspector L-A)
9. **Dispatcher hint**: add `_PREFIX_BACKEND`, `_backend_hint`, `_select_filetype`; inject `params["_backend"]` in `handle_purity_call` before the handler call; switch the path-less-capable handlers to `_select_filetype`; exclude `_backend` from the unknown-params check (`2803-2811`).
10. **Aliases**: add the `luals_*` keys after the `cuda_*` block (`2689`).
11. **Tool description**: update `PURITY_CALL_TOOL` (`2865`) to advertise in-process luals.
12. **Smoke**: extend `_mcp_smoke_test.py` with the JSON-RPC-observable assertions; ensure `py_compile` clean.

## Error Handling & Edge Cases

### Error Scenarios
- **lua-language-server binary missing** → `shutil.which` guard → honest error + 30s backoff (not a hang).
- **`initialize` error** → `RuntimeError` in `start()`; shield surfaces it.
- **`workspace/configuration` unanswered** → luals stalls → 90s timeout. The reader-loop hook MUST answer it (step 4).
- **`.lua` query before indexing complete** → `_prime_index` + indexing wait in `start()`.

### Edge Cases
- **Bare canonical `symbol` (no path, no luals prefix)** → `_select_filetype` returns `"cpp"` (no regression).
- **`luals_workspace_symbols` (no path)** → `_backend`="lua" → luals; fallback uses `(".lua",)`.
- **Positional `find_references` on Lua** → LSP-only (no grep supplement; M-A).
- **`symbol_change_impact` on Lua** → no `call_hierarchy`, `partial: true`.
- **`type_at` on Lua** → raw hover text (documented degradation).
- **Mixed `.lua` + C/C++ project** → two independent lazy backends.

### Validation
- Path params confined under `--project-root` via `safe_path`. `_route_filetype` returning `None` still raises `ValueError`. The reserved `_backend` key is dispatcher-internal, never trusted for paths.

## Testing Strategy

### Smoke (hermetic, committed — `_mcp_smoke_test.py`, JSON-RPC-observable only)
- Inventory lists the `luals_*` names.
- `luals_find_definition` (+ subset) dispatches (NOT "unknown function").
- `luals_bogus` → "unknown function".

### Live verification (separate, NOT committed — `p:minion-runner` during /p:implement)
- In-process import asserts: `_detect_language(".lua")=="lua"`, `_route_filetype("lua")=="luals"`, `LuaLsClient.supports_call_hierarchy is False`, `ClangdClient.supports_call_hierarchy is True`, `LuaLsClient.fallback_extensions==(".lua",)`.
- Spawn a real lua-language-server on a fixture `.lua` workspace; run `find_definition`, name-based `find_references` (incl. grep supplement + dedup), `outline`, `diagnostics`, `hover`/`type_at`, `symbol` (incl. `.lua` fallback), graceful `symbol_change_impact`. Artifacts under `.claude/tmp/`.
- Re-run the Phase 0 live clangd test (C/C++) to prove the `ClangdClient` re-base is regression-free.

### Manual
- [ ] `python3 -m py_compile Scripts/mcp-purity.py Scripts/_mcp_smoke_test.py`
- [ ] `python3 Scripts/_mcp_smoke_test.py Scripts/mcp-purity.py`
- [ ] In-session `purity_call` on a `.lua` file after the rebuilt server is loaded.

### Security
- [ ] luals child spawned without a shell, `cwd=project_root`, paths confined by `safe_path`.

### Edge Case
- [ ] `symbol_change_impact` on Lua omits `call_hierarchy`, returns a result (not an error).
- [ ] Missing `lua-language-server` binary → honest error + backoff, not a hang.
- [ ] Name-based Lua `find_references` does not emit duplicates across LSP + grep (uri:line dedup).

## Monitoring & Observability
- **Logging**: reuse `log.debug("Backend '%s' init: %s", ...)` in `_init_backend`; luals `start()` logs init/indexing milestones.
- **Debugging**: `--debug`/`--log-file` wire logging (F12 open policy item) applies to luals too — noted, not resolved here.

## Documentation Updates Required

### Code Documentation
- [ ] Docstrings on `BaseLspClient`, `LuaLsClient`, each divergence hook, the reader-loop hooks, `_select_filetype`/`_backend_hint`.
- [ ] Inline notes where luals degrades (`symbol_change_impact`, `type_at`, positional refs).

### External Documentation
- [ ] `PURITY_CALL_TOOL` description.
- [ ] Update the project-memory roadmap (`mcp-purity-consolidation-roadmap.md`) to mark Phase 1 after implementation (not part of this plan's writes).

## Dependencies & Sequencing
- Steps 1→2→3→4 sequential (inventory → base → re-base clangd → luals).
- Step 5 depends on the hooks (steps 2, 4).
- Steps 6,7,8 depend on `LuaLsClient` (step 4).
- Step 9 depends on `_select_filetype` + handlers (step 5).
- Step 10 depends on routing (6,7) + step 9. Step 11 independent. Step 12 last.

## Potential Challenges
- **Re-basing the committed `ClangdClient`** — keep the diff mechanical; prove parity via the live clangd test. If the `_reader_loop` hook refactor is risky, `LuaLsClient` may override `_reader_loop` wholesale.
- **luals init handshake fidelity** — the `workspace/configuration` reply + BOTH config pushes (`initializationOptions.Lua` at `264-277` AND the single `workspace/didChangeConfiguration` re-push at `306-321`) are silent-hang traps if dropped; captured in step 4.
- **Cross-source reference dedup** — the Lua grep supplement MUST dedup on `uri:line` against LSP hits and respect `remaining`, or it emits duplicates (root cause of inspector C-A).
- **`type_at` degradation** — documented non-parity boundary, not a bug.

## Critical Files for Implementation
- `Scripts/mcp-purity.py` — all structural work: `BaseLspClient` extraction, `ClangdClient` re-base, `LuaLsClient` fold, divergence hooks, module-helper edits, `.lua` routing, lazy-init branch, dispatcher hint, `luals_*` aliases, annotation widening, tool description.
- `Scripts/_mcp_smoke_test.py` — hermetic JSON-RPC luals dispatch assertions.
- `Scripts/mcp-lua-lsp.py` — fold SOURCE (reference only, NOT modified).
- `Scripts/mcp-clangd.py` — comparison reference (NOT modified).

## Post-Implementation Checklist
- [ ] All outputs in English
- [ ] `py_compile` clean for `mcp-purity.py` + `_mcp_smoke_test.py`
- [ ] Smoke test passes (incl. new luals JSON-RPC assertions)
- [ ] Live luals verification passes (p:minion-runner, artifacts in `.claude/tmp/`), incl. the in-process routing/flag asserts
- [ ] Phase 0 live clangd test re-run passes 4/4 (no C/C++ regression)
- [ ] Standalone `mcp-lua-lsp.py` unchanged and still launches
- [ ] Name-based `find_references` Lua grep supplement (uri:line dedup, capped) + `symbol` `.lua` fallback verified; positional refs LSP-only by design
- [ ] `symbol_change_impact`/`type_at` Lua degradation documented
- [ ] Call-hierarchy methods RETAINED in `ClangdClient`; `LuaLsClient` lacks them; guard at `2583` verified
- [ ] luals init handshake (`workspace/configuration` reply, double `didChangeConfiguration`) verified against a live server
- [ ] `_backend` reserved key excluded from unknown-params reporting
- [ ] No new dependencies introduced
- [ ] Security review completed (plan-mode after inspector-plan APPROVE)
