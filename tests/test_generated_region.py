#!/usr/bin/env python3
"""Generated-region drift gate -- groups A-F.

`Scripts/_mcp_json.py`, `Scripts/_mcp_logging.py`, `Scripts/_mcp_lsp.py` and
`Scripts/_mcp_paging.py` are the canonical sources for the helpers the MCP
servers share, and
`Scripts/amalgamate.py` inlines their named blocks into each server between
`# BEGIN GENERATED` / `# END GENERATED` markers. The servers stay
single-file on purpose (an imported sibling would write `Scripts/__pycache__`
into a tree every suite that snapshots bytecode asserts stays empty, and would
move the helpers out of the module attributes `test_mcp_footprint` reaches
for), so the shared code is duplicated on disk BY DESIGN -- and duplication on
disk is exactly what rots.

This suite is the thing that stops it rotting: it re-renders every live region
from the canonical source in memory and demands byte identity. A region that
drifted, or whose recorded hash no longer matches its body, fails here.

MORE THAN ONE SOURCE, AND THE SOURCE IS LOAD-BEARING. The filename on a BEGIN
line selects which block map the region's names are resolved against -- it is
not a comment. Two rules follow, and both are asserted rather than assumed: an
unknown source is refused by name, and a name defined in ANOTHER canonical file
does not resolve. That second one is the dangerous direction: a block served
out of the wrong domain compiles, runs, and looks correct in every server that
carries it, so nothing downstream would ever report it.

Each source is a DOMAIN, and the domains are narrowed as blocks earn their own
home -- LSP framing and row accounting both started in the JSON source and both
left it. Group E asserts the departures as well as the arrivals: a block that
came back would otherwise be tested in its new home and still be sitting in its
old one.

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

Every case here is a gated FAIL but one: comparing two in-memory strings cannot
flap on ordinary work, so a failure can only be a regression. The exception is
group A's `hand-copies-are-named`, an INFO census -- the roster comment beside
it gives the reason, that "this server keeps its own" is a legitimate answer
and so not a thing to fail on.

IN-MEMORY ONLY. No subprocess, no external binary, no network, and this suite
writes NOTHING -- not into the repo, not into a sandbox. The generator exposes
`audit_text` precisely so the control group needs no scratch directory.

Group E is the other half, and it is not optional: a drift gate on its own would
only ever prove that fourteen files agree on the same bug. E imports every
canonical module and exercises each block's BEHAVIOUR, so a helper inlined into
fourteen servers is unit-tested once -- here, and nowhere else in the fleet.

Groups:
  A. GATE:    the live regions in Scripts/mcp-*.py match their canonical source
  B. CONTRACT: marker spelling, hashing, anchoring, layout, source disjointness
  C. CONTROL: mutations are detected; quoted markers are not regions; a name
              does not resolve against a source that does not define it
  D. HYGIENE: no bytecode written, no source file touched
  E. BLOCKS:  what each shared block actually does
  F. TABS:    which blocks may be re-indented for a tab-indented host, decided
              per BLOCK and mechanically; one that may not is refused by name;
              a space-indented host is served exactly what it was served before

Usage:
  python3 tests/test_generated_region.py            # standalone
  python3 tests/run.py generated_region             # through the fleet runner
  python3 tests/test_generated_region.py --brief    # one line per group

The case count lives in the SUITES table in tests/run.py, never here.
Exit code 0 iff every non-informational case passes.
"""

import hashlib
import json
import logging
import os
import stat
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "generated_region"

SOURCE = H.repo_path("Scripts", "_mcp_json.py")
LOGGING_SOURCE = H.repo_path("Scripts", "_mcp_logging.py")
LSP_SOURCE = H.repo_path("Scripts", "_mcp_lsp.py")
PAGING_SOURCE = H.repo_path("Scripts", "_mcp_paging.py")
GENERATOR = H.repo_path("Scripts", "amalgamate.py")
TARGET = H.repo_path("Scripts", "mcp-purity.py")
SCRIPTS = H.repo_path("Scripts")

# The on-disk format contract. Spelled here, asserted against the generator in
# group B -- see the docstring for why these are not imported.
BEGIN_PREFIX = "# BEGIN GENERATED:"
END_PREFIX = "# END GENERATED:"
TARGET_GLOB = "mcp-*.py"
CANONICAL_NAME = "_mcp_json.py"
LOGGING_CANONICAL_NAME = "_mcp_logging.py"
LSP_CANONICAL_NAME = "_mcp_lsp.py"
PAGING_CANONICAL_NAME = "_mcp_paging.py"
# The registry is part of the same contract: it is written out by hand in the
# generator precisely so a new `_mcp_*.py` file cannot become a generation
# source by existing, and a test that read it back off a glob would agree with
# whatever the glob found.
CANONICAL_NAMES = (CANONICAL_NAME, LOGGING_CANONICAL_NAME,
                   LSP_CANONICAL_NAME, PAGING_CANONICAL_NAME)

GA = "A. GATE: live regions match their canonical source"
GB = "B. CONTRACT: marker spelling, hashing, anchoring, layout, disjointness"
GC = "C. CONTROL: mutations detected, quoted markers inert, sources not crossed"
GD = "D. HYGIENE: no bytecode, no source touched"
GE = "E. BLOCKS: what each shared block actually does"
GF = "F. TABS: per-block safety, host detection, space hosts untouched"

# --- synthetic material for group C -------------------------------------------

SYNTH_BODY = 'def _helper():\n    return "ok"\n'
SYNTH_BLOCKS = {"_helper": SYNTH_BODY}
SYNTH_SOURCES = {CANONICAL_NAME: SYNTH_BLOCKS}
RENDER_BLOCKS = {"a": "def a():\n    pass\n", "b": "def b():\n    pass\n"}

# A second synthetic domain that defines the SAME name with a different body.
# The collision is the point: it makes "which source did the marker say" an
# observable question instead of a stylistic one.
OTHER_BODY = 'def _helper():\n    return "OTHER"\n'
OTHER_ONLY_BODY = 'def _elsewhere():\n    return 1\n'
TWO_SOURCES = {
    CANONICAL_NAME: SYNTH_BLOCKS,
    LSP_CANONICAL_NAME: {"_helper": OTHER_BODY, "_elsewhere": OTHER_ONLY_BODY},
}


def synth_host(body, recorded=None, names="_helper", source=CANONICAL_NAME):
    """Build a synthetic target file carrying exactly one region."""
    tail = " %s" % recorded if recorded else ""
    return (
        '"""A synthetic target."""\n'
        "%s %s :: %s\n" % (BEGIN_PREFIX, source, names)
        + body
        + "%s%s\n" % (END_PREFIX, tail)
    )


# The fleet-wide sentence `_json_error_window` was extracted to serve. Spelled
# out here for the same reason the markers are: it is a CONTRACT across every
# server now, and a test that derived it from one of them would only ever prove
# they all agree -- including on a drift.
#
# The SENTENCE is one string; what varies is only the LOCAL NAME holding the
# text that failed to parse, and the fleet spells that name four ways: `params`
# in a param normalizer, and `args` / `arguments` / `tool_args` in the three
# flavours of tools/call handler. Every one is listed DELIBERATELY, and the list
# is meant to be edited when a fifth appears. Do NOT relax this into a substring
# or an identifier wildcard: the whole point is that "Near the error", a missing
# period and a lost trailing space are each caught, and a wildcard would wave
# all three through while still looking like a test.
#
# `value` was the fifth spelling and has LEFT the list -- which is how a name
# should leave it. It was the local inside `_ensure_dict`, and that function is
# now a generated block co-listed with the window, so its call site is single-
# sourced and compared byte for byte by group A instead of by this census. A
# hand copy coming back would surface in `hand-copies-are-named` first, and a
# drift inside the block cannot reach here at all.
WINDOW_NAME = "_json_error_window"
WINDOW_SENTENCE = 'f"Near the failure: {%s(%%s, exc.pos)}. "' % WINDOW_NAME
WINDOW_FIELDS = ("params", "args", "arguments", "tool_args")
WINDOW_MIN_HOSTS = 8

# The one block that calls the window from INSIDE a region. `host_provides`
# offers a region only the host's module-level IMPORTS, never a name another
# region defines, so `_ensure_dict` in a region of its own is refused in every
# host -- it travels co-listed with the window on one marker. That co-listing is
# why the liveness case below asks about the PAIR rather than about one name.
WINDOW_RELAY = "_ensure_dict"


def outside_regions(mod, label, text, sources):
    """*text* with every generated region's marker-to-marker span removed.

    A call INSIDE a region is the block calling itself, not a caller; counting
    it would make "this region has a user" true the moment the region exists.
    """
    lines = text.splitlines(keepends=True)
    for region in sorted(mod.audit_text(label, text, sources),
                         key=lambda r: r.begin, reverse=True):
        del lines[region.begin:region.end + 1]
    return "".join(lines)


def tab_host(names, source=PAGING_CANONICAL_NAME):
    """A synthetic TAB-indented target carrying exactly one region at column 0.

    The imports are not decoration: the block contract check runs against the
    host, and `_result`'s annotation needs `Any`, `encode_lsp_message` needs
    `json`, `_FENCE_LINE_RE` -- the first CONSTANT block -- needs `re`, and
    `_configure_logging` needs `logging`, `os` and `sys`. A fixture without
    them would be refused for the wrong reason and the tab cases would pass on
    an error that has nothing to do with tabs.

    `tab-emission-is-all-tabs` drives EVERY tab-safe block through this one
    fixture, so the import list is not "what today's cases happen to need" but
    the union of what every canonical block reads. A block added upstream with
    a new free name lands here as a refusal naming that name.

    That last sentence has now been paid out rather than merely promised: the
    logging source arrived with three free names this fixture did not import,
    and the suite stopped with a refusal naming `logging`, `os` and `sys` --
    before any tab case could pass on an unrelated error.
    """
    return (
        '"""A tab-indented target."""\n'
        "import json\n"
        "import logging\n"
        "import os\n"
        "import re\n"
        "import sys\n"
        "from typing import Any\n"
        "\n\n"
        "def _existing():\n"
        "\tif True:\n"
        "\t\treturn json, logging, os, re, sys, Any\n"
        "\n\n"
        "%s %s :: %s\n" % (BEGIN_PREFIX, source, names)
        + "%s\n" % END_PREFIX
    )


def untab(text, width=4):
    """Reverse of the emitter's conversion: one leading tab back to 4 spaces."""
    out = []
    for line in text.splitlines(keepends=True):
        body = line.lstrip("\t")
        out.append(" " * (width * (len(line) - len(body))) + body)
    return "".join(out)


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
    sources = mod.load_all_blocks()
    regions = mod.audit(Path(TARGET), sources)

    # The registry itself, before anything is resolved against it: an explicit
    # tuple of filenames, each one a real file. A source silently missing from
    # the registry would not fail below -- every region naming it would be
    # refused, which reads as a marker defect rather than a registry one.
    registry = sorted(mod.CANONICAL_SOURCES)
    problems = problem_if(
        registry != sorted(CANONICAL_NAMES),
        "generator registry is %s, the on-disk contract is %s"
        % (registry, sorted(CANONICAL_NAMES)),
    )
    problems += ["%s: not a file at %s" % (name, path)
                 for name, path in sorted(mod.CANONICAL_SOURCES.items())
                 if not path.is_file()]
    suite.record(GA, "sources-registered", problems,
                 detail=["registered: %s" % ", ".join(registry)])

    # The COUNT is not asserted: it grows every time a block is extracted, and a
    # number typed here would fail on progress rather than on a regression.
    suite.record(GA, "region-present", problem_if(
        not regions,
        "mcp-purity.py carries no generated region at all",
    ), detail=["%d region(s): %s"
               % (len(regions), "; ".join(r.label for r in regions))])

    drifted = ["%s: body differs from the canonical render" % r.label
               for r in regions if r.body != r.wanted]
    suite.record(GA, "body-identical", drifted, detail=drifted)

    unsummed = ["%s: recorded %r, body hashes to %r"
                % (r.label, r.recorded, mod.body_hash(r.body))
                for r in regions if r.recorded != mod.body_hash(r.body)]
    suite.record(GA, "sum-recorded", unsummed, detail=unsummed)

    stale = []
    listed = set()
    for path in sorted(Path(SCRIPTS).glob(TARGET_GLOB)):
        for region in mod.audit(path, sources):
            listed.update((region.source, name) for name in region.names)
            if region.state != "ok":
                stale.append("%s [%s]: %s" % (path.name, region.label, region.state))
    suite.record(GA, "fleet-ok", stale, detail=stale)

    # Resolved PER SOURCE, not against the union: a name that exists only in the
    # other canonical file is exactly the defect this pair of loops must see.
    missing = sorted("%s :: %s" % (source, name) for source, name in listed
                     if name not in sources[source])
    suite.record(GA, "names-exist", problem_if(
        missing,
        "regions list names their own source does not define: %s" % missing,
    ), detail=["%s :: %s" % pair for pair in sorted(listed)])

    # ONE WRITER, with the exceptions NAMED rather than hidden. A canonical block
    # name defined at top level in a server but not inside a generated region is
    # a hand copy, and every one left today is a DECLARED exclusion with a
    # measured reason: mcp-inspect's `_int_param` takes a parameter NAME and
    # RAISES where the canonical takes a default and falls back; mcp-git's
    # `_max_answer_chars` is excluded TWICE OVER, like webfetch's `_rows_note`
    # below -- it defaults to its own `DEFAULT_MAX_CHARS` rather than to the
    # 24000 the canonical block renders its reader WITH, so taking that marker
    # would take the value, AND its body carries a camelCase fallback loop the
    # canonical has no trace of, so settling the value question alone would not
    # make it adoptable. The reason was written at the definition all along;
    # what was missing until the constant beside it was lifted is this roster
    # entry, which is the half a reader surveying the fleet actually reaches for.
    # mcp-tshark's
    # `_bool_param` keeps the older `(params, key, default)` signature;
    # mcp-webfetch's `_bool_param` is an ALLOW-list, so an unrecognised string
    # reads False there and True canonically; and mcp-webfetch's `_rows_note`
    # is excluded TWICE OVER -- it fails `block_is_tab_safe` (its `else` aligns
    # under an open paren, so a tab host is refused that one BY NAME) AND its
    # body has diverged: `(start, shown, total)` against the canonical
    # `(start, shown, total, exact)`, with no lower-bound branch, so clearing
    # the tab hazard alone would not make it adoptable. Exclusion is per BLOCK,
    # not per server: both tab-indented servers host regions, webfetch two of
    # them.
    # INFO, not FAIL: "this server keeps its own" is a legitimate answer, so
    # this censuses rather than judges -- but an UNDECLARED hand copy, or a new
    # one, cannot appear without landing on this line, and the census is what
    # makes the next fan-out decision a reading rather than a survey.
    known = {name for blocks in sources.values() for name in blocks}
    hand = []
    for path in sorted(Path(SCRIPTS).glob(TARGET_GLOB)):
        text = path.read_text(encoding="utf-8")
        covered = set()
        for region in mod.audit_text(path.name, text, sources):
            covered.update(region.names)
        defined = set(mod.load_blocks_text(path.name, text)) & known
        hand += ["%s: %s" % (path.name, name)
                 for name in sorted(defined - covered)]
    suite.record(GA, "hand-copies-are-named", status=H.INFO,
                 detail=hand or ["no server keeps a hand copy of a canonical "
                                 "block name"])

    # `_json_error_window` went fleet-wide, and that creates two invariants
    # nothing measured before. FAIL rather than INFO for both: they compare text
    # already in the repo, with no environment, no binary and no ordering, so
    # neither can flap on ordinary work -- the fleet's bar for a gated FAIL.
    hosts, callers, sentences = set(), set(), []
    relay_hosts, relay_callers = set(), set()
    for path in sorted(Path(SCRIPTS).glob(TARGET_GLOB)):
        text = path.read_text(encoding="utf-8")
        regioned = set()
        for region in mod.audit_text(path.name, text, sources):
            regioned.update(region.names)
        if WINDOW_NAME in regioned:
            hosts.add(path.name)
        if WINDOW_RELAY in regioned:
            relay_hosts.add(path.name)
        body = outside_regions(mod, path.name, text, sources)
        if "%s(" % WINDOW_NAME in body:
            callers.add(path.name)
        if "%s(" % WINDOW_RELAY in body:
            relay_callers.add(path.name)
        sentences += [(path.name, line.strip()) for line in body.splitlines()
                      if "Near the failure:" in line]

    # A region with no caller is dead code -- and worse than ordinary dead code,
    # because the drift gate would then faithfully prove that N files agree on
    # carrying it. The reverse is a NameError the moment that path is taken.
    #
    # Reached THROUGH THE RELAY, which is not the same as waived for it. Once
    # `_ensure_dict` joined the window on one marker, the three servers whose
    # only window call site was the one inside that function -- mcp-forge,
    # mcp-git and mcp-inspect -- call the window from INSIDE the region, where
    # `outside_regions` deliberately cannot see it. What keeps their region
    # alive is a call to `_ensure_dict`, and it is required rather than assumed:
    # a server that stopped calling BOTH still lands on this line.
    reached = callers | (relay_hosts & relay_callers)
    problems = ["%s: hosts %s, and neither it nor %s is called from outside a "
                "region" % (name, WINDOW_NAME, WINDOW_RELAY)
                for name in sorted(hosts - reached)]
    problems += ["%s: calls %s without hosting the region -- NameError"
                 % (name, WINDOW_NAME) for name in sorted(callers - hosts)]
    problems += ["%s: calls %s without hosting it -- NameError"
                 % (name, WINDOW_RELAY)
                 for name in sorted(relay_callers - relay_hosts)]
    problems += problem_if(
        len(hosts) < WINDOW_MIN_HOSTS,
        "only %d server(s) host %s; this case examined too few to mean "
        "anything" % (len(hosts), WINDOW_NAME),
    )
    # Without this the relay arm is vacuous: if nothing hosted `_ensure_dict`
    # the union above would collapse back to `callers` and read green forever.
    problems += problem_if(
        not relay_hosts,
        "no server hosts %s, so the relay arm of this case proves nothing"
        % WINDOW_RELAY,
    )
    suite.record(GA, "window-region-has-a-caller", problems,
                 detail=["%d host(s): %d call %s directly, %d reach it only "
                         "through %s"
                         % (len(hosts), len(callers), WINDOW_NAME,
                            len((relay_hosts & relay_callers) - callers),
                            WINDOW_RELAY)])

    # One writer for the code was the easy half. The SENTENCE is the half that
    # rots: it is hand-written at each call site, says the same thing in a dozen
    # files, and nothing but this case compares them.
    #
    # Paired against the servers that call the window BY HAND, not against the
    # servers that HOST it. Those two sets used to coincide and stopped the day
    # `_ensure_dict` became a block: the sentence inside it is generated now,
    # and group A compares it byte for byte. What is left under this case is
    # exactly the hand-written population it was written for.
    allowed = {WINDOW_SENTENCE % field for field in WINDOW_FIELDS}
    problems = ["%s: %s" % (name, line) for name, line in sentences
                if line not in allowed]
    carrying = {name for name, _line in sentences}
    problems += problem_if(
        carrying != callers,
        "the servers carrying the sentence and the servers calling %s by hand "
        "differ: %s" % (WINDOW_NAME, sorted(carrying ^ callers)),
    )
    problems += problem_if(
        len(sentences) < WINDOW_MIN_HOSTS,
        "only %d call site(s) found; the wording comparison is vacuous"
        % len(sentences),
    )
    suite.record(GA, "window-sentence-identical", problems,
                 detail=["%d call site(s), %d distinct wording(s)"
                         % (len(sentences),
                            len({line for _n, line in sentences}))])

    # Every registered source must be ASKED FOR by a live region. A canonical
    # file nobody names is not harmless: it is a shelf the drift gate cannot
    # reach, so a block rotting in it reads green here forever.
    used = {source for source, _name in listed}
    suite.record(GA, "every-source-in-use", problem_if(
        sorted(used) != sorted(CANONICAL_NAMES),
        "registered but unrequested: %s"
        % sorted(set(CANONICAL_NAMES) - used),
    ), detail=["%s: %d name(s) requested"
               % (source, len([1 for s, _n in listed if s == source]))
               for source in sorted(CANONICAL_NAMES)])


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
    anchored = [
        "%s -> %s" % (name, path)
        for name, path in sorted(mod.CANONICAL_SOURCES.items())
        if os.path.realpath(str(path)) != os.path.realpath(
            H.repo_path("Scripts", name))
    ]
    suite.record(GB, "anchored-on-file", problem_if(
        os.path.realpath(str(mod.SCRIPTS_DIR)) != os.path.realpath(SCRIPTS)
        or anchored,
        "SCRIPTS_DIR=%s / %s do not resolve to the repo's Scripts/"
        % (mod.SCRIPTS_DIR, anchored),
    ))

    # No name may be defined by two canonical sources. A duplicate would make a
    # marker's meaning depend on a field readers skim past, and copying a region
    # from one server to another would then change what it emits.
    seen = {}
    collisions = []
    for source in sorted(mod.CANONICAL_SOURCES):
        for name in sorted(mod.load_blocks(mod.CANONICAL_SOURCES[source])):
            if name in seen:
                collisions.append("%r is defined by both %s and %s"
                                  % (name, seen[name], source))
            seen[name] = source
    suite.record(GB, "sources-disjoint", collisions,
                 detail=["%d block(s) across %d source(s)"
                         % (len(seen), len(mod.CANONICAL_SOURCES))])

    rendered = mod.render(CANONICAL_NAME, ["a", "b"], RENDER_BLOCKS)
    wanted = "def a():\n    pass\n\n\ndef b():\n    pass\n"
    suite.record(GB, "render-layout", problem_if(
        rendered != wanted,
        "render() gave %r, expected two blank lines and one trailing newline"
        % rendered,
    ))

    # An indent shifts every non-blank line and must NOT leave whitespace on a
    # blank one -- trailing whitespace is drift the byte comparison would catch
    # forever after.
    #
    # The indent also chooses the separator: ONE blank line between members of a
    # class body, against the two that `render-layout` above pins at column 0.
    # That is PEP 8's own split, and the indent is already the signal for which
    # side of it we are on -- a region hosted in a class needs no extra flag.
    indented = mod.render(CANONICAL_NAME, ["a", "b"], RENDER_BLOCKS, "    ")
    suite.record(GB, "render-indented", problem_if(
        indented != "    def a():\n        pass\n\n    def b():\n        pass\n",
        "indented render gave %r" % indented,
    ))


def group_control(suite, mod):
    def audit(text, sources=SYNTH_SOURCES):
        return mod.audit_text("synthetic.py", text, sources)

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
    unknown_source = expect_exit(lambda: audit(
        synth_host(SYNTH_BODY, good_sum, source="somewhere-else.py")))
    suite.record(GC, "unknown-source", problem_if(
        not unknown_source,
        "a region naming an unknown source was accepted",
    ), detail=[str(unknown_source)])

    # The refusal has to be actionable: whoever typed the wrong filename learns
    # the right ones here, or goes reading the generator to find them.
    listed_known = expect_exit(lambda: mod.audit_text(
        "synthetic.py",
        synth_host(SYNTH_BODY, good_sum, source="somewhere-else.py"),
        TWO_SOURCES))
    suite.record(GC, "unknown-source-lists-known", problem_if(
        not listed_known or not all(n in listed_known for n in TWO_SOURCES),
        "the refusal must name every source in play; got %r" % listed_known,
    ), detail=[str(listed_known)])

    suite.record(GC, "unknown-name", problem_if(
        not expect_exit(lambda: audit(
            synth_host(SYNTH_BODY, good_sum, names="_not_in_source"))),
        "a region naming a symbol absent from the source was accepted",
    ))

    # A name defined ONLY in the other canonical file must not resolve, and the
    # refusal must name the source that was actually asked -- not the file that
    # happens to define the name, which is the reading that would send somebody
    # to "fix" the wrong end.
    crossed = expect_exit(lambda: mod.audit_text(
        "synthetic.py",
        synth_host(SYNTH_BODY, good_sum, names="_elsewhere"),
        TWO_SOURCES))
    suite.record(GC, "cross-source-name-refused", problem_if(
        not crossed or CANONICAL_NAME not in str(crossed),
        "a name defined only in %s resolved against %s, or the refusal named "
        "the wrong file: %r" % (LSP_CANONICAL_NAME, CANONICAL_NAME, crossed),
    ), detail=[str(crossed)])

    # And the positive half: when BOTH sources define the name, the marker's
    # source decides which body is emitted. Without this, "refused" above could
    # be satisfied by a generator that simply merged the two namespaces and got
    # lucky on the collision.
    picked = mod.audit_text(
        "synthetic.py",
        synth_host(OTHER_BODY, mod.body_hash(OTHER_BODY),
                   source=LSP_CANONICAL_NAME),
        TWO_SOURCES)
    suite.record(GC, "source-selects-the-body", problem_if(
        len(picked) != 1 or picked[0].wanted != OTHER_BODY
        or picked[0].source != LSP_CANONICAL_NAME,
        "the marker's source did not choose the body: %s"
        % [(r.source, r.wanted) for r in picked],
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
        mod.audit_text("amalgamate.py", generator_source, SYNTH_SOURCES),
        "the generator's own docstring markers were treated as regions",
    ), detail=["the live proof: amalgamate.py documents both markers"])

    # 16. A decorator must travel WITH its function. ast puts a decorated
    #     function's lineno on the `def`, so a naive slice would drop
    #     @staticmethod and turn _result into an instance method that eats the
    #     class as its first argument -- a server that raises on its first reply.
    decorated = mod.load_blocks_text(
        "synthetic_source.py",
        "@staticmethod\ndef _result(msg_id, payload):\n    return payload\n",
    )
    suite.record(GC, "decorator-travels", problem_if(
        decorated.get("_result", "").splitlines()[:1] != ["@staticmethod"],
        "the decorator was dropped from the block: %r" % decorated.get("_result"),
    ))

    # 17. A region inside a class body. The BEGIN marker's own column is the
    #     only thing that places it, and that is what makes the _result/_error
    #     methods -- byte-identical in fourteen servers -- reachable at all.
    nested_body = "".join(
        "    %s" % line if line.strip() else line
        for line in SYNTH_BODY.splitlines(keepends=True)
    )
    nested_host = (
        "class Server:\n"
        '    """doc"""\n'
        "    %s %s :: _helper\n" % (BEGIN_PREFIX, CANONICAL_NAME)
        + nested_body
        + "    %s %s\n" % (END_PREFIX, mod.body_hash(nested_body))
    )
    regions = audit(nested_host)
    suite.record(GC, "indented-region", problem_if(
        len(regions) != 1 or regions[0].state != "ok"
        or regions[0].indent != "    ",
        "an indented region should read ok at a 4-space indent, got %s"
        % [(r.state, r.indent) for r in regions],
    ))

    # 18. THE tree.body FILTER, which decides what may be a block at all. It
    #     used to admit definitions only; it now also admits a module-level
    #     CONSTANT, and every other assignment shape is still turned down --
    #     each for its own reason, spelled out on `assign_name`. Both arms are
    #     here because neither proves anything alone: a loader that extracted
    #     nothing would satisfy the refusals, and one that extracted every
    #     statement would satisfy the acceptance.
    #
    #     `GOOD += 1` is placed AFTER `GOOD = 1` on purpose. If the augmented
    #     form were ever admitted it would overwrite the good entry under the
    #     same key, so the name list would still read right and only the BODY
    #     check below would see it -- which is how this shape would slip in.
    assign_source = (
        "GOOD = 1\n"
        "MULTI = ALSO = 2\n"
        "TUPLE_A, TUPLE_B = 3, 4\n"
        "[LIST_A, LIST_B] = 5, 6\n"
        "ANNOTATED: int = 7\n"
        "DECLARED: int\n"
        "GOOD += 1\n"
        "holder.attr = 8\n"
        "holder[0] = 9\n"
        "def _fn():\n"
        "    pass\n"
    )
    extracted = mod.load_blocks_text("synthetic_source.py", assign_source)
    problems = problem_if(
        sorted(extracted) != ["GOOD", "_fn"],
        "the loader extracted %s; exactly the single-Name constant and the "
        "def may be blocks" % sorted(extracted),
    )
    problems += problem_if(
        extracted.get("GOOD") != "GOOD = 1\n",
        "a constant block must be its own statement and nothing else, got %r"
        % extracted.get("GOOD"),
    )
    suite.record(GC, "single-name-assign-only", problems,
                 detail=["extracted: %s" % ", ".join(sorted(extracted))])

    # 19. THE FREE-NAME REFUSAL -- the check that makes a region refuse rather
    #     than emit a server that dies at startup. On the live tree it fires
    #     only incidentally, because every region in it passes, so a silent
    #     failure here would leave the whole suite green. Exercised directly,
    #     and on a CONSTANT: that is the block shape most likely to need a host
    #     import, since a compiled pattern reads `re` where a def usually reads
    #     nothing but its own arguments.
    #
    #     Both arms again, and the second is the load-bearing one: "refused"
    #     alone is satisfied by a generator that refuses every region, which is
    #     indistinguishable from a working check until somebody adds a block.
    pattern_body = '_PATTERN = re.compile(r"^x")\n'
    needs_re = {CANONICAL_NAME: {"_PATTERN": pattern_body}}

    def pattern_host(imports):
        return (
            '"""A synthetic target."""\n' + imports
            + "%s %s :: _PATTERN\n" % (BEGIN_PREFIX, CANONICAL_NAME)
            + "%s\n" % END_PREFIX
        )

    refused = expect_exit(lambda: mod.audit_text(
        "synthetic.py", pattern_host(""), needs_re))
    problems = problem_if(
        not refused,
        "a host that never imports `re` was served a block that reads it -- "
        "that server dies at its first import",
    )
    # The missing symbol is reported as a LIST, so the quotes are part of the
    # match: a bare "re" would also be satisfied by the word "region".
    problems += problem_if(
        refused and "['re']" not in str(refused),
        "the refusal must name the missing symbol; got %r" % refused,
    )
    accepted = mod.audit_text("synthetic.py", pattern_host("import re\n"),
                              needs_re)
    problems += problem_if(
        len(accepted) != 1,
        "the same region was refused to a host that DOES import `re`, so the "
        "refusal above is not about the free name",
    )
    problems += problem_if(
        accepted and accepted[0].wanted != pattern_body,
        "the importing host was served %r"
        % (accepted[0].wanted if accepted else None),
    )
    suite.record(GC, "free-name-refusal", problems, detail=[str(refused)])

    # 15. A file with no region at all is legitimate -- servers convert one at
    #     a time, so this must be silence, not an error.
    suite.record(GC, "no-region-is-fine", problem_if(
        audit('"""Nothing generated here."""\nX = 1\n'),
        "a target with no markers reported a region",
    ))


def group_blocks(suite, blocks, lsp, paging, logmod):
    """Unit-test the canonical modules themselves -- imported, not read as text."""
    falsy = ["", "false", "0", "no", "off", "none", "FALSE", "  Off  "]
    wrong = [v for v in falsy if blocks._bool_param(v) is not False]
    suite.record(GE, "bool-falsy-strings", problem_if(
        wrong, "these should read False: %s" % wrong,
    ))

    # The blacklist semantics are deliberate and load-bearing: an unrecognised
    # string reads True. mcp-webfetch's allow-list variant is the opposite, which
    # is exactly why it never asks for this block.
    truthy = ["1", "true", "yes", "on", "y", "enabled", "2", "anything"]
    wrong = [v for v in truthy if blocks._bool_param(v) is not True]
    suite.record(GE, "bool-unrecognised-is-true", problem_if(
        wrong, "these should read True under blacklist semantics: %s" % wrong,
    ))

    problems = []
    if blocks._bool_param(True) is not True or blocks._bool_param(False) is not False:
        problems.append("a real bool must pass through unchanged")
    if blocks._bool_param(None) is not False:
        problems.append("None must fall back to the default (False)")
    if blocks._bool_param(None, True) is not True:
        problems.append("None must fall back to an explicit True default")
    suite.record(GE, "bool-passthrough-and-default", problems)

    problems = []
    for value, want in (("42", 42), (7, 7), ("-3", -3), (3.9, 3)):
        got = blocks._int_param(value, 99)
        if got != want:
            problems.append("_int_param(%r) gave %r, wanted %r" % (value, got, want))
    suite.record(GE, "int-valid", problems)

    # The infinities are the reason this case is guarded rather than a bare call.
    # `json.loads` turns both `1e999` and the bare `Infinity` token into a float
    # infinity, and `int()` on one raises OverflowError -- neither a TypeError
    # nor a ValueError, so it escaped the fallback and surfaced as an opaque
    # internal error. The case name promises "never raises"; an escape has to be
    # a recorded failure naming the exception, not a traceback that takes the
    # whole suite down before the remaining values are tried.
    problems = []
    for value in ("abc", None, "", [], {}, "1.5",
                  float("inf"), float("-inf"), float("nan")):
        try:
            got = blocks._int_param(value, 99)
        except Exception as exc:                       # noqa: BLE001 -- the point
            problems.append("_int_param(%r) RAISED %s: %s"
                            % (value, type(exc).__name__, exc))
            continue
        if got != 99:
            problems.append("_int_param(%r) gave %r instead of the fallback" % (value, got))
    suite.record(GE, "int-falls-back-never-raises", problems)

    # Row accounting moved OUT of the JSON source too: the line it renders is a
    # sentence for a reader on the last line of a text payload, not a field in
    # an envelope. Same shape of claim as the framing one below, and the same
    # reason to state it where somebody would look for the function.
    suite.record(GE, "json-source-drops-row-accounting", problem_if(
        hasattr(blocks, "_rows_note"),
        "%s still defines _rows_note; it belongs to %s"
        % (CANONICAL_NAME, PAGING_CANONICAL_NAME),
    ))

    note = paging._rows_note
    suite.record(GE, "rows-complete-set", problem_if(
        (note(0, 3, 3), note(0, 1, 1)) != ("[3 rows]", "[1 row]"),
        "complete-set wording drifted: %r / %r" % (note(0, 3, 3), note(0, 1, 1)),
    ))
    # The empty set reaches a DIFFERENT branch from the two above -- `shown <= 0`
    # with `start == 0` -- and that branch is hardcoded plural, which is correct
    # for zero and is the one place the pluralisation is not computed. Nothing
    # else in this group enters it.
    suite.record(GE, "rows-empty-set", problem_if(
        note(0, 0, 0) != "[0 rows]",
        "empty-set wording drifted: %r" % note(0, 0, 0),
    ))
    suite.record(GE, "rows-more-remain", problem_if(
        note(0, 20, 347) != "[showing rows 1-20 of 347; offset=20 for more]",
        "resume-hint wording drifted: %r" % note(0, 20, 347),
    ))
    suite.record(GE, "rows-window-ends", problem_if(
        note(4, 2, 6) != "[showing rows 5-6 of 6; no rows left]",
        "end-of-window wording drifted: %r" % note(4, 2, 6),
    ))
    # Spelled out rather than a 1-based range, which would invert past the end.
    suite.record(GE, "rows-past-end-never-inverts", problem_if(
        note(99, 0, 6) != "[no rows at offset 99 of 6]",
        "offset-past-end wording drifted: %r" % note(99, 0, 6),
    ))
    inexact = note(0, 20, 347, exact=False)
    suite.record(GE, "rows-lower-bound", problem_if(
        "347+ (scan stopped at the ceiling; true total unknown)" not in inexact
        or "offset=20 for more" not in inexact,
        "lower-bound wording drifted: %r" % inexact,
    ))

    # `_offset` is the READ side of the line `_rows_note` prints, which is why
    # the two share a source. Ordinary values first.
    offset = paging._offset
    problems = []
    for args, want in (({}, 0), ({"offset": 7}, 7), ({"offset": "12"}, 12),
                       ({"offset": 0}, 0), ({"offset": 3.9}, 3)):
        got = offset(args)
        if got != want:
            problems.append("_offset(%r) gave %r, wanted %r" % (args, got, want))
    suite.record(GE, "offset-default-and-valid", problems)

    # The 0 floor is the entire reason this is not a bare int(): a negative
    # offset indexes a list from its END, so an unfloored -5 answers "before the
    # first item" with the LAST five -- a wrong answer shaped like a right one.
    problems = []
    for value in (-1, -5, "-5", -0.5, -99999):
        got = offset({"offset": value})
        if got != 0:
            problems.append("_offset(offset=%r) gave %r, not the 0 floor"
                            % (value, got))
    suite.record(GE, "offset-floors-negative", problems)

    # Junk falls back rather than raising: this value comes straight off the
    # wire, and a handler that dies on a typo is worse than one that starts at
    # the beginning. The infinities are junk of the one kind that does not look
    # like a typo -- `1e999` and the bare `Infinity` token both reach this as a
    # float, and `int()` on one raises OverflowError, which is neither of the two
    # this used to catch. It is the same escape `_int_param` carried, in the
    # sibling block, and both are read straight off the wire.
    problems = []
    for value in ("abc", None, "", [], {}, "1.5", " ", True,
                  float("inf"), float("-inf"), float("nan")):
        try:
            got = offset({"offset": value})
        except Exception as exc:                       # noqa: BLE001 -- the point
            problems.append("_offset(offset=%r) RAISED %s: %s"
                            % (value, type(exc).__name__, exc))
            continue
        # True is an int in Python and legitimately reads as 1; everything else
        # here is junk and must read 0.
        want = 1 if value is True else 0
        if got != want:
            problems.append("_offset(offset=%r) gave %r, wanted %r"
                            % (value, got, want))
    suite.record(GE, "offset-junk-never-raises", problems)

    # The two halves closing the loop, on the actual printed text rather than on
    # a number typed twice: take the hint `_rows_note` emits, parse it back out
    # of the line, and feed it to `_offset`. A drift in either half fails here,
    # and the guard below stops the case going quiet if the hint disappears.
    line = note(0, 20, 347)
    problems = []
    if "offset=" not in line:
        problems.append("the page line emits no offset= hint, so this case "
                        "parsed nothing: %r" % line)
    else:
        hinted = line.split("offset=", 1)[1].split(" ", 1)[0]
        back = offset({"offset": hinted})
        if back != 20:
            problems.append("the hint %r read back as %r, not 20" % (hinted, back))
    suite.record(GE, "offset-round-trips-the-page-line", problems,
                 detail=[line])

    # LSP framing moved OUT of the JSON source: it is a different protocol, and
    # the region markers in the four LSP servers now name `_mcp_lsp.py`. If it
    # came back here the two sources would collide, group B would see it -- but
    # this says so at the point a reader is looking for the function.
    suite.record(GE, "json-source-drops-framing", problem_if(
        hasattr(blocks, "encode_lsp_message"),
        "%s still defines encode_lsp_message; it belongs to %s"
        % (CANONICAL_NAME, LSP_CANONICAL_NAME),
    ))

    framed = lsp.encode_lsp_message({"jsonrpc": "2.0", "id": 1})
    header, _, body = framed.partition(b"\r\n\r\n")
    problems = []
    if not header.startswith(b"Content-Length: "):
        problems.append("missing Content-Length header: %r" % header)
    else:
        declared = int(header.split(b": ", 1)[1])
        if declared != len(body):
            problems.append(
                "Content-Length says %d, body is %d BYTES long" % (declared, len(body))
            )
    if json.loads(body.decode("utf-8")) != {"jsonrpc": "2.0", "id": 1}:
        problems.append("body does not round-trip through json.loads")
    suite.record(GE, "lsp-framing-counts-bytes", problems)

    # A non-ASCII payload, and the honest finding: this CANNOT discriminate a
    # byte count from a character count, because `json.dumps` escapes to \\uXXXX
    # by default, so the wire form is ASCII and the two numbers coincide. Saying
    # that out loud is the case's job. The block's `len(encoded)` is already
    # byte-based and only starts to MATTER the day somebody passes
    # ensure_ascii=False -- at which point a character count would under-declare
    # and the peer would read a short frame and desynchronise the stream rather
    # than report anything. Pinning the escaping here is what stops that change
    # landing quietly; relax this case only together with the length arithmetic.
    payload = {"text": "hello — 日本語"}
    framed = lsp.encode_lsp_message(payload)
    header, _, body = framed.partition(b"\r\n\r\n")
    declared = int(header.split(b": ", 1)[1])
    as_text = body.decode("utf-8")
    problems = []
    if declared != len(body):
        problems.append("Content-Length says %d, body is %d bytes"
                        % (declared, len(body)))
    try:
        body.decode("ascii")
    except UnicodeDecodeError:
        problems.append(
            "the wire form is no longer ASCII-escaped, so Content-Length now "
            "has to count ENCODED BYTES -- re-read the block's arithmetic "
            "before relaxing this case"
        )
    if json.loads(as_text) != payload:
        problems.append("a non-ASCII payload does not round-trip")
    suite.record(GE, "lsp-framing-non-ascii-payload", problems,
                 detail=["%d bytes for %d characters on the wire"
                         % (len(body), len(as_text))])

    # One header field, CRLFCRLF-terminated, and BYTES out -- a str return would
    # satisfy every assertion above and then fail at the first writer.write().
    framed = lsp.encode_lsp_message({"id": 2})
    problems = []
    if not isinstance(framed, bytes):
        problems.append("encode_lsp_message returned %s" % type(framed).__name__)
    if framed.count(b"\r\n\r\n") != 1:
        problems.append("expected exactly one header terminator: %r" % framed)
    head = framed.split(b"\r\n\r\n", 1)[0]
    if b"\r\n" in head:
        problems.append("the header carries more than one field: %r" % head)
    suite.record(GE, "lsp-framing-header-shape", problems)

    # _result/_error are staticmethod DESCRIPTORS here, not callables -- they are
    # methods only at their destination. Reaching through __func__ is the point,
    # not a workaround: if the decorator were ever tidied away this would break,
    # and so would every server's first reply.
    result = getattr(blocks._result, "__func__", blocks._result)
    error = getattr(blocks._error, "__func__", blocks._error)
    suite.record(GE, "result-envelope", problem_if(
        result(7, {"a": 2}) != {"jsonrpc": "2.0", "id": 7, "result": {"a": 2}},
        "result envelope drifted: %r" % (result(7, {"a": 2}),),
    ))
    suite.record(GE, "error-envelope", problem_if(
        error(7, -32601, "nope") != {
            "jsonrpc": "2.0", "id": 7,
            "error": {"code": -32601, "message": "nope"},
        },
        "error envelope drifted: %r" % (error(7, -32601, "nope"),),
    ))

    window = blocks._json_error_window
    suite.record(GE, "window-short-no-ellipsis", problem_if(
        window("abc", 1) != "'abc'",
        "a text shorter than the radius should carry no ellipsis: %r"
        % window("abc", 1),
    ))
    long_window = window("x" * 500, 250)
    suite.record(GE, "window-long-both-ellipses", problem_if(
        not (long_window.startswith("...") and long_window.endswith("...")),
        "a mid-text window needs an ellipsis on both sides: %r" % long_window,
    ))
    # The whole reason repr() is in there: a correctly escaped quote must LOOK
    # different from a bare one, which a raw slice renders identically.
    both = window(r'said \"ok\" then rung "1 CB/token" here', 26)
    suite.record(GE, "window-repr-discriminates", problem_if(
        '\\\\"' not in both or '"1 CB/token"' not in both,
        "repr must show the escaped quote as backslash-quote and the broken one "
        "bare; got %r" % both,
    ))
    suite.record(GE, "window-pos-at-end", problem_if(
        window("abcdef", 6) != "'abcdef'",
        "a pos at end-of-text (truncated JSON) must degrade to a left window: %r"
        % window("abcdef", 6),
    ))

    # `_ensure_dict` is the window's one in-region caller, and these three cases
    # are where the co-listing stops being a claim about the generator and turns
    # into a claim about behaviour: the third message below can only be built if
    # the block really does reach `_json_error_window` at run time.
    ensure = blocks._ensure_dict
    problems = []
    for value, want in ((None, {}), ({"a": 1}, {"a": 1}), ({}, {}),
                        ('{"a": 1}', {"a": 1}), ("{}", {})):
        try:
            got = ensure(value)
        except Exception as exc:                       # noqa: BLE001 -- the point
            problems.append("_ensure_dict(%r) RAISED %s: %s"
                            % (value, type(exc).__name__, exc))
            continue
        if got != want:
            problems.append("_ensure_dict(%r) gave %r, wanted %r"
                            % (value, got, want))
    suite.record(GE, "ensure-dict-accepts-none-dict-and-json", problems)

    # Everything that is not an OBJECT must raise, and raise a ValueError that a
    # handler can turn into a reply. The JSON array and the bare JSON scalar are
    # the two that a plain `json.loads` would wave through, which is the whole
    # reason the isinstance check sits after the decode rather than instead of it.
    problems = []
    for value in ("[1, 2]", "5", '"text"', "null", 5, [], ("a",), object()):
        try:
            got = ensure(value)
        except ValueError:
            continue
        except Exception as exc:                       # noqa: BLE001 -- the point
            problems.append("_ensure_dict(%r) raised %s, not ValueError"
                            % (value, type(exc).__name__))
            continue
        problems.append("_ensure_dict(%r) returned %r instead of raising"
                        % (value, got))
    suite.record(GE, "ensure-dict-rejects-non-objects", problems)

    # The message carries the two things a caller cannot reconstruct: WHICH
    # parameter was wrong -- hosts pass their own field name, the default is
    # "params" -- and WHERE the JSON broke. The window is reachable at all only
    # because `value` still holds the caller's string in the except branch:
    # `value = json.loads(value)` binds nothing when it raises.
    problems = []
    try:
        ensure("{broken", "filter")
    except ValueError as exc:
        message = str(exc)
        if "'filter'" not in message:
            problems.append("the message does not name the parameter: %r" % message)
        if "Near the failure:" not in message:
            problems.append("the message carries no window: %r" % message)
        if repr("{broken") not in message:
            problems.append("the window did not repr the caller's own text, so "
                            "`value` was rebound before it was read: %r" % message)
    else:
        problems.append("a non-JSON string did not raise")
    suite.record(GE, "ensure-dict-message-names-field-and-window", problems)

    # `_configure_logging` is measured on the one property that has already cost
    # a fleet-wide commit: `c8b74d0`, "every log file was created world-readable
    # in a world-writable directory", had to touch every server because the mode
    # was written down fifteen times. It is written down once now, so it is
    # pinned once -- and pinned against a real file rather than against a reading
    # of the source, because the mode that matters is the one on disk.
    #
    # What this does NOT assert is deliberate. `logging.basicConfig` is a no-op
    # when the root logger already carries a handler, so a case asserting on the
    # level or the handler would pass or fail on the order the fleet runner
    # happens to use. The `os.open` runs first and unconditionally, which is why
    # the descriptor's mode is the honest thing to measure here.
    log_dir = H.repo_path(".claude", "tmp", "test_generated_region")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "mode.log")
    if os.path.exists(log_path):
        os.unlink(log_path)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        logmod._configure_logging(False, log_path)
        mode = stat.S_IMODE(os.stat(log_path).st_mode)
    finally:
        for handler in root.handlers:
            if handler not in saved_handlers:
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
    suite.record(GE, "log-file-created-0600", problem_if(
        mode != 0o600,
        "the log file was created %04o, not 0600" % mode,
    ), detail=["path: %s" % log_path, "mode: %04o" % mode])


def group_tabs(suite, mod):
    """The tab refusal is PER BLOCK now, so both arms have to be live."""
    sources = mod.load_all_blocks()
    verdicts = {name: mod.block_is_tab_safe(body)
                for blocks in sources.values() for name, body in blocks.items()}
    unsafe = sorted(n for n, ok in verdicts.items() if not ok)
    safe = sorted(n for n, ok in verdicts.items() if ok)

    # Driven by the REAL canonical text, not a synthetic, so the case cannot
    # drift away from the thing it protects. Both arms asserted: a detector that
    # called everything safe would pass a "_rows_note is unsafe" check written
    # the other way round, and one that called everything unsafe would pass a
    # refusal check.
    problems = problem_if(
        unsafe != ["_rows_note"],
        "expected exactly _rows_note to be tab-unsafe, got %s" % unsafe,
    )
    problems += problem_if(
        len(safe) < 2,
        "no tab-SAFE blocks left, so the accept arm proves nothing: %s" % safe,
    )
    suite.record(GF, "tab-safety-real-blocks", problems,
                 detail=["unsafe: %s" % ", ".join(unsafe),
                         "safe: %s" % ", ".join(safe)])

    # The mechanism itself, on shapes the canonical set does not contain -- a
    # backslash continuation and an off-level indent have no live example, and
    # an unexercised branch of a safety check is where the next hole opens.
    cases = [
        ("plain nesting", "def f():\n    if x:\n        return 1\n", True),
        ("bracket join", "def f():\n    return (1 if x\n            else 2)\n", False),
        ("backslash join", "def f():\n    return 1 + \\\n        2\n", False),
        ("off-level indent", "def f():\n      return 1\n", False),
        ("already tabbed", "def f():\n\treturn 1\n", False),
    ]
    problems = ["%s: tab-safe read %r, wanted %r" % (what, mod.block_is_tab_safe(src), want)
                for what, src, want in cases
                if mod.block_is_tab_safe(src) is not want]
    suite.record(GF, "tab-safety-mechanism", problems)

    # The refusal, and the SAME fixture accepting a safe block one line later.
    # Without the second half this case would pass on a generator that refused
    # every block in a tab host, which is the old per-file rule.
    refusal = expect_exit(lambda: mod.audit_text(
        "tabby.py", tab_host("_rows_note"), sources))
    problems = problem_if(not refusal, "a tab host was served _rows_note")
    problems += problem_if(
        refusal and "_rows_note" not in str(refusal),
        "the refusal does not name the block: %r" % refusal,
    )
    accepted = mod.audit_text("tabby.py", tab_host("_offset"), sources)
    problems += problem_if(
        len(accepted) != 1,
        "the same tab fixture refused a tab-SAFE block too, so the refusal "
        "above is the old per-FILE rule, not a per-block one",
    )
    suite.record(GF, "tab-host-refuses-unsafe-by-name", problems,
                 detail=[str(refusal)])

    # Emission: tabs at every structural level, and NOT ONE leading space. A
    # half-converted body would still import and run, and would be invisible in
    # a diff viewer that renders both the same width.
    # Two blocks are driven through PAIRED with the name they read. That is not
    # a fixture convenience: `host_provides` offers a region only the host's
    # module-level IMPORTS, never the names another region defines, so
    # `free_names` refuses either of these in a region of its own and every real
    # host spells them as a two-name marker. Rendering one alone here would
    # assert a shape no server is allowed to use. Dependency first in both, so
    # the fixture emits what a server emits.
    paired = {
        "_max_answer_chars": "DEFAULT_MAX_ANSWER_CHARS, _max_answer_chars",
        WINDOW_RELAY: "%s, %s" % (WINDOW_NAME, WINDOW_RELAY),
    }
    problems = []
    for name in safe:
        source = next(s for s, blocks in sources.items() if name in blocks)
        regions = mod.audit_text(
            "tabby.py", tab_host(paired.get(name, name), source), sources)
        if len(regions) != 1:
            problems.append("%s: expected one region, got %d" % (name, len(regions)))
            continue
        body = regions[0].wanted
        if any(line.startswith(" ") for line in body.splitlines()):
            problems.append("%s: a line still begins with a SPACE" % name)
        # The join is DECLARED here rather than asked of the generator, which
        # would only prove it agrees with itself: two blank lines between
        # top-level definitions is what `render` owes an unindented region.
        expected = "\n\n\n".join(sources[source][n].rstrip("\n")
                                 for n in regions[0].names) + "\n"
        if untab(body) != expected:
            problems.append("%s: de-tabbing does not round-trip to canonical" % name)
    suite.record(GF, "tab-emission-is-all-tabs", problems,
                 detail=["%d block(s) emitted into a tab host" % len(safe)])

    # Host style comes from the host's own INDENT tokens. The marker's column
    # cannot answer it -- every fixture above puts the marker at column 0, which
    # is exactly the case that carries no signal.
    styles = {}
    for path in sorted(Path(SCRIPTS).glob(TARGET_GLOB)):
        styles[path.name] = mod.host_indent(path.read_text(encoding="utf-8"))
    tabbed = sorted(n for n, s in styles.items() if s == "tab")
    problems = problem_if(
        tabbed != ["mcp-forge.py", "mcp-webfetch.py"],
        "expected forge and webfetch to be the tab-indented servers, got %s" % tabbed,
    )
    problems += problem_if(
        mod.host_indent(tab_host("_offset")) != "tab",
        "a column-0 marker in a tab file was not detected as a tab host",
    )
    suite.record(GF, "host-indent-from-tokens", problems,
                 detail=["tab: %s" % ", ".join(tabbed)])

    # The invariant a careless indent refactor breaks first: a SPACE host must
    # be untouched by any of the above. Not one leading tab may appear in what
    # a space host is served, and what it is served must already be on disk.
    problems = []
    for path in sorted(Path(SCRIPTS).glob(TARGET_GLOB)):
        text = path.read_text(encoding="utf-8")
        if mod.host_indent(text) != "space":
            continue
        for region in mod.audit_text(path.name, text, sources):
            if any(line.startswith("\t") for line in region.wanted.splitlines()):
                problems.append("%s [%s]: a TAB leaked into a space host"
                                % (path.name, region.label))
            if region.body != region.wanted:
                problems.append("%s [%s]: no longer byte-identical on disk"
                                % (path.name, region.label))
    suite.record(GF, "space-hosts-take-no-tabs", problems,
                 detail=["%d space-indented server(s) re-rendered"
                         % sum(1 for s in styles.values() if s == "space")])


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
    digests_before = {p: H.sha256_file(p) for p in
                      (SOURCE, LOGGING_SOURCE, LSP_SOURCE, PAGING_SOURCE,
                       GENERATOR, TARGET)}

    mod = H.load_module_from_path("amalgamate_under_test", GENERATOR)
    blocks = H.load_module_from_path("mcp_json_under_test", SOURCE)
    lsp = H.load_module_from_path("mcp_lsp_under_test", LSP_SOURCE)
    paging = H.load_module_from_path("mcp_paging_under_test", PAGING_SOURCE)
    logmod = H.load_module_from_path("mcp_logging_under_test", LOGGING_SOURCE)
    group_gate(suite, mod)
    group_contract(suite, mod)
    group_control(suite, mod)
    group_blocks(suite, blocks, lsp, paging, logmod)
    group_tabs(suite, mod)
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
