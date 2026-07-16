---
name: 0001-purity-server-unification
type: adr
status: current
title: Unify mcp-clangd and mcp-cuda into mcp-purity
description: Decision to fold the clangd and cuda LSP servers into mcp-purity behind purity_call via a layered backend architecture.
sources:
  - Scripts/mcp-purity.py
  - Scripts/mcp-purity.py:ClangdClient
  - Scripts/mcp-purity.py:_resolve_aliases
verified:
  commit: 2787c7f
  date: 2026-07-16
links:
  - scripts
  - spec-purity-unification
---

# ADR 0001: Unify `mcp-clangd` + `mcp-cuda` into `mcp-purity`

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is the companion spec [[spec-purity-unification]].

## Context

Three single-file, stdlib-only MCP servers under `Scripts/` each register one
tool: `mcp-purity.py` (file IO + search), and `mcp-clangd.py` / `mcp-cuda.py`
(C/C++/CUDA semantic navigation via clangd). `mcp-cuda.py`'s LSP core is a
byte-for-byte copy of clangd's `Scripts/mcp-clangd.py:ClangdClient`, and its
`_symbol_to_location` fallback is truncated (missing two tiers), so CUDA
`static` / `__device__` symbol lookup fails. Callers must also learn three tool
names for one concern — code intelligence — and the `purity_call` file layer is
split across processes from the LSP tools.

## Decision

Fold the clangd + cuda semantic capability into `mcp-purity.py` behind the
unchanged `purity_call` entry point, via a **layered in-place unification**:
keep the working sync file layer behind a one-line executor wrap; add an async
LSP-backend layer with a single clangd-family backend (C/C++/CUDA carried as
language-config, not duplicated code); route by filetype through a backend map
`Scripts/mcp-purity.py`. Legacy `clangd_*` / `cuda_*` names survive as aliases.
Phase 0 (the skeleton) ships this; luals (Phase 1) and retiring the standalone
`clangd_call` / `cuda_call` tools (Phase 2) are deferred. The `purity_call`
name, dispatcher contract, and the stdlib-only constraint are invariant.

## Alternatives Evaluated

### Option 1 — Thin facade over three servers
A new server that calls the existing three as backends.
- **Pros:** existing servers untouched; lowest immediate risk.
- **Cons:** 4 processes + MCP-over-MCP; preserves the clangd↔cuda duplication
  and the CUDA fallback bug; doubles the init lifecycle; leaves the fundamental
  problem untouched.

### Option 2 — Full async rewrite of the file layer
Rewrite every sync file handler as `async def` with `asyncio.to_thread`.
- **Pros:** uniform async code.
- **Cons:** large regression surface across a working, battle-tested file layer
  for no functional gain.

### Chosen — Layered in-place unification (executor-wrapped sync + backend-type map)
Folds clangd+cuda into one backend (eliminating the duplication and the CUDA
bug as a side effect), keeps the working file layer untouched behind a one-line
executor wrap, scales to luals via the backend map, and preserves the
`purity_call` convention.

## Consequences

- **Positive:** one code-intelligence entry point; the clangd↔cuda duplication
  and the CUDA truncation bug (`_symbol_to_location`) are eliminated by
  construction; a crashed clangd fails pending futures promptly instead of
  hanging; the backend map is luals-ready.
- **Backwards compatibility:** file-IO / search functions unchanged; legacy
  `clangd_*` / `cuda_*` names become aliases inside `purity_call`; the standalone
  `clangd_call` / `cuda_call` tools keep running in parallel through Phase 0.
  Rollback = revert the single `mcp-purity.py`.
- **Costs / risks:** `mcp-purity.py` grows to ~3000 lines (accepted — matches
  the one-file-per-server convention); during the Phase 0 parallel window two
  clangd processes may target the same project index (`.cache/clangd/index/`)
  and must tolerate the contention.
- **Invariants held:** Python 3.9+, stdlib-only (`# dependencies = []`);
  `purity_call` tool name + dispatcher contract unchanged.

The full implementation plan (requirements, captured code, step-by-step,
testing, security) lives in the companion spec: [[spec-purity-unification]].
