# tests/

Stdlib-only functional test suites for this repo. No pytest, no third-party
dependencies, no build step. Everything is driven from one entry point:

```bash
python3 tests/run.py
```

Works from any working directory — all paths are derived from `__file__`,
never from `os.getcwd()`.

**Inside a Claude Code session, go through forge instead.** The repo root
carries a `project-forge.yaml`, and the standing rule is that when that file
exists, every build/test/clean goes through `forge_call` rather than Bash:

```
forge_call(function="test", params={"targets":["all"]})
forge_call(function="test", params={"targets":["purity_lsp"]})
forge_call(function="test", params={"targets":["all"],"env":{"RUN_ARGS":"mcp_first_guard --whitebox"}})
forge_call(function="clean", params={"targets":["pycache"]})
```

Each test target `requires` the `syntax` build target, so a broken edit fails in
under a second instead of after a full suite. `RUN_ARGS` on the `all` target is
the full surface — suite names and flags both — because `run.py` already takes
those as positional arguments. The `python3` invocations below remain correct
for a plain shell and for CI.

> **This file is the WHAT and HOW. The WHY lives in the wiki.**
> `docs/subsystems/tests.md` carries the design: why severity is argued per rule
> rather than assumed, what a *suppressor* is and what the removed fourth one
> cost, why the harness is two layers, why some counts are declared and others
> derived, and the invariants a newcomer breaks. Read it before changing how a
> suite decides FAIL versus INFO. Keep that reasoning there, not here — two
> documents arguing the same case will drift, and the wiki page is the one a
> lint pass re-checks against the code.

> **Case counts are not written down here.** They live in exactly one place —
> the `SUITES` table in `run.py` — and `run.py` asserts every declared count
> against what the suite actually reported, failing the run on drift. This
> README deliberately names *groups*, not totals: a count duplicated into prose
> goes stale and then gets quoted as fact. That has already happened six times
> in this repo (a commit message advertising "119 cases" for a 121-case suite,
> an unpushed-commit total, a hardcoded fleet size of 16 after the fleet dropped
> to 15, two "10 canonical" comments, and a "13 `luals_*` names" blurb). Run the
> suite; believe the run. The same rule applies to a module docstring — the
> drift gate compares the `SUITES` table against the run and nothing else, so a
> count written anywhere else is unchecked by construction.

## Suites

Eleven registry entries in `run.py`: ten in-process Python suites plus `smoke`,
which runs as a subprocess and reports *servers* rather than cases. Per-suite
coverage detail lives in each module's own docstring; the design rationale lives
in `docs/subsystems/tests.md`.

| name | file | groups |
|---|---|---|
| `inspect_validate` | `test_inspect_validate.py` | A–O |
| `mcp_first_guard` | `test_mcp_first_guard.py` | A–N |
| `sbx_gate` | `test_sbx_gate.py` | A–Q |
| `purity_lsp` | `test_purity_lsp.py` | A–J |
| `purity_file_ops` | `test_purity_file_ops.py` | A–F |
| `mcp_git_params` | `test_mcp_git_params.py` | A–M |
| `name_existence` | `test_name_existence.py` | A–I |
| `spawn_stdin` | `test_spawn_stdin.py` | A–D |
| `mcp_footprint` | `test_mcp_footprint.py` | A–H |
| `wiki_recall` | `test_wiki_recall.py` | A–P |
| `smoke` | `Scripts/_mcp_smoke_test.py` | — |

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
python3 tests/test_sbx_gate.py
python3 tests/test_purity_lsp.py
python3 tests/test_purity_file_ops.py
python3 tests/test_mcp_git_params.py
python3 tests/test_name_existence.py
python3 tests/test_spawn_stdin.py
python3 tests/test_mcp_footprint.py
python3 tests/test_wiki_recall.py
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
and a green summary hides them — the reasoning is in
`docs/subsystems/tests.md`, but operationally:

```bash
python3 tests/run.py <suite> | grep -iE "INFO|SKIP"
```

Note that `SKIP` is not a harness status. Two suites define it locally as a
plain string, and a `SKIP` row with no recorded problems tallies as a **PASS**;
`purity_lsp` instead records its skips as `INFO`. Two conventions, so read a
tally with that in mind.

## Tests never write into the repo tree

Hard rule, enforced rather than hoped for:

* Fixtures are generated from scratch on every run into a
  `tempfile.mkdtemp()` directory (`_harness.TempWorkspace`) and removed
  afterwards. `--keep` retains the directory and prints its path.
* Suites that need scratch *inside* the repo — `name_existence`,
  `spawn_stdin`, `mcp_footprint` — use a per-run
  `.claude/tmp/<suite>/run-<unique>/` directory instead, and that boundary is
  structurally gated: a single write path and a single child launcher record
  every target, and the group fails if any recorded path escapes the sandbox.
  The per-run subdirectory exists because two concurrent instances at a fixed
  path had one instance's teardown deleting the other's fixtures mid-probe.
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
or modified by a test run.

## Layout

```
tests/
  run.py                     single entry point / aggregator + the SUITES table
  _harness.py                shared plumbing, used by every suite
  test_inspect_validate.py   groups A-O
  test_mcp_first_guard.py    groups A-N
  test_sbx_gate.py           groups A-Q   (grant-only gate; the guard's mirror)
  test_purity_lsp.py         groups A-J   (live clangd + lua-language-server)
  test_purity_file_ops.py    groups A-F   (stdlib file handlers, no binary, ~2s)
  test_mcp_git_params.py     groups A-M   (offline, subprocess stubbed)
  test_name_existence.py     groups A-I
  test_spawn_stdin.py        groups A-D   (offline, AST only, nothing spawned)
  test_mcp_footprint.py      groups A-H   (AST + one handshake per server)
  test_wiki_recall.py        groups A-P   (synthetic corpus, offline)
  files/                     tf_-prefixed C and Lua fixtures for purity_lsp
  README.md
```

Everything under `files/` carries a `tf` prefix, and that is an invariant: a
repo-wide search for a real symbol must not land in a fixture.
`files/c/tf_broken.c` and `files/lua/tf_broken.lua` are **deliberately invalid**
— assertions depend on the language servers reporting problems on them. Never
"repair" them.

## Adding a suite

Three edits, once the suite file exists — there is no auto-discovery:

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
change to the very thing it measures. What is typed gets checked; what is
derived gets derived. For those suites the gate is the *invariant*, not the
count: "new spawn site with no explicit stdin at `foo.py:120`" is a far better
error message than "265 != 262".

Once the three edits are in place, `run.py` handles the name, the subset
selection, and the aggregation.
