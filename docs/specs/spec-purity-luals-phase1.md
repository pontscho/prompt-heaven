---
name: spec-purity-luals-phase1
type: spec
status: active
title: 'Phase 1: luals as the second LSP backend behind purity_call'
description: How lua-language-server was folded in behind the unchanged purity_call entry point via a BaseLspClient extraction and a per-language divergence layer, plus the security-hardening pass that shipped with it.
sources:
  - Scripts/mcp-purity.py
  - Scripts/mcp-purity.py:BaseLspClient
  - Scripts/mcp-purity.py:LuaLsClient
  - Scripts/mcp-purity.py:_lua_text_references
  - Scripts/mcp-purity.py:_select_filetype
  - Scripts/mcp-purity.py:_lsp_path_in_root
  - Scripts/mcp-purity.py:_resolve_lsp_binary
  - Scripts/mcp-purity.py:_sanitize_log
  - Scripts/mcp-purity.py:CLANGD_EXEC_DENYLIST_EXACT
  - Scripts/mcp-purity.py:HANDLERS
  - Scripts/_mcp_smoke_test.py:purity_semantic_checks
  - Scripts/mcp-lua-lsp.py
verified:
  commit: 7459e17
  date: 2026-09-10
links:
  - spec-purity-unification
  - 0001-purity-server-unification
  - scripts
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
---

# Phase 1: `luals` as the second LSP backend

Phase 0 built a backend abstraction with exactly one implementation, so its
"luals-ready" claim was untested by construction. Phase 1 is that test: it folds
`lua-language-server` in behind the unchanged `purity_call` entry point and, in
doing so, establishes what actually varies between two LSP servers. The decision
is recorded in [[0001-purity-server-unification]]; the skeleton it builds on is
[[spec-purity-unification]].

## What a second backend forced: a divergence layer

Phase 0's `ClangdClient` *was* the backend. Phase 1 splits it — the shared
transport, request/response correlation, reader loop and `textDocument/*`
wrappers moved into `Scripts/mcp-purity.py:BaseLspClient`, and the two concrete
clients now override only their handshake plus a small set of named divergence
points.

The load-bearing choice is *where* that divergence is consulted: the hooks and
attributes are read by module-level semantic helpers, **not** by the handlers
`Scripts/mcp-purity.py:BaseLspClient`. That is what keeps all 10 canonical
handlers backend-agnostic, so a third backend would not touch them. The surface
two real servers needed turned out to be small: call-hierarchy support, the
fallback/prime extension sets, the languageId, hover-to-type conversion, and a
supplemental-references hook.

`supports_call_hierarchy` is the clearest evidence the abstraction is real rather
than nominal — luals does not implement call hierarchy at all
`Scripts/mcp-purity.py:LuaLsClient`, which turned `symbol_change_impact`'s
partial-result path from a Phase 0 hypothetical into a live code path.

## Three things luals needs that clangd does not

**A dual config push.** `LuaLsClient.start` sends the `Lua` config block *both*
as `initializationOptions` and again as a post-init
`workspace/didChangeConfiguration` `Scripts/mcp-purity.py:LuaLsClient`. The
redundancy is deliberate: some luals versions ignore `initializationOptions` when
the client negotiates the `workspace/configuration` capability, and dropping
either push fails silently rather than loudly — it stalls startup until the
90-second timeout. A silent 90s hang is the expensive failure mode, so both
pushes stay.

**A reply to `workspace/configuration`.** luals requests per-folder settings
during and after init and *blocks* until answered; the reply is one null per
requested item, meaning "no override, use defaults"
`Scripts/mcp-purity.py:LuaLsClient`. Not answering is not a degraded mode, it is
a hang.

**A grep supplement for references.** `Scripts/mcp-purity.py:_lua_text_references`
merges word-boundary text matches into LSP `textDocument/references` results. The
gap it fills is dynamic dispatch — a method reached through a table held in a
variable whose type luals cannot resolve. Note this is a *supplement*, not a
fallback: it runs alongside a successful LSP answer instead of replacing a failed
one, which is why it deduplicates against already-collected references. It runs
in the thread executor so large Lua workspaces do not block the event loop.

## Routing and dispatch

`.lua` paths reach the luals backend through the same filetype routing Phase 0
established, extended with a dispatcher-level hint for calls that carry no path
`Scripts/mcp-purity.py:_select_filetype`. The legacy `luals_*` names are
registered as direct `HANDLERS` keys `Scripts/mcp-purity.py:HANDLERS`, obeying
the same constraint Phase 0 discovered — the dispatcher routes on the raw
function name, so an alias that is not a direct key does not route.

## The security pass that shipped with it

A user-authorized hardening pass ran alongside the fold, on the reasoning that a
second subprocess-spawning backend doubles the untrusted-input surface:

- **Containment at every read site.** Symlink and URI containment is applied
  wherever a path reaches the filesystem, including the URIs that come back *from*
  the language server `Scripts/mcp-purity.py:_lsp_path_in_root` — an LSP response
  is untrusted input too.
- **Absolute binary resolution** (CWE-426) for both backends, with
  `--clangd-path` / `--luals-path` overrides
  `Scripts/mcp-purity.py:_resolve_lsp_binary`. Residual and explicitly accepted:
  `shutil.which` PATH ordering when no override is passed, under a local
  single-user threat model.
- **A `compile_commands` exec-flag denylist**
  `Scripts/mcp-purity.py:CLANGD_EXEC_DENYLIST_EXACT` so a hostile repository
  cannot make clangd load a shared object via `-load` or `-fplugin=`.
  Response-file expansion is contained and nesting-guarded, because the filter is
  otherwise bypassable by indirection.
- **Log-injection and resource ceilings**: CR/LF stripped from logged values
  `Scripts/mcp-purity.py:_sanitize_log`, a caller-regex length cap with scan
  deadlines, and a 64 MB LSP `Content-Length` ceiling checked *before* the read,
  so a buggy or hostile child cannot drive the server out of memory.

Validation is over the wire rather than in-process: the smoke test asserts luals
dispatch alongside the Phase 0 assertions
`Scripts/_mcp_smoke_test.py:purity_semantic_checks`. A fleet-level gate has since
joined it: check 7 drives all fifteen servers with an unknown function name and
requires `isError: True`, read as the flag and never as the reply text, plus a
control that omits `function` entirely to prove a deliberate status reply is not
flagged `Scripts/_mcp_smoke_test.py:error_envelope_checks`.

## Still deferred

Phase 2 — retiring the standalone `clangd_call` / `cuda_call` tools and migrating
the `p:mcp-clangd` / `p:mcp-cuda` skills and minion tool-lists — remains
unstarted. All three standalone servers are still in the tree: this phase's own
counterpart `Scripts/mcp-lua-lsp.py` beside `Scripts/mcp-clangd.py` and
`Scripts/mcp-cuda.py`, with both skills.

Unretired is not unmaintained, and the fleet has now paid that distinction twice.
None of the three is registered, so Claude Code never launches them — but they
are the template the whole fleet grew from, which is why the read-loop conversion
covered them rather than skipping them
[[0008-a-serialized-read-loop-looks-like-a-dead-server]], and why the
error-envelope contract covers them too. Each carries an `_ErrorText` `str`
subclass so a handler failure still reaches the caller's `isError` flag after the
dispatcher has already flattened its reply to text
`Scripts/mcp-lua-lsp.py:_ErrorText` — the three servers whose `_serialize` is a
closure every exit has run, so the wrap only ever sees a `str`.

That helper is three byte-identical hand copies, and it stays copies on purpose.
It is *declared* as a divergence in `Scripts/MCP_SKELETON.md` rather than homed as
a generated canonical block: generating it would bless a second error-reporting
mechanism as infrastructure and buy drift protection **for** the divergence in
three servers that never launch, instead of removing it. See [[scripts]] for the
fleet-wide shape and the canonical-block machinery it declines to use.
