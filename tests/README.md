# tests/

Stdlib-only functional test suites for this repo. No pytest, no third-party
dependencies, no build step. Everything is driven from one entry point:

```bash
python3 tests/run.py
```

Works from any working directory — all paths are derived from `__file__`,
never from `os.getcwd()`.

**Inside a Claude Code session, go through forge instead.** The repo root now
carries a `project-forge.yaml`, and the standing rule is that when that file
exists, every build/test/clean goes through `forge_call` rather than Bash:

```
forge_call(function="test", params={"targets":["all"]})
forge_call(function="test", params={"targets":["purity_lsp"]})
forge_call(function="test", params={"targets":["all"],"env":{"RUN_ARGS":"mcp_first_guard --whitebox"}})
forge_call(function="clean", params={"targets":["pycache"]})
```

Each test target `requires` the `syntax` build target, so a broken edit fails in
under a second instead of after a 45-second suite. `RUN_ARGS` on the `all` target
is the full surface — suite names and flags both — because `run.py` already takes
those as positional arguments. The `python3` invocations below remain correct for
a plain shell and for CI.

> **Case counts are not written down here.** They live in exactly one place —
> the `SUITES` table in `run.py` — and `run.py` asserts every declared count
> against what the suite actually reported, failing the run on drift. This
> README deliberately names *groups*, not totals: a count duplicated into prose
> goes stale and then gets quoted as fact. That has already happened six times
> in this repo (a commit message advertising "119 cases" for a 121-case suite, a
> hardcoded fleet size of 16 after the fleet dropped to 15, two "10 canonical"
> comments, and a "13 `luals_*` names" blurb). Run the suite; believe the run.

## Suites

| name | file | what it covers | groups |
|---|---|---|---|
| `inspect_validate` | `test_inspect_validate.py` | the `mcp-inspect` VALIDATION family: `validate`, `python`, `json`, `yaml`, `toml`, `xml`, `ini`, `csv`, `tsv`, `plist` and every alias. Valid + invalid fixture per format, reported error **line numbers**, python-depth catches that `ast.parse` misses, PEP-263/BOM/undecodable source encoding, XML entity + XXE rejection, the parameter error matrix, `max_mb` cap semantics, `strict`, batch `paths`, real repo files, hostile-input robustness, and a read-only contract check. | A–N |
| `mcp_first_guard` | `test_mcp_first_guard.py` | `ClaudeCode/hooks/mcp-first-guard.py`, the PreToolUse Bash guard: primary-command DENY set, the `tail` follow-mode exemption, downstream-pipe-stage ALLOW, exemption leakage, every pre-existing regression case, heredoc-body stripping, herestring (`<<<`) handling, `&` as a statement separator, hit dedup/sorting, the wrapper-peeling forms (subshell, brace group, command substitution, `bash -c`), the ALL-CAPS fold for blocked names, python interpreters **and** shells (`BASH -c 'cat f'`), `python3 -m py_compile` / `-m compileall` including bundled short-option clusters, the `node --check` / `node -c` syntax-check redirect (gated on `node` being installed, with `node file.js` / `-e` / `-p` / `--version` / npm/npx asserted untouched), and the plumbing contract (always `exit 0`, ALLOW means empty stdout, malformed input fails open). | A–N |
| `purity_lsp` | `test_purity_lsp.py` | `purity_call`'s semantic navigation, i.e. whether absorbing `mcp-clangd` / `mcp-luals` into `mcp-purity` actually preserved their capability: definitions, references asserted to the exact site set, hover, outline, diagnostics on a clean **and** a deliberately broken fixture, every prefixed alias, type-definition navigation, and A/B comparison against the still-on-disk retired servers so "purity returns nothing" can be told apart from "there was nothing to return". Also the hygiene group: no repo writes, no bytecode, fixtures byte-identical, plus measured warm-up latencies. | A–I |
| `purity_file_ops` | `test_purity_file_ops.py` | `purity_call`'s gitignore-aware **file** handlers (no LSP, no external binary, ~2 s). Two behaviours that pull in opposite directions: `.claude/tmp` is never skipped — it is gitignored on purpose, yet it is where the fleet drops artifacts the next `search` goes looking for — *and* that exemption must stay **narrow**. Purity has no gitignore inheritance of its own (an ignored directory is enforced by pruning the walk), so un-pruning `.claude` as a gateway to `.claude/tmp` initially handed out the gateway's other children too; `_ignore_inherited` restores what pruning used to deliver. **Group B is that shipped defect** — a sibling subtree and a file sitting directly in the gateway dir must both stay invisible, and the group carries an anti-vacuity control that re-asserts every one of its negatives as *visible* under `skip_ignored_files:false`. Plus `list_dir` on both branches and both settings of that flag (its default is `False`, the opposite of `search`'s `True`), and the search param contract: the per-function `query` alias, `regex`/`line_numbers` tolerated as true-only no-ops, a genuinely unknown param still rejected, and `symbol` keeping its own `query`. Group E records — as `INFO`, because it is a limitation and not a guarantee — that `_is_ignored` is fed a bare basename at both search sites, making a slash-bearing pattern like this repo's own `.claude/tmp` inert there; its fixture carries one pattern of each shape so the asymmetry shows up in a single `.gitignore`. | A–F |
| `mcp_git_params` | `test_mcp_git_params.py` | `mcp-git`'s named-param → `git` argv conversion, fully offline (the module's `subprocess` is stubbed, so nothing is spawned): the revision/pathspec/repository positional slots and their aliases, leading-dash rejection per list element, flag-vs-positional ordering, the deliberate `--key=value` fall-through pinned as a trap, and display fidelity of the echoed command line — checked three ways, by exact rendering, by a `shlex` round-trip, and by replaying the line under real `bash`/`zsh`/`sh`. | A–I |
| `name_existence` | `test_name_existence.py` | the three-way name check: every MCP server and function name that the prompt corpus (`ClaudeCode/**`) **or** a server's own model-facing tool description prescribes must exist in the live inventory and name a *registered* server; every server function nobody references anywhere is reported as an orphan; and every tool an agent's own body tells it to call must appear in that agent's frontmatter `tools:` grant (with the mirror check — a granted tool that does not exist — and the orphan-capability check, a registered dispatcher no agent may call at all). Includes a negative control with planted defects, because a detector that never fires is worthless. | A–I |
| `mcp_footprint` | `test_mcp_footprint.py` | what the fleet costs in tokens, in three parts: the **description tax** (a connected server's `tools/list` reply is resent on *every* request for the whole session, measured over a real JSON-RPC handshake), the **result ceiling** (what one call may dump into the transcript — AST-derived per server: cap param, default, alias table, pagination knobs, hardcoded constants) and the **boilerplate** a handler emits regardless of content. Sums are taken over the **registered** servers only, because an unregistered server file is never started and its footprint is exactly zero — `SERVERS` enumerates *files*, and conflating those two sets has already produced one wrong fleet-wide conclusion here; group F cross-checks the launch table's `registered` flags against the live `~/.claude.json`. **A measuring tape, not a gate:** every finding about a server is `INFO`. What *is* gated is the suite's own integrity — the negative control (planted ceilings it must classify and description/docstring bait it must not), a probe floor, and sandbox hygiene. | A–G |
| `spawn_stdin` | `test_spawn_stdin.py` | every subprocess spawn site under `Scripts/` must pass an explicit `stdin=`. An MCP server's stdin **is** the JSON-RPC stream, so a child that inherits it eats protocol messages — one such site swallowed a `ping` and the reply never came, another desynced the stream mid-message. **AST-based, never regex** (this repo really contains both regex false positives: a `subprocess.Popen` type annotation and a docstring naming `subprocess.run()`), and the assertion is *explicitness*, not a particular value — `DEVNULL`, `PIPE`, a variable or a raw fd all pass, a missing keyword fails. Also fails outright on the forms that cannot take `stdin=` at all (`os.system`, `os.popen`, `subprocess.getoutput`, `subprocess.getstatusoutput`; currently zero, now checked rather than assumed). `ClaudeCode/**` and `tests/**` are surveyed as INFO rather than gated — see the suite docstring for why. Carries a negative control with planted defects, including a multi-line call a regex would miss and an unparseable file that must be reported rather than skipped. | A–D |
| `smoke` | `Scripts/_mcp_smoke_test.py` | JSON-RPC 2.0 plumbing invariants for every MCP server: `initialize` protocol/version, notifications get no reply, `ping` → `{}`, exactly one tool with the right name, unknown method → `-32601`, forced handler exception → `-32603` with a response actually arriving, plus the `mcp-purity` semantic-dispatch checks. Reports *servers*, not cases; the fleet size is derived from its own launch table. | — |

## Commands

```bash
python3 tests/run.py                        # every suite
python3 tests/run.py inspect_validate       # one suite
python3 tests/run.py mcp_first_guard smoke  # a subset
python3 tests/run.py --brief                # terse one-line-per-case output
python3 tests/run.py --keep                 # keep the generated fixture dirs
python3 tests/run.py --help                 # usage + valid suite names
```

Every suite file is also runnable standalone, with the same exit-code contract:

```bash
python3 tests/test_inspect_validate.py
python3 tests/test_mcp_first_guard.py
python3 tests/test_purity_lsp.py
python3 tests/test_purity_file_ops.py
python3 tests/test_mcp_git_params.py
python3 tests/test_name_existence.py
python3 tests/test_spawn_stdin.py
python3 tests/test_mcp_footprint.py
python3 Scripts/_mcp_smoke_test.py
```

An unknown suite name is a hard error (`exit 2`) that lists the valid names.

`purity_lsp` is the slow one (~45 s): it drives live `clangd` and
`lua-language-server` children through a real handshake. It SKIPs gracefully
when those binaries are absent, so a machine without the toolchain still exits
0. Everything else finishes in seconds.

## Exit-code contract

* `0` — every selected suite passed and no declared case count drifted.
* `1` — at least one case/server failed, or a suite's declared count in
  `SUITES` no longer matches its run. Failing suites are named in the
  `AGGREGATE` block; drift is reported separately with the declared and actual
  numbers.
* `2` — bad invocation (unknown suite name).

A case is counted as failing purely on the presence of recorded problems.
`INFO` cases record the actual behaviour of deliberately under-specified edge
cases and never fail — but an `INFO` case that breaks a hard invariant
(non-zero exit, dirty stderr) still fails.

**Read the `INFO` lines.** They are the most interesting output in the suite,
and a green summary hides them: the guard suite's group H pins wrapper bypasses
that are knowingly left open, `purity_lsp` group G records capability gaps, and
`name_existence` reports orphaned functions nobody documents, orphaned
*capabilities* (registered dispatchers no agent's `tools:` grants — that row is
what surfaced `inspect_call`), and the missing grants it deliberately declines to
fail on. `spawn_stdin`'s group B is INFO-only by design: it is the measured list
of spawn sites outside the gated tree that still inherit stdin, kept visible so
widening the gate later is a decision on printed data rather than a fresh audit.
And `mcp_footprint` is *almost entirely* INFO on purpose — it is a measuring tape
for this round, so its description-tax table, its per-server ceiling verdicts and
its registry-drift comparison are all readings, not verdicts. Skipping them and
trusting the green summary is skipping the entire point of that suite.
To see them:

```bash
python3 tests/run.py <suite> | grep -iE "INFO|SKIP"
```

## Tests never write into the repo tree

Hard rule, enforced rather than hoped for:

* Fixtures are generated from scratch on every run into a
  `tempfile.mkdtemp()` directory (`_harness.TempWorkspace`) and removed
  afterwards. `--keep` retains the directory and prints its path.
* `_harness` sets `sys.dont_write_bytecode = True` and exports
  `child_env()`, which puts `PYTHONDONTWRITEBYTECODE=1` into the environment
  of **every** child process, so neither the runner nor any server/hook it
  spawns can drop a `.pyc` anywhere under the repo. `run.py` sets the same flag
  before its own first repo import.
* `--whitebox` needs to import the hook module; it goes through
  `_harness.load_module_from_path()`, which pins `dont_write_bytecode` for the
  duration of the import.
* Group D of `inspect_validate` and group I of `purity_lsp` assert this at
  runtime. Both assert **zero** `.pyc`, not "the count did not change": a file
  that already existed when the run started reads as "1 before, 1 after" and
  sails straight through a delta check, which is exactly how one hid under
  `Scripts/__pycache__` until it was found by hand.

Nothing under `Scripts/`, `ClaudeCode/`, `tests/` or the repo root is created
or modified by a test run. Scratch, when a suite needs any, goes under
`.claude/tmp/` — never `/tmp`, which is shared with every other process on the
machine and invisible to `git status`.

## Layout

```
tests/
  run.py                     single entry point / aggregator + the SUITES table
  _harness.py                shared plumbing, used by every suite
  test_inspect_validate.py   groups A-N
  test_mcp_first_guard.py    groups A-N
  test_purity_lsp.py         groups A-I   (live clangd + lua-language-server)
  test_purity_file_ops.py    groups A-F   (stdlib file handlers, no binary, ~2s)
  test_mcp_git_params.py     groups A-I   (offline, subprocess stubbed)
  test_name_existence.py     groups A-I
  test_spawn_stdin.py        groups A-D   (offline, AST only, nothing spawned)
  test_mcp_footprint.py      groups A-G   (AST + one handshake per server)
  files/                     tf_-prefixed C and Lua fixtures for purity_lsp
  README.md
```

Everything under `files/` carries a `tf` prefix, and that is an invariant: a
repo-wide search for a real symbol must not land in a fixture. `files/c/tf_broken.c`
and `files/lua/tf_broken.lua` are **deliberately invalid** — assertions depend on
the language servers reporting problems on them. Never "repair" them.

`_harness.py` has two deliberately separate layers, because the suites do not
all speak the same protocol and must not be forced through one abstraction:

* **Layer 1 — protocol agnostic, used by every suite.** `Options` /
  `parse_options` (the shared flag surface), `Result` / `Suite` (case model,
  per-group and total PASS/FAIL/INFO tallies, streamed or grouped rendering,
  failure dump, exit code), `SuiteReport` (what `run.py` aggregates),
  `TempWorkspace`, `run_process` (one-shot "run this argv with this stdin →
  `(rc, stdout, stderr)`"), `load_module_from_path`, `pycache_snapshot`,
  `file_digests` / `sha256_file`.
* **Layer 2 — only for suites that drive an MCP server.** `JsonRpcClient`:
  one long-lived `python3 Scripts/mcp-*.py` child, `initialize` +
  `tools/call`, line-delimited reads with a real timeout. The guard and
  param suites never touch this — they only need `run_process`, or nothing at
  all.

## Adding a suite

Three lines, once the suite file exists:

1. Write `tests/test_<name>.py` exposing `run(opts) -> _harness.Suite`
   (record cases with `suite.record(group, cid, problems, ...)`), plus a
   `main(argv=None)` for standalone use.
2. Add a runner one-liner in `run.py`:
   `def run_<name>(opts): return run_python_suite("test_<name>", opts)`
3. Add its entry to the `SUITES` list in `run.py`:
   `("<name>", run_<name>, "one-line description", <case count>)`

Declare the case count so drift fails loudly. Pass `None` instead **only** when
the count is data-derived rather than a fixed case table — `name_existence`
generates one case per name found in the corpus, `spawn_stdin` one case per
spawn site found under `Scripts/`, and `mcp_footprint` several per server in the
launch table, so pinning any of those totals would fail on every legitimate
change to the very thing it measures. What is typed gets checked; what is derived
gets derived. For those suites the gate is the *invariant*, not the count: "new
spawn site with no explicit stdin at `foo.py:120`" is a far better error message
than "265 != 262".

`run.py` picks up the name, the subset selection, and the aggregation
automatically.

## Why `Scripts/_mcp_smoke_test.py` is still standalone

It predates this directory and its exact path is referenced from roughly
fifteen places across the repo's docs and instructions, so moving, renaming or
refactoring it would be a documented-interface regression. Its *interface* is
therefore frozen: `run.py` invokes it as a subprocess with `sys.executable`,
consumes its exit code, and surfaces its output only when it fails. It also
reports *servers*, not cases, so the aggregate counts it honestly as servers
rather than inventing a case count for it — and `run.py` derives that number by
importing the harness's own `SERVERS` launch table instead of keeping a copy that
can rot.

Frozen interface is not the same as a frozen file, and the `SERVERS` table has
since gained one field. Each entry now carries an explicit
`registered: True/False`, recording whether Claude Code actually launches that
server (i.e. whether `~/.claude.json` has an `mcpServers` entry for it) as
opposed to merely whether the file exists. The table enumerates server *files*,
those two sets are **not** equal — four files are currently inert — and treating
them as one has already produced a wrong fleet-wide conclusion here, so the
distinction is now written down per entry instead of being re-derived badly each
time. `mcp_footprint` sums its totals over the flag and cross-checks it against
the live registration; the smoke harness itself ignores the flag on purpose,
since its job is that every server file still speaks the protocol, registered or
not.
