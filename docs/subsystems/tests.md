---
name: tests
type: subsystem
status: active
title: Test Fleet
description: The stdlib-only functional test fleet — one explicit entry point, a two-layer harness, case counts written down once and machine-checked, and a severity model where FAIL is reserved for rules that cannot flap.
sources:
  - tests
  - project-forge.yaml
verified:
  commit: 2663d02
  date: 2026-09-16
links:
  - agents
  - scripts
  - generated-regions
  - layer-contract
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0009-the-first-reader-is-a-cold-model
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0013-the-ceiling-is-a-payload-class
  - 0016-a-cell-may-not-forge-a-boundary
---

# Test Fleet

`tests/` is a stdlib-only, zero-dependency functional test fleet: one entry point
(`tests/run.py`), one shared plumbing module (`tests/_harness.py`), the in-process
suite modules its `SUITES` table registers plus a subprocess smoke check, and a
committed C/Lua fixture tree under `tests/files`. Two organising ideas explain
nearly every design choice in the tree, and both exist because the repo already
paid for the alternative.

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

`None` is **not** the default for a per-server suite, though, and three rows say
so where somebody would go looking: `read_loop`, `wire_log` and `handler_crash`
each emit a fixed multiple of the fleet size and each **types** its count anyway
`tests/run.py`. The reason is written beside every one of them, and it is the
exact inverse of `mcp_footprint`'s: there a moved count would fire on a
legitimately added server, so pinning it would buy a false failure; here *a
server appearing without a declared row is the defect*, so a count that moves
when the roster moves is the alarm working rather than noise. Same arithmetic,
opposite verdict — which is why the choice is argued per suite instead of read
off the shape.

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
measuring tape almost everywhere), or a knowingly-open gap that has not been
decided yet — turning those into gates *"would report a decision that has not
been made yet as a regression"* (`tests/test_mcp_footprint.py`).

**That last reason can expire, and watching one expire is the useful half.** The
sentence names its own precondition — a decision — so a landed decision does not
merely permit the promotion, it withdraws the argument for the INFO.
[[0013-the-ceiling-is-a-payload-class]] ratified the fleet's three output
ceilings as three payload classes, and exactly one server finding in
`mcp_footprint` became a gated FAIL: `ceiling-deviation-says-why`, which asserts
that a server carrying a non-default ceiling wrote its reason down. It clears the
flap test for the ordinary reason — an int and a comment, both already in the
repo, no environment, no binary, no clock — but the part worth copying is that
the promotion was a **reading** of a condition the suite had itself recorded,
not a fresh argument. Conformance as a whole stays INFO, because that ADR
licenses a deviation on the default alone and leaves the other two criteria open.

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

The harness also owns the fleet's **repo-pollution detectors**, and they are a
pair with a deliberate asymmetry: `pycache_snapshot` for bytecode
`tests/_harness.py:pycache_snapshot` and `repo_tree` for path names
`tests/_harness.py:repo_tree`. Both walk from the repo root and both skip
`.git`, which churns on its own. Only `repo_tree` also skips the scratch area,
and that difference is the point rather than an oversight: a write under
`.claude/tmp` is legitimate work by a suite that owns a sandbox there, while a
`.pyc` under it is litter like any other — so the bytecode half deliberately
sees `ClaudeCode/`, `docs/` and the scratch area too.

A suite takes the snapshot at its start and again in its hygiene group, failing
on `after - before`. Neither set of users is written down here, and both are
open: every suite that snapshots path names also snapshots bytecode, but not the
reverse — the bytecode half is strictly the larger set. The case id is not a
reliable handle either, because the same check is registered as
`no-new-repo-paths`, `k-no-new-repo-paths` and `no new repo paths` in different
suites; read the hygiene group, do not grep the name. `repo_tree` lived as four
hand copies until one of them independently grew the scratch-area exclusion and
the other three never learned it; homing it beside its twin is what ended that.
The `SUITES` table was not touched by that move `tests/run.py`, so Idea 1's own
gate is what certifies those four kept their case counts.

Two details of the homed version are recorded decisions rather than incidental.
The scratch area is excluded **at any depth** `tests/_harness.py:SCRATCH_DIR`,
mirroring `.gitignore`'s deliberately unanchored `**/.claude/tmp`, because a
root-anchored skip would be *narrower than the concept it is named after* — and
that mirroring is a **declared** divergence, not a silent hand-copy: the check
is an `os.walk` over path names that deliberately never consults git, since a
git dependency would change what it measures into "did some future `.gitignore`
excuse this mess". And the match is on whole path components, never
`str.startswith` `tests/_harness.py:_is_scratch_dir` — `".gitignore"` starts
with `".git"`, and the prefix test this replaced silently ate that file out of
the snapshot.

## The roster

One registry entry per suite in `tests/run.py`: the in-process Python suites
listed below plus `smoke`, which runs `Scripts/_mcp_smoke_test.py` as a
subprocess and reports *servers* rather than cases. Every suite has a matching
`forge` target in `project-forge.yaml`, each requiring the `syntax` prerequisite.
That direction does **not** invert, and the table below is keyed on suites only:
forge's `test` group also carries targets that are not suites and have no row
here — `test.amalgamate_check` wraps `Scripts/amalgamate.py --check`, the
generator's own CLI staleness gate, which takes the same `syntax` prerequisite
but reports an exit code rather than a case count `project-forge.yaml`.

There is no suite count in that sentence on purpose. The one it used to carry was
two suites behind by the time anyone noticed — Idea 1's own failure mode, showing
up in the page that argues for it. The table below is the roster, `tests/run.py`
is the registry, and the run is the only thing that knows the totals.

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
| `mcp_footprint` | fleet token cost: description tax, result ceilings, boilerplate. A tape measure, not a gate — with one exception, the ceiling-deviation rule of [[0013-the-ceiling-is-a-payload-class]], plus its own negative control |
| `wiki_recall` | the wiki search relevance gate on a synthetic corpus — silence, calibration, type signal, aliases |
| `jira_cli` | the Jira CLI fully offline with the transport injected: auth mode, context-path URL join, lazy deployment probe, **four** pagers — the Cloud token model and the DC offset model behind one iterator, the agile `isLast`/`startAt` envelope that serves boards, sprints and create metadata, and `list_projects`, which is that same envelope hand-rolled a second time and was never counted with its three siblings, walked past page one with the caller's query re-sent every time and **bounded**, because a server that ignores `startAt` defeats all three stop conditions at once and the unbounded walk was measured at 5.58 GB before it was killed — `JIRA_READ_ONLY`, `--dry-run`, the error mappings and the byte-pinned multipart body |
| `bitbucket_cli` | the Bitbucket Server/DC CLI fully offline with the transport injected: the context-path URL join, the two refusals at the door — an `http://` scheme that would hand the bearer PAT to the network in cleartext, and a userinfo base URL whose refusal is asserted **not** to reproduce the secret — the same-origin redirect handler gated component by component, `BITBUCKET_READ_ONLY` at **both** layers with `WRITE_COMMANDS` compared against the set *measured* by driving every handler through a verb-recording transport rather than against a second typed list, the four merge refusals each measured as **zero requests** rather than asserted as statement order, `pr-builds`' exit code pinned in Markdown **and** `--json` in one case, and byte parity with `jira.py`'s `_profile_path` — a copy that is deliberate and is gated on identity so the eventual unification stays mechanical, which is exactly what it bought: the copy was taken carrying a `$HOME` boundary that bound on neither side, and the identity gate is what forced the repair through **both** files in one change rather than one, with the boundary itself then *driven* through a real symlinked `$HOME` because identical text is not the same claim as identical behaviour |
| `checkpoint` | `checkpoint.py` as a **writer**: `Start`/`End` land on the block and nothing else, the numbers describe the file *after* the region was inserted, `prepend` lands the block and the table it describes in one `os.replace`, a stale or duplicate-id segment is refused on content, and every refusal exits 2 leaving the file alone — [[0009-the-first-reader-is-a-cold-model]] |
| `generated_region` | every live generated region still matches its canonical source, and the source named on a region's `BEGIN` line is the one its names resolve against — plus a behavioural group so a shared helper is unit-tested once rather than only drift-checked — [[generated-regions]] |
| `read_loop` | every server's read loop carries the shape [[0008-a-serialized-read-loop-looks-like-a-dead-server]] decided: a single-thread reader executor no handler can take, and one task per message — with the pool/coroutine split declared per server rather than inferred |
| `wire_log` | wire logging is structure only at both sites — protocol metadata and argument *keys*, never a payload body or value (F12/CWE-532) — with the shape each site may log declared per server, and `Scripts/MCP_SKELETON.md`'s own sample lifted by script and run through the same analyser — [[0011-a-truncated-payload-carries-the-first-cookie]] |
| `handler_crash` | every tool-handler catch-all leaves a traceback at a level the default WARNING configuration emits, at **both** site layers — the `McpServer` wrap, which all fifteen have, and the module-level dispatcher, which nine do — each declared per server, plus one security clause: the format string must be a literal, so a payload cannot be interpolated into the one log that IS written |
| `table_cells` | every table renderer either escapes its own delimiter and documents the scheme where the model reads it, or is whitespace-delimited and has none to escape — one declared row per renderer carrying the class and the reason, the real escapers imported and round-tripped rather than restated, and reversibility as a **separate** clause because an encoder that does not escape its own escape character still passes a column count — [[0016-a-cell-may-not-forge-a-boundary]] |
| `protocol_version` | every server declares the handshake protocol version **once**, as the first member of `class McpServer`, and the `initialize` reply *reads* that member instead of restating the literal — the shape the live smoke handshake structurally cannot see, since a server inlining the **right** string is indistinguishable on the wire from one reading the constant, with the fleet's agreement asserted *between* the files so the suite never holds a copy of the number it polices |
| `smoke` | JSON-RPC plumbing invariants across every server file, including the error-envelope contract ([[scripts]]) |

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

The smoke check is also the fleet's one deliberate exception to the child-env
rule, and the exception is argued rather than inherited: its `Popen` passes no
`env=` and no `-B` `Scripts/_mcp_smoke_test.py`. That is safe by **architecture**
— a server runs as `__main__`, which CPython never caches, and the
generated-region design means it imports no sibling from this repo
[[generated-regions]], so there is nothing here for CPython to write bytecode
for. Measured, not reasoned: a standalone run with `PYTHONDONTWRITEBYTECODE`
unset starts every server in the launch table and leaves the tree bytecode-free.
The note records its own expiry condition — the first server to grow a
repo-local import breaks the premise, and the suites asserting zero absolutely
are what would say so.

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
- **Bytecode discipline comes in two kinds, and they are not interchangeable.**
  `sys.dont_write_bytecode` is set before the first repo import `tests/run.py`,
  and every child process inherits `PYTHONDONTWRITEBYTECODE=1`
  `tests/_harness.py:child_env`. What backs that up at runtime is two different
  assertions. Most snapshotting suites check a **delta** — nothing created,
  nothing touched since the suite started — which stays silent on a file that
  was already there, because it reads as "1 before, 1 after". A smaller set
  asserts **zero, absolutely**, so a pre-existing artifact is a finding no
  matter who wrote it; `tests/test_purity_lsp.py:_pyc_problems` is that check
  with its reasoning attached, and it reports pre-existing files separately from
  newly written ones because the fix differs — delete the litter versus stop
  producing it. Two delta sites carry a note saying which kind they are and
  where the absolute form lives `tests/test_mcp_footprint.py`
  `tests/test_spawn_stdin.py`. A third shape exists in exactly one place:
  `name_existence` pairs its delta FAIL with an INFO row listing stale `.pyc`
  for the three modules it imports — unattributable to this run, but it must
  stay visible `tests/test_name_existence.py`.
- **Neither bytecode set gets a count written down here.** Both move whenever a
  suite is added, and the number this page used to carry was not a measurement
  but one stale ancestor copied three ways — the same wrong "two" sat in
  `project-forge.yaml` and `tests/README.md`. The fleet's sources now state it
  structurally instead: *a tree every suite that snapshots bytecode asserts
  stays empty* `project-forge.yaml` `Scripts/MCP_SKELETON.md`, and
  `tests/README.md` names one example of each kind with no count at all. This
  is also why `py_compile` must never be reintroduced `project-forge.yaml`.
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
