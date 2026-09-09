#!/usr/bin/env python3
"""Generated-region drift gate -- groups A-D.

`Scripts/_mcp_json.py` is the canonical source for helpers the MCP servers
share, and `Scripts/amalgamate.py` inlines its named blocks into each server
between `# BEGIN GENERATED` / `# END GENERATED` markers. The servers stay
single-file on purpose (an imported sibling would write `Scripts/__pycache__`
into a tree four suites assert is empty, and would move the helpers out of the
module attributes `test_mcp_footprint` reaches for), so the shared code is
duplicated on disk BY DESIGN -- and duplication on disk is exactly what rots.

This suite is the thing that stops it rotting: it re-renders every live region
from the canonical source in memory and demands byte identity. A region that
drifted, or whose recorded hash no longer matches its body, fails here.

WHY THE MARKERS ARE SPELLED OUT BELOW instead of imported from the generator:
they are an ON-DISK FORMAT CONTRACT, not an implementation detail. A test that
imported `BEGIN_PREFIX` would sail through a rename that orphaned every region
already written into a server. So this file carries its own copy and asserts the
generator agrees -- the same reasoning `test_checkpoint` applies to its table-of-
contents markers.

TEXT, NOT AST -- deliberately. The fleet's rule is that source SCANNING uses
`ast`, never regex, because finding a call site is a semantic question. This
suite asks a different question: whether a delimited region of text is
byte-identical to what a generator emits. That is legitimately a text operation.
The generator itself does use `tokenize` to find its markers, and group C proves
it: a marker quoted inside a docstring must be inert.

SCOPE. Group A gates the live tree. Group B gates the format contract. Group C
is the negative control -- synthetic sources, each mutation asserted DETECTED,
plus bait that must stay silent, because a checker that silently matches nothing
is indistinguishable from a clean tree. Group D is hygiene.

Every case is a gated FAIL: comparing two in-memory strings cannot flap on
ordinary work, so a failure here can only be a regression.

IN-MEMORY ONLY. No subprocess, no external binary, no network, and this suite
writes NOTHING -- not into the repo, not into a sandbox. The generator exposes
`audit_text` precisely so the control group needs no scratch directory.

Groups:
  A. GATE:    the live regions in Scripts/mcp-*.py match the canonical source
  B. CONTRACT: marker spelling, hash algorithm, path anchoring, render layout
  C. CONTROL: mutations are detected; quoted markers are not regions
  D. HYGIENE: no bytecode written, no source file touched

Usage:
  python3 tests/test_generated_region.py            # standalone
  python3 tests/run.py generated_region             # through the fleet runner
  python3 tests/test_generated_region.py --brief    # one line per group

The case count lives in the SUITES table in tests/run.py, never here.
Exit code 0 iff every non-informational case passes.
"""

import hashlib
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "generated_region"

SOURCE = H.repo_path("Scripts", "_mcp_json.py")
GENERATOR = H.repo_path("Scripts", "amalgamate.py")
TARGET = H.repo_path("Scripts", "mcp-purity.py")
SCRIPTS = H.repo_path("Scripts")

# The on-disk format contract. Spelled here, asserted against the generator in
# group B -- see the docstring for why these are not imported.
BEGIN_PREFIX = "# BEGIN GENERATED:"
END_PREFIX = "# END GENERATED:"
TARGET_GLOB = "mcp-*.py"
CANONICAL_NAME = "_mcp_json.py"

GA = "A. GATE: live regions match the canonical source"
GB = "B. CONTRACT: marker spelling, hashing, anchoring, layout"
GC = "C. CONTROL: mutations detected, quoted markers inert"
GD = "D. HYGIENE: no bytecode, no source touched"

# --- synthetic material for group C -------------------------------------------

SYNTH_BODY = 'def _helper():\n    return "ok"\n'
SYNTH_BLOCKS = {"_helper": SYNTH_BODY}
RENDER_BLOCKS = {"a": "def a():\n    pass\n", "b": "def b():\n    pass\n"}


def synth_host(body, recorded=None, names="_helper", source=CANONICAL_NAME):
    """Build a synthetic target file carrying exactly one region."""
    tail = " %s" % recorded if recorded else ""
    return (
        '"""A synthetic target."""\n'
        "%s %s :: %s\n" % (BEGIN_PREFIX, source, names)
        + body
        + "%s%s\n" % (END_PREFIX, tail)
    )


def expect_exit(fn):
    """Run *fn*; return its SystemExit message, or None if it did not raise."""
    try:
        fn()
    except SystemExit as exc:
        return str(exc)
    return None


def problem_if(condition, message):
    return [message] if condition else []


# --- groups -------------------------------------------------------------------

def group_gate(suite, mod):
    blocks = mod.load_blocks(mod.CANONICAL)
    regions = mod.audit(Path(TARGET), blocks)

    suite.record(GA, "region-present", problem_if(
        len(regions) != 1,
        "mcp-purity.py should carry exactly 1 region, found %d" % len(regions),
    ), detail=["names: %s" % ", ".join(r.label for r in regions)])

    if not regions:
        # Without a region the remaining gate cases have no subject; record them
        # as failures rather than skipping, so the count never moves.
        for cid in ("body-identical", "sum-recorded"):
            suite.record(GA, cid, ["no region to inspect"])
    else:
        region = regions[0]
        suite.record(GA, "body-identical", problem_if(
            region.body != region.wanted,
            "region body differs from the canonical render",
        ))
        suite.record(GA, "sum-recorded", problem_if(
            region.recorded != mod.body_hash(region.body),
            "recorded hash %r != body hash %r"
            % (region.recorded, mod.body_hash(region.body)),
        ))

    stale = []
    listed = set()
    for path in sorted(Path(SCRIPTS).glob(TARGET_GLOB)):
        for region in mod.audit(path, blocks):
            listed.update(region.names)
            if region.state != "ok":
                stale.append("%s [%s]: %s" % (path.name, region.label, region.state))
    suite.record(GA, "fleet-ok", stale, detail=stale)

    missing = sorted(n for n in listed if n not in blocks)
    suite.record(GA, "names-exist", problem_if(
        missing,
        "regions list names absent from %s: %s" % (CANONICAL_NAME, missing),
    ), detail=["names in use: %s" % ", ".join(sorted(listed))])


def group_contract(suite, mod):
    suite.record(GB, "begin-prefix", problem_if(
        mod.BEGIN_PREFIX != BEGIN_PREFIX,
        "generator BEGIN_PREFIX is %r, on-disk contract is %r"
        % (mod.BEGIN_PREFIX, BEGIN_PREFIX),
    ))
    suite.record(GB, "end-prefix", problem_if(
        mod.END_PREFIX != END_PREFIX,
        "generator END_PREFIX is %r, on-disk contract is %r"
        % (mod.END_PREFIX, END_PREFIX),
    ))
    suite.record(GB, "target-glob", problem_if(
        mod.TARGET_GLOB != TARGET_GLOB,
        "generator TARGET_GLOB is %r, expected %r" % (mod.TARGET_GLOB, TARGET_GLOB),
    ))

    sample = b"generated-region-hash-sample"
    expected = hashlib.sha256(sample).hexdigest()[:12]
    actual = mod.body_hash(sample.decode("ascii"))
    suite.record(GB, "hash-is-sha256-12", problem_if(
        actual != expected,
        "body_hash gave %r, sha256[:12] is %r" % (actual, expected),
    ))

    # The generator resolves its paths from __file__, never from getcwd() --
    # that is what makes `python3 ../../Scripts/amalgamate.py` work.
    suite.record(GB, "anchored-on-file", problem_if(
        os.path.realpath(str(mod.SCRIPTS_DIR)) != os.path.realpath(SCRIPTS)
        or os.path.realpath(str(mod.CANONICAL)) != os.path.realpath(SOURCE),
        "SCRIPTS_DIR=%s CANONICAL=%s do not resolve to the repo's Scripts/"
        % (mod.SCRIPTS_DIR, mod.CANONICAL),
    ))

    rendered = mod.render(["a", "b"], RENDER_BLOCKS)
    wanted = "def a():\n    pass\n\n\ndef b():\n    pass\n"
    suite.record(GB, "render-layout", problem_if(
        rendered != wanted,
        "render() gave %r, expected two blank lines and one trailing newline"
        % rendered,
    ))


def group_control(suite, mod):
    def audit(text):
        return mod.audit_text("synthetic.py", text, SYNTH_BLOCKS)

    good_sum = mod.body_hash(SYNTH_BODY)
    mutated = SYNTH_BODY.replace('"ok"', '"OK"')

    # 1. The control fires at all: a drifted body whose hash was recorded from
    #    that same drifted body is STALE -- the generator may rewrite it.
    regions = audit(synth_host(mutated, mod.body_hash(mutated)))
    suite.record(GC, "drift-detected", problem_if(
        len(regions) != 1 or regions[0].state != "stale",
        "expected one stale region, got %s"
        % [(r.label, r.state) for r in regions],
    ))

    # 2. A body that no longer matches its OWN recorded hash was hand-edited,
    #    and must be refused rather than silently overwritten.
    regions = audit(synth_host(mutated, good_sum))
    suite.record(GC, "hand-edit-detected", problem_if(
        len(regions) != 1 or regions[0].state != "hand-edited",
        "expected one hand-edited region, got %s"
        % [(r.label, r.state) for r in regions],
    ))

    # 3. An in-sync region must NOT be reported -- the other half of vacuity.
    regions = audit(synth_host(SYNTH_BODY, good_sum))
    suite.record(GC, "in-sync-silent", problem_if(
        len(regions) != 1 or regions[0].state != "ok",
        "an in-sync region should read ok, got %s"
        % [(r.label, r.state) for r in regions],
    ))

    # 4-7. Every structural marker defect must be LOUD, never a silent skip.
    open_region = '"""x"""\n%s %s :: _helper\n%s' % (
        BEGIN_PREFIX, CANONICAL_NAME, SYNTH_BODY)
    open_message = expect_exit(lambda: audit(open_region))
    suite.record(GC, "missing-end", problem_if(
        not open_message,
        "a BEGIN without an END was accepted",
    ), detail=[str(open_message)])

    suite.record(GC, "missing-begin", problem_if(
        not expect_exit(lambda: audit('"""x"""\n%s %s\n' % (END_PREFIX, good_sum))),
        "an END without a BEGIN was accepted",
    ))

    reversed_pair = '"""x"""\n%s %s\n%s %s :: _helper\n%s' % (
        END_PREFIX, good_sum, BEGIN_PREFIX, CANONICAL_NAME, SYNTH_BODY)
    suite.record(GC, "reversed-markers", problem_if(
        not expect_exit(lambda: audit(reversed_pair)),
        "END-before-BEGIN was accepted",
    ))

    nested = '"""x"""\n%s %s :: _helper\n%s %s :: _helper\n%s%s\n' % (
        BEGIN_PREFIX, CANONICAL_NAME, BEGIN_PREFIX, CANONICAL_NAME,
        SYNTH_BODY, END_PREFIX)
    suite.record(GC, "nested-begin", problem_if(
        not expect_exit(lambda: audit(nested)),
        "a BEGIN inside an open region was accepted",
    ))

    # 8-9. Malformed marker payloads.
    suite.record(GC, "malformed-marker", problem_if(
        not expect_exit(lambda: audit(
            '"""x"""\n%s no-separator-here\n%s%s\n'
            % (BEGIN_PREFIX, SYNTH_BODY, END_PREFIX))),
        "a BEGIN without the '::' separator was accepted",
    ))
    suite.record(GC, "unknown-source", problem_if(
        not expect_exit(lambda: audit(
            synth_host(SYNTH_BODY, good_sum, source="somewhere-else.py"))),
        "a region naming an unknown source was accepted",
    ))
    suite.record(GC, "unknown-name", problem_if(
        not expect_exit(lambda: audit(
            synth_host(SYNTH_BODY, good_sum, names="_not_in_source"))),
        "a region naming a symbol absent from the source was accepted",
    ))

    # 10. An empty region is drift, not "nothing to do".
    regions = audit(synth_host(""))
    suite.record(GC, "empty-region", problem_if(
        len(regions) != 1 or regions[0].state == "ok",
        "an empty region should not read ok, got %s"
        % [(r.label, r.state) for r in regions],
    ))

    # 11. Whitespace-only drift is drift: byte identity, not "looks the same".
    spaced = SYNTH_BODY.rstrip("\n") + " \n"
    regions = audit(synth_host(spaced, mod.body_hash(spaced)))
    suite.record(GC, "whitespace-drift", problem_if(
        len(regions) != 1 or regions[0].state == "ok",
        "trailing-whitespace drift read as ok",
    ))

    # 12-14. BAIT: a marker that is not a comment is not a marker. This is what
    #        `tokenize` buys over a line scan, and the hazard is real -- the
    #        generator's own docstring carries a full BEGIN/END pair.
    bait_docstring = (
        '"""Documentation.\n\n'
        "    %s %s :: _helper\n"
        "    def _helper(): ...\n"
        "    %s deadbeefcafe\n"
        '"""\n'
    ) % (BEGIN_PREFIX, CANONICAL_NAME, END_PREFIX)
    suite.record(GC, "bait-docstring", problem_if(
        audit(bait_docstring),
        "a marker quoted in a docstring was treated as a region",
    ))

    bait_literal = 'NOTE = "%s %s :: _helper"\nOTHER = "%s ff00ff00ff00"\n' % (
        BEGIN_PREFIX, CANONICAL_NAME, END_PREFIX)
    suite.record(GC, "bait-string-literal", problem_if(
        audit(bait_literal),
        "a marker inside a string literal was treated as a region",
    ))

    with open(GENERATOR, encoding="utf-8") as handle:
        generator_source = handle.read()
    suite.record(GC, "bait-generator-itself", problem_if(
        mod.audit_text("amalgamate.py", generator_source, SYNTH_BLOCKS),
        "the generator's own docstring markers were treated as regions",
    ), detail=["the live proof: amalgamate.py documents both markers"])

    # 15. A file with no region at all is legitimate -- servers convert one at
    #     a time, so this must be silence, not an error.
    suite.record(GC, "no-region-is-fine", problem_if(
        audit('"""Nothing generated here."""\nX = 1\n'),
        "a target with no markers reported a region",
    ))


def group_hygiene(suite, pyc_before, digests_before):
    pyc_after = H.pycache_snapshot()
    suite.record(GD, "pycache-zero", problem_if(
        pyc_before or pyc_after,
        "expected zero .pyc in the tree, saw %d before and %d after"
        % (len(pyc_before), len(pyc_after)),
    ))

    changed = [
        path for path, digest in digests_before.items()
        if H.sha256_file(path) != digest
    ]
    suite.record(GD, "sources-untouched", problem_if(
        changed,
        "this suite modified: %s" % [os.path.basename(p) for p in changed],
    ))


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="generated regions match their canonical source",
                    opts=opts, mode="grouped")

    pyc_before = H.pycache_snapshot()
    digests_before = {p: H.sha256_file(p) for p in (SOURCE, GENERATOR, TARGET)}

    mod = H.load_module_from_path("amalgamate_under_test", GENERATOR)
    group_gate(suite, mod)
    group_contract(suite, mod)
    group_control(suite, mod)
    group_hygiene(suite, pyc_before, digests_before)

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
