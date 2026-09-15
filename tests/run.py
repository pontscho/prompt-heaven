#!/usr/bin/env python3
"""Single entry point for the prompt-heaven test suites.

The registered suites are the SUITES table below, and that table is the only
place any of it is written down: the name, the one-line subject, and the
declared case count all live in the same tuple, and every written-down count is
checked against the run.

This docstring used to carry a second copy of that roster -- name, file and
description, hand-wrapped, ninety lines of it. It listed thirteen of the
seventeen suites by the time anyone read it against the table: `sbx_gate`,
`jira_cli`, `checkpoint` and `generated_region` had each been added below and
not here. A roster whose whole job is completeness is not partly stale, it is
wrong, and the copy could only ever drift in one direction. Deleting it is the
file applying to itself the rule it already states.

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


def run_sbx_gate(opts):
    return run_python_suite("test_sbx_gate", opts)


def run_purity_lsp(opts):
    return run_python_suite("test_purity_lsp", opts)


def run_purity_file_ops(opts):
    return run_python_suite("test_purity_file_ops", opts)


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


def run_jira_cli(opts):
    return run_python_suite("test_jira_cli", opts)


def run_bitbucket_cli(opts):
    return run_python_suite("test_bitbucket_cli", opts)


def run_checkpoint(opts):
    return run_python_suite("test_checkpoint", opts)


def run_generated_region(opts):
    return run_python_suite("test_generated_region", opts)


def run_read_loop(opts):
    return run_python_suite("test_read_loop", opts)


def run_wire_log(opts):
    return run_python_suite("test_wire_log", opts)


def run_handler_crash(opts):
    return run_python_suite("test_handler_crash", opts)


def run_table_cells(opts):
    return run_python_suite("test_table_cells", opts)


def run_protocol_version(opts):
    return run_python_suite("test_protocol_version", opts)


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
     "mcp-first-guard PreToolUse Bash hook", 335),
    ("sbx_gate", run_sbx_gate,
     "sbx PreToolUse grant-only gate", 91),
    ("purity_lsp", run_purity_lsp,
     "purity_call semantic navigation: clangd + luals absorption", 152),
    ("purity_file_ops", run_purity_file_ops,
     "purity_call file handlers: the .claude/tmp ignore exemption, the "
     "inheritance rule that keeps it narrow, the param contract, and the "
     "glob spellings that can only ever match nothing",
     53),
    ("mcp_git_params", run_mcp_git_params,
     "mcp-git named params -> git argv, offline", 258),
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
     "source_to_pages per-hit description, MEASURED state labels, "
     "file-relative line windows, the page type as a ranking signal, "
     "the frontmatter aliases synonym field", 116),
    ("jira_cli", run_jira_cli,
     "Jira CLI offline: auth mode, context-path URL join, lazy deployment "
     "probe, the Cloud token pager and the DC offset pager behind one "
     "iterator, config precedence, JIRA_READ_ONLY, --dry-run, the five "
     "error mappings, the multipart attachment body, the Markdown "
     "rendering: pipe-escaped cells, empty tables, no duplicated fields, the "
     "`.claude/jira.json` walk and its $HOME boundary, and the create payload: "
     "the one-level merge, alias resolution, NAME=VALUE at the first `=`, the "
     "system-field shaping table, and `@active` refusing every ambiguous "
     "board/sprint configuration instead of guessing", 192),
    # TYPED, and the count is a fixed case table plus ONE derived row: group C
    # sweeps every entry in the CLI's HANDLERS dict, but it records three cases
    # regardless of how many subcommands it finds, so adding a subcommand moves
    # this number by zero.  What DOES move it is adding a case, which is what a
    # typed count is for.  Unlike `mcp_footprint`, nothing here is a per-server
    # or per-site multiple, so there is no legitimate change to the thing being
    # measured that a typed count would punish.
    ("bitbucket_cli", run_bitbucket_cli,
     "Bitbucket CLI offline (transport injected): the context-path URL join "
     "and the two refusals at the door -- an http:// scheme that would put the "
     "bearer PAT on the wire in cleartext, and a userinfo URL whose refusal is "
     "asserted NOT to reproduce the secret -- the same-origin redirect handler "
     "that keeps the Authorization header from following a 30x off this origin, "
     "BITBUCKET_READ_ONLY at BOTH layers with the write set DERIVED by driving "
     "every handler through a verb-recording transport rather than typed twice, "
     "the four merge refusals each measured as zero requests rather than as "
     "statement order, pr-builds' verdict precedence and its exit code pinned "
     "in Markdown AND --json in one case, the per-subcommand request contract "
     "including pr-approve's three requests and paging that follows "
     "nextPageStart, reviewer flattening in both the documented and the "
     "OBSERVED shape, and byte parity with jira.py's _profile_path", 128),
    ("checkpoint", run_checkpoint,
     "checkpoint.py section reader + TOC writer: Start/End land on the block "
     "and nothing else, the numbers describe the file AFTER the region was "
     "inserted, a second write is a byte-for-byte no-op, every byte outside "
     "the region survives, `prepend` lands the block and the table it "
     "describes in ONE os.replace, a stale or duplicate-id segment is refused "
     "on content, and every refusal exits 2 with one line on stderr, leaving "
     "the file alone",
     140),
    ("generated_region", run_generated_region,
     "generated regions match their canonical source, and the source named on "
     "a region's BEGIN line is the one its names resolve against", 88),
    # TYPED, not None, although it is one-plus-one cases per server: here a
    # server appearing WITHOUT a declared row is the defect, so a count that
    # moves when the roster moves is the alarm working rather than noise.  That
    # is the opposite of mcp_footprint's reasoning above, and deliberately so.
    ("read_loop", run_read_loop,
     "every MCP server's read loop carries ADR 0008's shape: a single-thread "
     "reader executor no handler can take, and one task per message -- with "
     "the pool/coroutine split declared per server rather than inferred", 47),
    # TYPED for read_loop's reason above, not mcp_footprint's: a server that
    # appears without a declared row is the defect, so a moved count is the
    # alarm working.  The two per-server groups make it 2N + a fixed tail.
    ("wire_log", run_wire_log,
     "every MCP server's wire logging is structure only -- protocol metadata "
     "and argument KEYS, never a payload body or value (F12/CWE-532), with "
     "the shape each site may log declared per server rather than inferred, "
     "and MCP_SKELETON.md's own sample lifted by script and gated the same way",
     53),
    # TYPED for the same reason as the two above.  The count is 24 declared
    # sites + 15 servers + 4 roster + 11 control + 4 hygiene: the site total is
    # 15 layer-W plus 9 layer-D, so a server that gains or loses a catch-all
    # moves it, which is the alarm working.
    ("handler_crash", run_handler_crash,
     "every MCP server's tool-handler catch-all leaves a traceback at a level "
     "the default WARNING configuration emits -- at BOTH site layers, the "
     "McpServer wrap and the module-level dispatcher, each declared per server "
     "rather than inferred, with the format string required to be a literal so "
     "a payload cannot be interpolated into a log that IS written", 58),
    # TYPED for the same reason again.  The count is 4 escapers + 4 rendered
    # rows + 1 coupling + 2 documented + 2 structure + 4 roster + 12 control +
    # 4 hygiene: a renderer that arrives or changes class moves it, which is the
    # alarm.  The control group is the large one on purpose -- six defective
    # escapers plus a four-way parser/renderer pairing, because this suite's one
    # renderer-level defect lived in two functions and in neither alone.
    ("table_cells", run_table_cells,
     "a rendered table cell cannot forge a column boundary: every renderer "
     "either escapes its own delimiter and documents the scheme where the "
     "model reads it, or is whitespace-delimited and has none to escape -- "
     "with reversibility a SEPARATE clause, because an encoder that does not "
     "escape its own escape character passes a column count", 33),
    # TYPED for the same reason again.  The count is 3 clauses x 15 servers +
    # 2 fleet + 3 roster + 18 control + 3 hygiene: a server arriving without
    # being analysed IS the defect here, so a count that moves when the fleet
    # moves is the alarm working.  The control group is the large one on
    # purpose -- this gate can never be observed red against the live tree,
    # because the fleet converged before it was written, so the planted
    # defects are the only evidence that it detects anything at all.
    ("protocol_version", run_protocol_version,
     "every MCP server declares the handshake protocol version once, as the "
     "first member of class McpServer, and the initialize reply READS that "
     "member instead of restating the literal -- the shape the live smoke "
     "handshake structurally cannot see, since a server inlining the RIGHT "
     "string is indistinguishable on the wire from one reading the constant, "
     "with the fleet's agreement asserted BETWEEN the files so the suite "
     "never holds a copy of the number it polices", 71),
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
