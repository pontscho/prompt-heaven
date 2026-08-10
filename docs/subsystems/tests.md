---
name: tests
type: subsystem
status: draft
title: Test Fleet
description: The stdlib-only functional test fleet — one explicit entry point, a two-layer harness, case counts written down once and machine-checked, and a severity model where FAIL is reserved for rules that cannot flap.
sources:
  - tests
  - project-forge.yaml
links:
  - agents
  - scripts
  - layer-contract
---

# Test Fleet

`tests/` is a stdlib-only, zero-dependency functional test fleet: one entry point
(`tests/run.py`), one shared plumbing module (`tests/_harness.py`), ten in-process
suite modules plus a subprocess smoke check, and a committed C/Lua fixture tree
under `tests/files`. Two organising ideas explain nearly every design choice in
the tree, and both exist because the repo already paid for the alternative.

## Idea 1 — every typed number is machine-checked

The `SUITES` table in `tests/run.py` is the single place a suite's case count is
written down. The banner prints it, and the runner asserts it against what the
suite actually reported; a mismatch is a hard failure with an error that tells
you to fix the table. The reason is stated where the table is defined: *"A count
that lives in prose and is checked by nobody is a lie waiting to happen — this
repo shipped six of them."*

A declared count of `None` is not laziness — it means the count is **data-derived
rather than a fixed case table**. `spawn_stdin` emits one case per spawn site it
finds, so the gate is the invariant (every site passes an explicit `stdin=`), and
a drift line reading `265 != 262` would be a strictly worse error message than
naming the new site. The rule the tree follows: *"What is typed gets checked;
what is derived gets derived."* `tests/README.md` The same instinct removes the
fleet size from human hands — `tests/run.py` computes it from
`Scripts/_mcp_smoke_test.py`'s own table, because a hand-maintained copy was
wrong within a day of being written.

Note what this gate does **not** cover: it compares the `SUITES` table against
the run, and nothing else. A case count repeated in a module docstring is
outside it — which is why the convention is to never write one there, and why a
docstring that still claims 94 cases for a suite that declares 119 can survive a
fully green run (`tests/test_inspect_validate.py`).

## Idea 2 — severity is argued, not assumed

Every rule's severity is a written decision, and the criterion is whether the
rule can **flap on ordinary work**. The clearest statement of the test is in
`tests/test_name_existence.py`, defending a FAIL: the rule fires on a structural
frontmatter key, the corpus now carries zero of them, so *"the rule has no live
subject, so it cannot flap on an ordinary prompt edit. It can only fire on a
REGRESSION — precisely the event worth breaking a build for."*

The inverse governs everything that fires on prose. Rule 3b in the same suite
scans agent bodies for tool prescriptions, and prose editing is this repo's main
activity, so a false FAIL there would fire constantly. INFO is likewise chosen
where a finding is a measurement rather than a verdict (`mcp_footprint` is a
measuring tape and never a gate), or a knowingly-open gap that has not been
decided yet — turning those into gates *"would report a decision that has not
been made yet as a regression"* (`tests/test_mcp_footprint.py`).

INFO rows are printed, not swallowed, and that is the point: a knowingly-open
gap stays **visible**, so promoting a tree to gated later is a scope decision on
printed data rather than a fresh audit (`tests/test_spawn_stdin.py`).

## The suppressor — and the one that was removed

A **suppressor** is a documented contextual condition that demotes a candidate
FAIL to an INFO-with-evidence row. Three are live in `tests/test_name_existence.py`:
a tool whose server is not registered cannot be granted, so naming it is a
retirement note; an illustrative context on the mention line (`e.g.`, `for
example`, `such as`, `etc.`); and a negative or delegating context — a
"does-not-exist" sentence nearby, or another agent named on the line, where the
agent's own name pointedly does not count. The window is asymmetric on purpose:
negative context counts within ±1 line, the other two must be line-exact, so a
neighbouring `e.g.` cannot excuse a prescription of its own.

The subsystem's cautionary tale is the **fourth suppressor, since removed**. It
excused a missing grant whenever the agent's frontmatter disagreed, and its only
live subject turned out to be a real defect it was hiding — leaving one minion
silently unable to perform the documentation lookup its own routing table
prescribed. The conclusion is recorded in the source and is the reason the
related rule now fails on the key rather than reasoning about it: *"That is the
cost of answering a platform question with a suppressor instead of a fact."*

Suppressors are themselves negative-controlled. A synthetic agent corpus pins
the exact expected severity of every case, and **INFO and silence are asserted as
hard as FAIL** `tests/test_name_existence.py` — because an over-eager suppressor
produces exactly the same "no FAIL" as a correct one.

## The harness contract

`tests/_harness.py` is deliberately two layers rather than one abstraction,
*"because the suites do NOT speak the same protocol and must not be forced
through one"*: a protocol-agnostic layer (options, suites, results, temp
workspaces, process running) and a JSON-RPC client used only by suites that
drive an MCP server child.

The single most load-bearing detail: **status is the display verdict, `problems`
is the actual one.** Pass/fail accounting is driven by `problems` alone, so an
INFO case that trips a hard invariant — non-zero exit, dirty stderr — still
counts as a failure while keeping its informational label.

There is also an undeclared fourth status. `SKIP` is not a harness constant; two
suites define it locally as a plain string, and because a `SKIP` row carries no
problems and is not `INFO`, the group tally counts it as a **PASS**. `purity_lsp`
does the opposite, recording its skips as `INFO` so a host without clangd or
lua-language-server stays green without inflating the pass count. Two suites, two
incompatible conventions — worth knowing before reading a tally.

Rendering has two modes so neither ported driver lost its output shape: streamed
one-line-per-case, or buffered and grouped into `=== group (pass N, fail N, info
N) ===` blocks.

## The roster

Eleven registry entries in `tests/run.py` — ten in-process Python suites plus
`smoke`, which runs `Scripts/_mcp_smoke_test.py` as a subprocess and reports
*servers* rather than cases. Every suite has a matching `forge` target in
`project-forge.yaml`, each requiring the `syntax` prerequisite.

| Suite | What it verifies |
|---|---|
| `inspect_validate` | the `mcp-inspect` validation family against fixtures, including the reported error line number |
| `mcp_first_guard` | the deny-guard hook: DENY iff the permission decision says so |
| `sbx_gate` | the grant-only gate — the guard suite's inverted mirror, where empty stdout is the safe outcome |
| `purity_lsp` | that `purity_call` really absorbed the retired clangd/luals servers, driven against live language servers |
| `purity_file_ops` | the gitignore-aware file handlers: the `.claude/tmp` exemption **and** its narrowness |
| `mcp_git_params` | named params → `git` argv, fully offline with `subprocess` stubbed |
| `name_existence` | prompt corpus + server text ↔ live MCP inventory, plus agent grants vs their own prescriptions |
| `spawn_stdin` | every spawn site under `Scripts/` passes an explicit `stdin=` — AST-based, one case per site |
| `mcp_footprint` | fleet token cost: description tax, result ceilings, boilerplate. A tape measure, not a gate |
| `wiki_recall` | the wiki search relevance gate on a synthetic corpus — silence, calibration, type signal, aliases |
| `smoke` | JSON-RPC plumbing invariants across every server file |

There is **no auto-discovery**: adding a suite is three edits — the module, a
wrapper function, and the `SUITES` row `tests/run.py`.

### Why the smoke check is still standalone

`Scripts/_mcp_smoke_test.py` sits outside `tests/`, and the runner treats its
**interface** as frozen: it invokes the script as a subprocess, consumes its
exit code, and surfaces its output only on failure `tests/run.py`. That is also
why it reports *servers* rather than cases — the aggregate sums the two units
separately instead of inventing a case count for it.

A frozen interface is not a frozen file. Its launch table has since gained a
`registered` flag per entry, recording whether Claude Code actually starts that
server as opposed to merely whether the file exists
`Scripts/_mcp_smoke_test.py`. The table enumerates server *files*, the two sets
are **not** equal, and conflating them has already produced one wrong fleet-wide
conclusion here — which is why `mcp_footprint` sums only over registered servers
while the smoke check deliberately ignores the flag: its job is that every server
file still speaks the protocol, registered or not.

## Fixtures

`tests/files` holds committed C and Lua fixtures for `purity_lsp` only. They are
fixtures, not code: nothing there is compiled, linked, shipped or imported.

The `tf` prefix on every symbol is an **invariant, not a style choice** — a
repo-wide search for a real symbol must never match this directory. Two files are
**deliberately broken** and carry a header telling you so: *"Do not fix this
file. A test asserts that clangd reports a problem here; repairing it would
silently disable that assertion."* Their defects are asserted at their planted
lines.

No `compile_commands.json` is committed, and that omission is load-bearing twice
over: it is why the language server emits no progress notifications against this
repo, and why a cache-exception list can stay empty while its group asserts
strictly.

## Invariants a newcomer breaks

- **Never write a case count into a module docstring.** It is written once, in
  `tests/run.py`, where it is checked against the run. A second copy is a number
  nobody verifies — and three modules already violate this.
- **Every scanner suite must carry a negative control.** *"A checker that
  silently matches nothing is indistinguishable from a clean tree."*
  `tests/test_spawn_stdin.py`
- **AST, never regex, for source-scanning suites** — both regex false-positive
  shapes are live in this repo, so the justification is empirical, not aesthetic.
- **Bytecode discipline.** `sys.dont_write_bytecode` is set before the first repo
  import, children inherit the environment variable, and two suites assert
  **zero** `.pyc` absolutely rather than as a delta — because a file that already
  existed reads as "1 before, 1 after" and sails through a delta check. This is
  also why `py_compile` must never be reintroduced.
- **Two sandbox conventions coexist and are not interchangeable.** Some suites
  use a system temp workspace; three use a per-run `.claude/tmp/<suite>/`
  directory whose escape is structurally gated — a single write path and a single
  child launcher record every target, and the group fails if any recorded path
  leaves the sandbox.
- **No clean target wipes `.claude/tmp`.** It holds the session handoff document
  and is gitignored, so it is not recoverable; suites clean their own
  subdirectories in a `finally`.
- **Nothing numeric may be typed if the module under test publishes it** —
  `wiki_recall` reads the live constants, because a hardcoded copy would still
  pass after somebody swapped the real values out, which is the exact edit its
  group exists to catch.
