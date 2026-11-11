#!/usr/bin/env python3
"""Offline suite for the documents skill (ClaudeCode/skills/documents/scripts).

The skill's three scripts (measure.py, replica.py, compare.py) and their shared
replica_common.py are exercised through plain `unittest` case classes that live
beside this file as documents_cases_{typst,compare,measure}.py, with the
synthetic-dump builders in documents_helpers.py.  None of those names starts
with `test_`, so nothing discovers them but this module: it loads every TestCase
and records ONE harness case per test method, grouped by case module.

NOTHING HERE NEEDS THE PRIVATE liteparser PROJECT.  replica.py's dependency on
it (the whole litepdf package, litepdf_typst's API in process) is exercised
against a STUB package written under a mkdtemp, and the lookup through its
refusals (exit 2 and the one-line message naming the missing module).  The
single integration test (emit -> build -> compare against the real package)
needs $LITEPDF_DIR and the Liberation Serif TTFs; without them it is SKIPPED by
unittest and recorded here as INFO (the convention purity_lsp uses), so the case
count is the same on every host and a host without liteparser stays green.

Groups:
  A  replica.py   font map, the package-wide lookup, the CLI exit codes, the
                  subset-error one-line mapping, same-stem outputs, the text
                  check, and the integration emit -> build -> compare (skipped
                  without liteparser)
  B  compare.py   structure checks and the CLI exit codes
  C  measure.py   page analysis, the rotation refusal, the dependency lookup
  D  hygiene      the run wrote nothing into the repo tree and no bytecode

Usage:
  python3 tests/test_documents.py
  python3 tests/test_documents.py --brief
Exit code 0 iff every non-informational case passes.
"""

import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "documents"
SCRIPTS = H.repo_path("ClaudeCode", "skills", "documents", "scripts")

GA = "A. replica.py"
GB = "B. compare.py"
GC = "C. measure.py"
GD = "D. hygiene"

MODULES = (
    (GA, "documents_cases_typst"),
    (GB, "documents_cases_compare"),
    (GC, "documents_cases_measure"),
)


def _iter_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            for sub in _iter_tests(item):
                yield sub
        else:
            yield item


def _record_module(suite, group, module_name):
    try:
        module = __import__(module_name)
    except Exception as exc:  # noqa: BLE001 -- an import failure is a finding
        suite.record(group, "import-" + module_name,
                     ["%s: %s" % (type(exc).__name__, exc)])
        return
    loaded = unittest.defaultTestLoader.loadTestsFromModule(module)
    for test in _iter_tests(loaded):
        cid = "%s.%s" % (type(test).__name__, test._testMethodName)
        result = unittest.TestResult()
        test.run(result)
        problems = []
        for _t, tb in result.failures + result.errors:
            problems.append(tb.strip().splitlines()[-1] if tb.strip() else "failed")
        detail = [tb for _t, tb in result.failures + result.errors]
        if problems:
            suite.record(group, cid, problems, detail=detail)
        elif result.skipped:
            suite.record(group, cid, status=H.INFO,
                         detail=["SKIPPED: %s" % result.skipped[0][1]])
        else:
            suite.record(group, cid)


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="documents skill: replica (litepdf_typst wrapper), "
                          "compare and measure logic, their refusals and the "
                          "no-fallback liteparser package lookup, offline",
                    opts=opts, mode="grouped")

    before = H.repo_tree()
    pyc_before = H.pycache_snapshot()

    if not os.path.isdir(SCRIPTS):
        suite.record(GA, "target-exists",
                     ["the scripts under test are missing: %s" % SCRIPTS])
        suite.print_summary()
        return suite

    for group, module_name in MODULES:
        _record_module(suite, group, module_name)

    after = H.repo_tree()
    pyc_after = H.pycache_snapshot()
    problems = []
    new = sorted(after - before)
    if new:
        problems.append("this suite wrote into the repo tree: %s" % new[:12])
    created = sorted(set(pyc_after) - set(pyc_before))
    if created:
        problems.append("this run wrote bytecode: %s" % created[:6])
    suite.record(GD, "no-new-repo-paths-or-bytecode", problems)

    suite.print_summary()
    return suite


def main(argv=None):
    opts = H.parse_options(argv)
    if opts.help:
        print(__doc__)
        return 0
    return run(opts).exit_code


if __name__ == "__main__":
    sys.exit(main())
