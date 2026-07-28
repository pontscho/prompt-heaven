# tests/files — language fixtures for the semantic-navigation surface

These are **fixtures, not real code**. Nothing here is compiled, linked, shipped
or imported by the project. They exist so the compiler/LSP-accurate half of
`purity_call` can be exercised inside this repo, which is otherwise Python and
Markdown only.

## Why they exist

`mcp-clangd` and `mcp-luals` were unregistered as standalone MCP servers because
`purity_call` absorbed their semantic navigation. That claim needed a way to be
tested. Before these fixtures the repo contained no C/C++ at all and only a few
throwaway `.lua` files under `.claude/tmp/`, so the absorption could only be
validated by switching to a different project.

## The `tf` prefix rule — do not break it

**Every symbol, type, macro and module name here starts with `tf`** (`tf_vec_add`,
`TF_VEC_DIM`, `tfMathlib`, …). That is deliberate: a repo-wide search for a real
symbol must never match this directory, and a headless eval that greps the repo
must not pick these up as if they were project code. If you add a fixture, keep
the prefix.

## Layout

```
c/    tf_math.h      declarations: macro, enum, struct, three functions
      tf_math.c      the definitions
      tf_main.c      call sites, so find_references has several hits to find
      tf_broken.c    deliberate type error, for the diagnostics path
lua/  tf_mathlib.lua module table with functions and fields
      tf_consumer.lua requires the module and calls into it
      tf_broken.lua  deliberate defect, for the diagnostics path
```

`tf_broken.*` are **meant to be broken**. Do not "fix" them; a test asserts that
the LSP reports a problem in them. They are never part of a build.

## Notes on the toolchain

- No `compile_commands.json` is committed. It is inherently machine-specific and
  would be stale for anyone else. These translation units are plain C99 with a
  local `#include`, which clangd handles on its fallback flags. If a future test
  needs exact flags, generate the file at test time rather than committing one.
- CUDA fixtures are deliberately absent: `nvcc` is not installed on the current
  machine, so a `.cu` fixture could not be honestly verified here.
- The Lua files rely on `lua-language-server` defaults; no `.luarc.json` is
  committed for the same staleness reason.
