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
| `mcp_first_guard` | `test_mcp_first_guard.py` | `ClaudeCode/hooks/mcp-first-guard.py`, the PreToolUse Bash guard: primary-command DENY set, the `tail` follow-mode exemption, downstream-pipe-stage ALLOW, exemption leakage, every pre-existing regression case, heredoc-body stripping, herestring (`<<<`) handling, `&` as a statement separator, hit dedup/sorting, and the plumbing contract (always `exit 0`, ALLOW means empty stdout, malformed input fails open). | A–J |
| `purity_lsp` | `test_purity_lsp.py` | `purity_call`'s semantic navigation, i.e. whether absorbing `mcp-clangd` / `mcp-luals` into `mcp-purity` actually preserved their capability: definitions, references asserted to the exact site set, hover, outline, diagnostics on a clean **and** a deliberately broken fixture, every prefixed alias, type-definition navigation, and A/B comparison against the still-on-disk retired servers so "purity returns nothing" can be told apart from "there was nothing to return". Also the hygiene group: no repo writes, no bytecode, fixtures byte-identical, plus measured warm-up latencies. | A–I |
| `mcp_git_params` | `test_mcp_git_params.py` | `mcp-git`'s named-param → `git` argv conversion, fully offline (the module's `subprocess` is stubbed, so nothing is spawned): the revision/pathspec/repository positional slots and their aliases, leading-dash rejection per list element, flag-vs-positional ordering, the deliberate `--key=value` fall-through pinned as a trap, and display fidelity of the echoed command line — checked three ways, by exact rendering, by a `shlex` round-trip, and by replaying the line under real `bash`/`zsh`/`sh`. | A–I |
| `name_existence` | `test_name_existence.py` | the bidirectional name check: every MCP server and function name that the prompt corpus (`ClaudeCode/**`) **or** a server's own model-facing tool description prescribes must exist in the live inventory and name a *registered* server — and every server function nobody references anywhere is reported as an orphan. Includes a negative control with planted defects, because a detector that never fires is worthless. | A–H |
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
python3 tests/test_mcp_git_params.py
python3 tests/test_name_existence.py
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
`name_existence` reports orphaned functions nobody documents. To see them:

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
  test_mcp_first_guard.py    groups A-J
  test_purity_lsp.py         groups A-I   (live clangd + lua-language-server)
  test_mcp_git_params.py     groups A-I   (offline, subprocess stubbed)
  test_name_existence.py     groups A-H
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
generates one case per name found in the corpus, so pinning its total would
fail on every legitimate change to the very thing it measures.

`run.py` picks up the name, the subset selection, and the aggregation
automatically.

## Why `Scripts/_mcp_smoke_test.py` is still standalone

It predates this directory and its exact path is referenced from roughly
fifteen places across the repo's docs and instructions, so moving, renaming or
refactoring it would be a documented-interface regression. It is therefore
left byte-for-byte alone: `run.py` invokes it as a subprocess with
`sys.executable`, consumes its exit code, and surfaces its output only when it
fails. It also reports *servers*, not cases, so the aggregate counts it
honestly as servers rather than inventing a case count for it — and `run.py`
derives that number by importing the harness's own `SERVERS` launch table
instead of keeping a copy that can rot.
