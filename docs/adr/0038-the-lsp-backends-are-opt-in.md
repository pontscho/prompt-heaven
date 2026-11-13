---
name: 0038-the-lsp-backends-are-opt-in
type: adr
status: active
title: The LSP backends are opt-in -- mcp-purity serves clangd and luals only when the launch line says --clangd / --luals
description: Decision (amends ADR 0001) that mcp-purity's two LSP backends are off unless the launch line switches them on -- --clangd and --luals default off, --clangd-path / --luals-path imply their flag, enabling stays lazy with no startup probe or spawn, CUDA is part of --clangd, and clang_tidy stays always on by user decision; every name stays registered and a call to a disabled function reaches one refusal naming the flag, gated in _ensure_backend, restart_lsp and the init no-ops after parameter validation; the tool description, status reply, Available list and did-you-mean pool show only enabled names; path-less routing follows the single enabled backend; with the alternatives rejected, the 32-case group K, the consequence that the owner's live launch lines carry no flag, and the declared limit that agents and skills still name the semantic functions as the C/C++ route.
sources:
  - Scripts/mcp-purity.py:_ENABLED_BACKENDS
  - Scripts/mcp-purity.py:_require_enabled
  - Scripts/mcp-purity.py:_require_any_enabled
  - Scripts/mcp-purity.py:_lsp_filetype
  - Scripts/mcp-purity.py:_select_filetype
  - Scripts/mcp-purity.py:_ensure_backend
  - Scripts/mcp-purity.py:handle_lsp_init_noop
  - Scripts/mcp-purity.py:handle_restart_lsp
  - Scripts/mcp-purity.py:_name_requires
  - Scripts/mcp-purity.py:_visible_function_names
  - Scripts/mcp-purity.py:handle_purity_call
  - Scripts/mcp-purity.py:_purity_description
  - Scripts/mcp-purity.py:PURITY_CALL_TOOL
  - Scripts/mcp-purity.py:main
  - tests/test_purity_lsp.py:group_k
  - Scripts/_mcp_smoke_test.py:SERVERS
verified:
  commit: 8717044
  date: 2026-11-13
links:
  - 0001-purity-server-unification
  - 0030-point-at-the-near-miss-never-resolve-it
  - scripts
  - tests
  - agents
  - skills
---

# ADR 0038: The LSP backends are opt-in

Status: accepted 2026-11-13, implemented in `8717044`. Amends
[[0001-purity-server-unification]]: the unified server still carries both LSP
backends behind `purity_call`, but neither is served unless the launch line asks
for it. Every count below is the count at the moment of the decision; the live
values belong to the code and to the living pages [[scripts]] and [[tests]].

## Context

ADR 0001 folded clangd (C / C++ / CUDA) and, in Phase 1, lua-language-server
into `mcp-purity` behind the one `purity_call` tool, and from then on every
purity launch advertised the eleven semantic functions, the legacy
`clangd_*` / `cuda_*` / `luals_*` names, `restart_lsp` and the
"LSPs spin up lazily" paragraph in its tool description. The owner never uses
the semantic functions. A description is read by the model on every request, so
a block of capability nobody calls is paid for on every request, and a backend
that can be spawned is process surface whether or not anyone wants it. The ask
was to make the semantic half something a launch line opts into, without losing
the file layer, `clang_tidy`, or the refusal quality the fleet holds every
dispatcher to ([[0030-point-at-the-near-miss-never-resolve-it]]).

## Decision

**Two flags, both off by default.** `--clangd` enables the clangd backend and
`--luals` the lua-language-server backend; `--clangd-path` / `--luals-path` imply
their flag, so a launch line that already pins a binary (the CWE-426 mitigation)
does not also have to repeat the switch. `main()` fills one module-level set from
the four arguments `Scripts/mcp-purity.py:main`, and an in-process import starts
with it empty `Scripts/mcp-purity.py:_ENABLED_BACKENDS`.

**Enabling is lazy, as before.** A flag only makes a backend *eligible*: nothing
is probed, resolved or spawned at startup, and the first semantic call still
does the spawn. Eager verification was rejected (below).

**CUDA is part of `--clangd`.** CUDA has always been a language configuration of
the clangd-family backend (ADR 0001), so it gets no flag of its own; `cuda_*`
legacy names need `--clangd` `Scripts/mcp-purity.py:_name_requires`.

**`clang_tidy` stays always on** (user decision). It is a plain clang-tidy
subprocess, not an LSP backend, so nothing about it is gated and it stays in
every description, status reply and `--list` row `Scripts/mcp-purity.py:handle_clang_tidy`.

**Every name stays registered; a disabled one reaches one refusal naming the
flag.** `HANDLERS` is unchanged. The gate is in three places, each the single
choke point of its path:

- `Scripts/mcp-purity.py:_ensure_backend`, which every semantic handler passes,
  refuses a disabled backend **before** the per-backend lock, the registry or any
  spawn is touched `Scripts/mcp-purity.py:_require_enabled`;
- `Scripts/mcp-purity.py:handle_restart_lsp` refuses before the lock and before
  any teardown or cache wipe;
- the `clangd_init` / `cuda_init` / `luals_init` no-ops refuse instead of
  answering their benign notice `Scripts/mcp-purity.py:handle_lsp_init_noop`.

Parameter validation still runs first: an unknown key on a disabled function is
answered `Unknown params for ...`, not with the disabled refusal
`Scripts/mcp-purity.py:handle_purity_call`. The refusal texts are fixed:

- `LSP backend 'clangd' is disabled; launch mcp-purity with --clangd` (and the
  `luals` / `--luals` twin);
- `LSP backends are disabled; launch mcp-purity with --clangd and/or --luals`
  when nothing is enabled and the call names no file `Scripts/mcp-purity.py:_require_any_enabled`;
- `no LSP backend handles this file type` for a path that is neither C-family nor
  Lua (`.py`, `.md`, no suffix) while clangd is off. Such a path used to fall back
  to clangd's `cpp`; with clangd off, naming `--clangd` for a Python file would be
  misleading, so it is unrouted instead `Scripts/mcp-purity.py:_lsp_filetype`.
  With clangd on the old fallback is unchanged.

**Visibility follows the enabled set.** The tool description is assembled from a
file-ops core plus per-backend fragments `Scripts/mcp-purity.py:_purity_description`:
with no backend there is no semantic block, no lazy-spawn sentence, no legacy-name
sentence and no `Semantic (...)` list, and a `STATIC ANALYSIS` heading keeps
`clang_tidy`. The status reply, the `Available:` list of an unknown-function
refusal and the did-you-mean pool list only enabled names, so a typo is never
pointed at a function that would refuse `Scripts/mcp-purity.py:_visible_function_names`.
`restart_lsp` is hidden when no backend is on. `tools/list` serves the
description for the launch's set, fixed once `main()` has run.

**Routing of a path-less call** (a `symbol` query, a by-name `find_definition`)
follows the single enabled backend; with both on it is clangd, as before; with
none it refuses naming both flags `Scripts/mcp-purity.py:_select_filetype`.
`restart_lsp` with no target picks the one enabled backend the same way.

**`--list` stays before argparse** and still prints every registered name; a
gated row is tagged `[requires --clangd]`, `[requires --luals]` or
`[requires --clangd or --luals]` `Scripts/mcp-purity.py:main`.

**`PURITY_CALL_TOOL` stays a module constant** equal to the both-on description,
which is byte-identical to the single literal it replaced
`Scripts/mcp-purity.py:PURITY_CALL_TOOL`. The tests read the constant; the server
no longer serves it unconditionally.

## Alternatives rejected

- **Unregister the semantic names when their backend is off.** Smaller code, but
  a call to `find_definition` would become `Unknown function` with a near miss
  pointing somewhere else, losing the one sentence that tells a caller which flag
  turns it on; and `name_existence`, which checks the prompt corpus against the
  live inventory, would lose coverage of names the agents and skills still
  mention. Registered-but-refusing keeps both.
- **A separate `--cuda` flag.** CUDA is not a backend, it is a clangd language
  configuration; a third flag would gate nothing the clangd flag does not already
  gate, and would add a combination (`--cuda` without `--clangd`) with no meaning.
- **Gate `clang_tidy` with `--clangd`.** Rejected by the user: it is a one-shot
  subprocess with no LSP session, its own `--clang-tidy-path`, and its own use;
  it was kept as it was.
- **Verify the enabled binaries at startup.** Fails fast on a missing clangd, but
  breaks the lazy rule the smoke row relies on (a purity launch spawns nothing
  until a semantic call) and makes every launch pay a probe the owner's sessions
  never need. The binary check stays at first use, where group K proves it fires.

## Tests

`purity_lsp` moved from 155 to 187 cases with group K (32 cases)
`tests/test_purity_lsp.py:group_k`: the off launch (every refusal text, the
legacy and init names, the parameter-first order, `clang_tidy` unchanged, the
status reply, the unknown-function list, the did-you-mean pool and the
description all free of semantic names), a luals-only launch, `--clangd-path`
and `--luals-path` each implying their flag, a both-on launch whose description
equals `PURITY_CALL_TOOL`, an in-process run proving nothing is spawned while
off, and the `--list` tags and docstring usage. Every launch that switches a
backend on points `--clangd-path` / `--luals-path` at a nonexistent binary
(`tests/test_purity_lsp.py:K_NOBIN_CLANGD`), so the group proves routing by
reaching the binary check, and no language server process is ever spawned by it.

The existing live groups, `purity_file_ops` and the smoke row launch with
`--clangd --luals` `Scripts/_mcp_smoke_test.py:SERVERS`
`tests/test_purity_file_ops.py`. Group J's in-process rows set
`_ENABLED_BACKENDS` by hand, because an import starts with everything off
`tests/test_purity_lsp.py:group_j`.

## Consequences

- **The owner's live launch lines carry no flag** (`~/.claude.json` and the
  mcp-proxy children config, both outside the repo and reported at decision
  time, not anchored), so semantic navigation disappears from those sessions by
  design: the description loses the semantic block and a semantic call refuses
  naming the flag. Re-enabling is one flag on the launch line.
- The agents and skills that route C / C++ / Lua symbol work to the purity
  semantic functions were reworded to "when enabled" in `ddfd397`: the four
  `mcp-*` skills gained an enabling section, and the minion prompts say that on
  the first `disabled` refusal they fall back to `search_for_pattern` /
  `find_file` plus Read without retrying `ClaudeCode/agents/minion-watson.md`
  ([[agents]], [[skills]]).
- The description is fixed per process: there is no `listChanged`, so enabling a
  backend takes a relaunch.

## Declared limits

- The 13 agent prompts and the skills still describe the semantic functions as
  *the* C/C++ (and Lua) route; after the rewording that route is conditional, and
  nothing gates that a prompt keeps saying so. A session without the flags that follows
  such a prompt gets the flag-naming refusal, which is the designed failure.
- The byte-identity of the both-on description to the pre-change literal is
  asserted by the code's own comment and by reading the diff; group K pins the
  live both-on description to `PURITY_CALL_TOOL`, not to the old literal.
