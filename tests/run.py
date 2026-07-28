#!/usr/bin/env python3
"""Single entry point for the prompt-heaven test suites.

Registered suites:
  inspect_validate   tests/test_inspect_validate.py   mcp-inspect VALIDATION
                                                      family, 94 cases (A-N)
  mcp_first_guard    tests/test_mcp_first_guard.py    ClaudeCode/hooks/
                                                      mcp-first-guard.py PreToolUse
                                                      Bash guard, 165 cases (A-J)
  purity_lsp         tests/test_purity_lsp.py         purity_call semantic
                                                      navigation vs the retired
                                                      mcp-clangd / mcp-luals
                                                      servers (A-I)
  smoke              Scripts/_mcp_smoke_test.py       JSON-RPC plumbing invariants
                                                      across 16 MCP servers

`purity_lsp` drives live clangd / lua-language-server children, so it is the
slow one (~2.5 min): purity blocks the first semantic call on a full backend
handshake whose indexing wait times out at 60s in a repo with no
compile_commands.json.  It SKIPs cleanly when those binaries are absent.

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

Exit code 0 iff every selected suite passed.  Works from ANY working directory:
paths are derived from __file__, never from os.getcwd().

Adding a suite takes three lines: write tests/test_<x>.py exposing
`run(opts) -> Suite`, then add one SUITES entry naming it.
"""

import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

SMOKE = H.repo_path("Scripts", "_mcp_smoke_test.py")
SMOKE_SERVERS = 16


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


# name -> (runner, one-line description)
SUITES = [
    ("inspect_validate", run_inspect_validate,
     "mcp-inspect VALIDATION family (94 cases)"),
    ("mcp_first_guard", run_mcp_first_guard,
     "mcp-first-guard PreToolUse Bash hook (165 cases)"),
    ("purity_lsp", run_purity_lsp,
     "purity_call semantic navigation: clangd + luals absorption"),
    ("smoke", run_smoke,
     "MCP JSON-RPC plumbing invariants (16 servers)"),
]

SUITE_NAMES = [name for name, _runner, _desc in SUITES]


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
    for name, runner, desc in selected:
        print("\n" + H.BANNER)
        print("SUITE %s -- %s" % (name, desc))
        print(H.BANNER)
        reports.append(runner(opts))

    total_cases = sum(r.count for r in reports if r.unit == "cases")
    total_pass = sum(r.passed for r in reports if r.unit == "cases")
    total_fail = sum(r.failed for r in reports if r.unit == "cases")
    total_info = sum(r.info for r in reports if r.unit == "cases")
    total_servers = sum(r.count for r in reports if r.unit == "servers")
    ok = all(r.ok for r in reports)

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
    if not ok:
        print("\nFAILING SUITES: %s"
              % ", ".join(r.name for r in reports if not r.ok))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
