# tests/

Stdlib-only functional test suites for this repo. No pytest, no third-party
dependencies, no build step. Everything is driven from one entry point:

```bash
python3 tests/run.py
```

Works from any working directory — all paths are derived from `__file__`,
never from `os.getcwd()`.

## Suites

| name | file | what it covers | size |
|---|---|---|---|
| `inspect_validate` | `test_inspect_validate.py` | the `mcp-inspect` VALIDATION family: `validate`, `python`, `json`, `yaml`, `toml`, `xml`, `ini`, `csv`, `tsv`, `plist` and every alias. Valid + invalid fixture per format, reported error **line numbers**, python-depth catches that `ast.parse` misses, PEP-263/BOM/undecodable source encoding, XML entity + XXE rejection, the parameter error matrix, `max_mb` cap semantics, `strict`, batch `paths`, real repo files, hostile-input robustness, and a read-only contract check. | 94 cases, groups A–N |
| `mcp_first_guard` | `test_mcp_first_guard.py` | `ClaudeCode/hooks/mcp-first-guard.py`, the PreToolUse Bash guard: primary-command DENY set, the `tail` follow-mode exemption, downstream-pipe-stage ALLOW, exemption leakage, every pre-existing regression case, heredoc-body stripping, herestring (`<<<`) handling, `&` as a statement separator, hit dedup/sorting, and the plumbing contract (always `exit 0`, ALLOW means empty stdout, malformed input fails open). | 165 cases, groups A–J |
| `smoke` | `Scripts/_mcp_smoke_test.py` | JSON-RPC 2.0 plumbing invariants for every MCP server: `initialize` protocol/version, notifications get no reply, `ping` → `{}`, exactly one tool with the right name, unknown method → `-32601`, forced handler exception → `-32603` with a response actually arriving, plus the `mcp-purity` semantic-dispatch checks. | 16 servers |

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
python3 Scripts/_mcp_smoke_test.py
```

An unknown suite name is a hard error (`exit 2`) that lists the valid names.

## Exit-code contract

* `0` — every selected suite passed.
* `1` — at least one case/server failed. The failing suites are named in the
  `AGGREGATE` block, and each suite prints its own `FAILURES:` detail.
* `2` — bad invocation (unknown suite name).

A case is counted as failing purely on the presence of recorded problems.
`INFO` cases (group H of the guard suite, plus one in group J) record actual
behaviour of deliberately under-specified edge cases and never fail — but an
`INFO` case that breaks a hard invariant (non-zero exit, dirty stderr) still
fails.

Expected green totals: **3 suites, 259 cases, 16 servers, ALL PASS** —
`inspect_validate` 94/94, `mcp_first_guard` 165 cases (150 pass, 15 info),
`smoke` 16/16 servers.

## Special modes

Both are available through `run.py` and on the suite file directly.

```bash
# dump the full server reply for every case whose id contains a substring
python3 tests/run.py inspect_validate --show alias-yml maxmb
python3 tests/test_inspect_validate.py --show valid-json

# print the guard's raw split_top() / _strip_heredocs() output, needed because
# some deny reasons are string-identical regardless of how the command split
python3 tests/run.py mcp_first_guard --whitebox
python3 tests/test_mcp_first_guard.py --whitebox
```

`--show` greedily consumes the following non-flag arguments. Both flags are
forwarded to every selected suite; a suite that does not understand a flag
ignores it, so `python3 tests/run.py --whitebox` is harmless.

## Tests never write into the repo tree

Hard rule, enforced rather than hoped for:

* Fixtures are generated from scratch on every run into a
  `tempfile.mkdtemp()` directory (`_harness.TempWorkspace`) and removed
  afterwards. `--keep` retains the directory and prints its path.
* `_harness` sets `sys.dont_write_bytecode = True` and exports
  `child_env()`, which puts `PYTHONDONTWRITEBYTECODE=1` into the environment
  of **every** child process, so neither the runner nor any server/hook it
  spawns can drop a `.pyc` anywhere under the repo.
* `--whitebox` needs to import the hook module; it goes through
  `_harness.load_module_from_path()`, which pins `dont_write_bytecode` for the
  duration of the import.
* Group D of `inspect_validate` asserts this at runtime: no new `__pycache__`
  entry, no touched `__pycache__` entry, and every fixture byte-identical
  after the run.

Nothing under `Scripts/`, `ClaudeCode/`, `tests/` or the repo root is created
or modified by a test run.

## Layout

```
tests/
  run.py                     single entry point / aggregator
  _harness.py                shared plumbing, used by BOTH ported suites
  test_inspect_validate.py   94 cases, groups A-N
  test_mcp_first_guard.py    165 cases, groups A-J
  README.md
```

`_harness.py` has two deliberately separate layers, because the two suites do
not speak the same protocol and must not be forced through one abstraction:

* **Layer 1 — protocol agnostic, used by every suite.** `Options` /
  `parse_options` (the shared flag surface), `Result` / `Suite` (case model,
  per-group and total PASS/FAIL/INFO tallies, streamed or grouped rendering,
  failure dump, exit code), `SuiteReport` (what `run.py` aggregates),
  `TempWorkspace`, `run_process` (one-shot "run this argv with this stdin →
  `(rc, stdout, stderr)`"), `load_module_from_path`, `pycache_snapshot`,
  `file_digests` / `sha256_file`.
* **Layer 2 — only for suites that drive an MCP server.** `JsonRpcClient`:
  one long-lived `python3 Scripts/mcp-*.py` child, `initialize` +
  `tools/call`, line-delimited reads with a real timeout. The guard suite
  never touches this — it only needs the one-shot `run_process` helper.

## Adding a suite

Three lines, once the suite file exists:

1. Write `tests/test_<name>.py` exposing `run(opts) -> _harness.Suite`
   (record cases with `suite.record(group, cid, problems, ...)`), plus a
   `main(argv=None)` for standalone use.
2. Add a runner one-liner in `run.py`:
   `def run_<name>(opts): return run_python_suite("test_<name>", opts)`
3. Add its entry to the `SUITES` list in `run.py`:
   `("<name>", run_<name>, "one-line description")`

`run.py` picks up the name, the subset selection, and the aggregation
automatically.

## Why `Scripts/_mcp_smoke_test.py` is still standalone

It predates this directory and its exact path is referenced from roughly
fifteen places across the repo's docs and instructions, so moving, renaming or
refactoring it would be a documented-interface regression. It is therefore
left byte-for-byte alone: `run.py` invokes it as a subprocess with
`sys.executable`, consumes its exit code, and surfaces its output only when it
fails. It also reports *servers*, not cases, so the aggregate counts it
honestly as `16 servers` rather than inventing a case count for it.
