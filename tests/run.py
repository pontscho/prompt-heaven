#!/usr/bin/env python3
"""Single entry point for the prompt-heaven test suites.

Registered suites (see SUITES below -- that table is the only place a case
count is written down, and every written-down count is checked against the
run):

  inspect_validate   tests/test_inspect_validate.py   mcp-inspect VALIDATION
                                                      family (A-O)
  mcp_first_guard    tests/test_mcp_first_guard.py    ClaudeCode/hooks/
                                                      mcp-first-guard.py PreToolUse
                                                      Bash guard (A-M)
  purity_lsp         tests/test_purity_lsp.py         purity_call semantic
                                                      navigation vs the retired
                                                      mcp-clangd / mcp-luals
                                                      servers (A-J)
  mcp_git_params     tests/test_mcp_git_params.py     mcp-git named params ->
                                                      git argv, fully offline
                                                      (A-L)
  name_existence     tests/test_name_existence.py     every MCP name the prompt
                                                      corpus and the servers'
                                                      own model-facing text
                                                      prescribe must exist in
                                                      the live inventory, and
                                                      every tool an agent is told
                                                      to call must be in its
                                                      `tools:` grant (A-I)
  spawn_stdin        tests/test_spawn_stdin.py        every subprocess spawn
                                                      site under Scripts/ must
                                                      pass an explicit `stdin=`,
                                                      because an MCP server's
                                                      stdin IS the JSON-RPC
                                                      stream (AST-based, A-D)
  mcp_footprint      tests/test_mcp_footprint.py      what the fleet costs in
                                                      tokens: the permanent
                                                      tools/list description
                                                      tax, the per-call result
                                                      ceiling, and the fixed
                                                      boilerplate -- summed over
                                                      the REGISTERED servers,
                                                      not the file set (A-G)
  wiki_recall        tests/test_wiki_recall.py        the mcp-wiki `search`
                                                      relevance gate on a
                                                      synthetic corpus that
                                                      reproduces the measured
                                                      pathologies: silence, the
                                                      MEASURED calibration
                                                      window, floored
                                                      percentages, and the
                                                      query-side stopword drop
                                                      -- plus `get_page`'s
                                                      section index and
                                                      `source_to_pages`' per-hit
                                                      description, each on its
                                                      own separate fixture (A-K)
  smoke              Scripts/_mcp_smoke_test.py       JSON-RPC plumbing
                                                      invariants across the
                                                      whole server fleet

`purity_lsp` is the slow one (~45 s): it drives live clangd /
lua-language-server children through a real handshake.  It SKIPs cleanly when
those binaries are absent.  (It used to take ~2.5 min because each backend's
init blocked on an indexing-progress event nobody was in a position to receive,
so the 60 s deadline could only expire -- see that suite's module docstring for
the full causal chain, which has three links, not one.  The wait is now gated on
indexing announcing itself, the announcement-wait runs CONCURRENTLY with the
priming that provokes it, and an idle watchdog releases a wedged indexer.)

`smoke` is deliberately left standalone -- its path is referenced from ~15
places in the repo docs, so it is invoked here as a subprocess and only its
exit code (plus its output on failure) is consumed.  It reports SERVERS, not
cases, and is counted as such.

Usage:
  python3 tests/run.py                       # every suite
  python3 tests/run.py inspect_validate      # one or more suites by name
  python3 tests/run.py mcp_first_guard smoke
  python3 tests/run.py --show valid-json     # forwarded to inspect_validate
  python3 tests/run.py --whitebox            # forwarded to mcp_first_guard
  python3 tests/run.py --keep                # keep generated fixture dirs
  python3 tests/run.py --brief               # terse per-case lines

Exit code 0 iff every selected suite passed AND no declared case count drifted.
Works from ANY working directory: paths are derived from __file__, never from
os.getcwd().

Adding a suite takes three lines: write tests/test_<x>.py exposing
`run(opts) -> Suite`, then add one SUITES entry naming it and declaring its
case count (or None if the count is data-derived rather than a fixed table).
"""

import os
import sys

# Must precede every repo import: several suites assert that a run leaves no
# .pyc behind, and importing a repo module is exactly how one would appear.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

SMOKE = H.repo_path("Scripts", "_mcp_smoke_test.py")

# The fleet size is DERIVED from the smoke harness's own launch table, never
# typed here -- a hand-maintained copy of this number was wrong within a day of
# being written (it said 16 after the table dropped to 15).  The module is
# import-safe: SERVERS is module-level and main() is behind an __main__ guard.
sys.path.insert(0, H.repo_path("Scripts"))
import _mcp_smoke_test as _smoke_mod  # noqa: E402

SMOKE_SERVERS = len(_smoke_mod.SERVERS)


def run_python_suite(module_name, opts):
    """Import tests/<module_name>.py and run it in-process."""
    module = __import__(module_name)
    return module.run(opts).report()


def run_inspect_validate(opts):
    return run_python_suite("test_inspect_validate", opts)


def run_mcp_first_guard(opts):
    return run_python_suite("test_mcp_first_guard", opts)


def run_purity_lsp(opts):
    return run_python_suite("test_purity_lsp", opts)


def run_mcp_git_params(opts):
    return run_python_suite("test_mcp_git_params", opts)


def run_name_existence(opts):
    return run_python_suite("test_name_existence", opts)


def run_spawn_stdin(opts):
    return run_python_suite("test_spawn_stdin", opts)


def run_mcp_footprint(opts):
    return run_python_suite("test_mcp_footprint", opts)


def run_wiki_recall(opts):
    return run_python_suite("test_wiki_recall", opts)


def run_smoke(opts):
    """Invoke the standalone smoke harness as a subprocess; parse its rc."""
    rc, out, err = H.run_process([sys.executable, SMOKE], timeout=300,
                                 cwd=H.REPO_ROOT)
    ok = rc == 0
    if not ok:
        print(out, end="" if out.endswith("\n") else "\n")
        if err.strip():
            print("--- smoke stderr ---")
            print(err.rstrip())
    else:
        print("  Scripts/_mcp_smoke_test.py -> rc=0, RESULT: ALL PASS")
    return H.SuiteReport("smoke", ok, SMOKE_SERVERS, unit="servers",
                         output=out, note="rc=%d" % rc)


# name -> (runner, one-line description, declared case count)
#
# The count is the SINGLE place this number is written down: the banner prints
# it, and main() asserts it against what the suite actually reported.  A count
# that lives in prose and is checked by nobody is a lie waiting to happen --
# this repo shipped six of them (a commit message claiming "119 cases" for a
# 121-case suite, an unpushed-commit total, two "10 canonical" comments, a
# "13 luals_* names" blurb, and a hardcoded fleet size of 16).
#
# None means the count is DATA-DERIVED, not a fixed case table, so pinning it
# would fail on every legitimate change to the thing being measured:
#   - smoke        reports servers, and that number is derived above
#   - name_existence generates one case per name found in the corpus, so it
#     moves whenever the corpus or the server inventory moves -- which is the
#     entire point of the suite
#   - spawn_stdin  enumerates FILES and emits one case per spawn site found, so
#     its count moves whenever a server gains or loses a subprocess call.  The
#     gate there is the INVARIANT (every site passes an explicit stdin=), and a
#     drift line reading "265 != 262" would be a strictly worse error message
#     than "new spawn site with no explicit stdin at foo.py:120".  What is typed
#     gets checked; what is derived gets derived.
#   - mcp_footprint emits several cases PER SERVER (description tax, result
#     ceiling, boilerplate), so its count is a multiple of the launch-table size
#     -- the same derived quantity SMOKE_SERVERS above refuses to hardcode.
#     Adding or retiring a server would trip a typed count for no defect.
SUITES = [
    ("inspect_validate", run_inspect_validate,
     "mcp-inspect VALIDATION family", 119),
    ("mcp_first_guard", run_mcp_first_guard,
     "mcp-first-guard PreToolUse Bash hook", 308),
    ("purity_lsp", run_purity_lsp,
     "purity_call semantic navigation: clangd + luals absorption", 152),
    ("mcp_git_params", run_mcp_git_params,
     "mcp-git named params -> git argv, offline", 209),
    ("name_existence", run_name_existence,
     "corpus + server text <-> live MCP inventory name existence", None),
    ("spawn_stdin", run_spawn_stdin,
     "explicit stdin= at every subprocess spawn site", None),
    ("mcp_footprint", run_mcp_footprint,
     "MCP fleet token footprint: description tax, result ceilings, boilerplate",
     None),
    ("wiki_recall", run_wiki_recall,
     "mcp-wiki search relevance gate: silence, measured calibration window, "
     "floored percentages, query-side stopwords, get_page section index, "
     "source_to_pages per-hit description", 60),
    ("smoke", run_smoke,
     "MCP JSON-RPC plumbing invariants across the fleet", None),
]

SUITE_NAMES = [name for name, _runner, _desc, _count in SUITES]


def usage():
    print(__doc__)
    print("Valid suite names: %s" % ", ".join(SUITE_NAMES))


def main(argv=None):
    opts = H.parse_options(argv)
    if opts.help:
        usage()
        return 0

    unknown = [n for n in opts.names if n not in SUITE_NAMES]
    if unknown:
        print("error: unknown suite name(s): %s" % ", ".join(unknown),
              file=sys.stderr)
        print("valid names: %s" % ", ".join(SUITE_NAMES), file=sys.stderr)
        return 2

    selected = [s for s in SUITES if not opts.names or s[0] in opts.names]

    reports = []
    drift = []
    for name, runner, desc, declared in selected:
        label = desc if declared is None else "%s (%d cases)" % (desc, declared)
        print("\n" + H.BANNER)
        print("SUITE %s -- %s" % (name, label))
        print(H.BANNER)
        report = runner(opts)
        reports.append(report)
        if declared is not None and report.count != declared:
            drift.append((name, declared, report.count))

    total_cases = sum(r.count for r in reports if r.unit == "cases")
    total_pass = sum(r.passed for r in reports if r.unit == "cases")
    total_fail = sum(r.failed for r in reports if r.unit == "cases")
    total_info = sum(r.info for r in reports if r.unit == "cases")
    total_servers = sum(r.count for r in reports if r.unit == "servers")
    ok = all(r.ok for r in reports) and not drift

    print("\n" + H.BANNER)
    print("AGGREGATE")
    print(H.BANNER)
    for report in reports:
        print("  " + report.line())
    bits = ["%d suite%s" % (len(reports), "" if len(reports) == 1 else "s")]
    if total_cases:
        bits.append("%d cases" % total_cases)
    if total_servers:
        bits.append("%d servers" % total_servers)
    print("  %s -- %s" % (", ".join(bits), "ALL PASS" if ok else "FAILURES"))
    if total_cases:
        print("  cases: %d pass, %d fail, %d info" % (total_pass, total_fail,
                                                      total_info))
    if drift:
        print("\nCASE COUNT DRIFT -- a declared count no longer matches the run:")
        for name, declared, actual in drift:
            print("  %-18s declared %d, ran %d" % (name, declared, actual))
        print("  Fix the number in the SUITES table in this file (it is the")
        print("  only place it is written down), then re-run.  This is a hard")
        print("  failure on purpose: a stale count gets quoted as fact.")
    if not ok:
        failing = [r.name for r in reports if not r.ok]
        if failing:
            print("\nFAILING SUITES: %s" % ", ".join(failing))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
