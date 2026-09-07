#!/usr/bin/env python3
"""Offline suite for the checkpoint section reader and TOC writer
(ClaudeCode/skills/checkpoint/scripts/checkpoint.py).

THE SAFETY PROBLEM COMES FIRST, BECAUSE THIS IS THE FIRST WRITER UNDER TEST
---------------------------------------------------------------------------
Every command in this script defaults to `.claude/tmp/checkpoint.md`, and on a
developer machine that is not a fixture: it is the live session handoff
document, it is gitignored, and `toc --write` rewrites it in place.  A suite
that ran one command with the default target would destroy work that history
cannot give back.  So the default path is not merely avoided here, it is
UNREACHABLE by construction, three ways:

  * `sandbox_path()` is called on every path this suite hands to the script --
    CLI and in-process alike -- and raises unless the path resolves inside this
    run's `mkdtemp()` sandbox.
  * `run_cli()` refuses to spawn an argv that does not carry `--file`, so a
    forgotten flag is an error in the test rather than a write to the user's
    file, and it runs the child with `cwd=` the sandbox, so even the relative
    default would land inside the sandbox if the two guards above were removed.
  * group K digests the live checkpoint before and after the run and FAILS if a
    single byte moved -- the backstop for the two rules above, and the one case
    in this file that is about the suite rather than about the script.

`prepend` adds a SECOND default path to keep away from
(`.claude/tmp/session-block.md`, its segment file).  Every case below names
its own `--block-file` inside the sandbox; the one case that exercises the
default relies on the child's `cwd` already being the sandbox, so the relative
default resolves inside it -- and group K's repo-tree check is what would catch
it if that ever stopped being true.

WHAT IS WORTH GATING IN A LINE-NUMBER TABLE
-------------------------------------------
`toc` exists so a cold reader can `Read offset=Start limit=End-Start+1` and get
one block instead of a 150-block file.  That makes the ONLY interesting
property a numeric one, and every group below reproduces a way it can be
quietly wrong rather than a hypothetical:

  A  the block model.  `Start` must BE the block's `## ` header line and `End`
     its last non-blank line -- a table whose numbers merely look plausible is
     the failure mode here, because nothing downstream re-derives them.
  B  the range is USABLE.  `lines[Start-1:End]` is compared element-wise
     against what `session S0xx` / `mission` print.  This is the whole feature:
     if the slice and the command disagree, one of them is lying to a cold
     agent that cannot tell which.
  C  the fix point.  `toc --write` inserts a region ABOVE every block, so the
     numbers it writes must describe the file AFTER the insertion, not before.
     The script buys that with two renders (the first only measures the
     region's height) instead of iterating, so the gate is that the region in
     the file is byte-identical to what a fresh `toc` prints from the written
     file -- plus idempotence, because the second write must be a no-op.
  D  byte preservation.  Session blocks are immutable by contract (SKILL.md's
     append-only model).  So the written file minus the region must equal the
     original BYTE for byte -- trailing newline, file mode and non-ASCII bytes
     included, the last one under a deliberately non-UTF-8 locale, since a
     checkpoint may carry verbatim non-English quotes and the whole file is
     re-encoded on every write.
  E  the lifecycle by hand -- fresh file -> write -> insert S002 at the top
     row's `Start` -> write.  This is the four-step sequence group I's
     `prepend` was built to delete, kept and still gated for two reasons:
     `toc --write` remains the migration and repair path, and the manual route
     must keep producing the same file the one-call route does.  Checked end to
     end, including that S001 and MISSION come out byte-identical.
  F  migration of an older, TOC-less file, and the marker-lookalike case: a
     session block that QUOTES the marker comments (which any block describing
     this feature does) must not be mistaken for the region.
  G  the TOC must be invisible to `latest` / `mission` / `session` /
     `next-number`.  Asserted as PARITY -- the same command on the same file
     with and without a region -- not by reading the parser.
  H  every refusal, each gated on all three of: exit 2, the file unchanged to
     the byte, and no `.checkpoint-*.tmp` left behind.  A script that writes
     has no room for a second failure route: a traceback on a directory target,
     or a bare `return 2` with nothing on stderr, both read as "the block was
     empty" to the caller watching stdout.
  I  `prepend`, which exists to delete a discipline.  Adding a session used to
     be four steps across two writers, and the seams were real defects: an
     insert line typed wrong or gone stale, a `toc --write` nobody ran, and the
     window between them where the file and its own table disagreed.  The group
     gates the invariant that replaced them -- ONE call, one os.replace, and
     afterwards the block is in AND the table already describes the file that
     contains it -- plus the two content gates that make the surface safe: the
     five segment refusals (the self-target one pinned separately, it is the
     most harmful) and the duplicate-id gate, which is what makes a stale
     segment at the DEFAULT block path unable to be inserted twice.  A
     `toc --write` right after a prepend must be a byte-identical no-op: that
     is the test of the shared `with_toc()` generator, since two renderers
     would agree only until one of them changed.
  J  NEGATIVE CONTROL.  The oracles of A/B/C are fed deliberately broken row
     tables and a deliberately broken writer -- an `End` that includes trailing
     blanks, an `End` one line short, a `Start` shifted by one, a region
     rendered with offset 0, a strip that leaks a blank line per write -- and
     the group FAILS if any of them is accepted.  The mirror (the real
     implementation passing those same oracles) is recorded alongside, so a
     control that quietly stopped running is visible.
  K  hygiene: the live checkpoint, the repo tree, bytecode, leftover temp
     files.  Runs LAST, after every writing group.

Fixtures are generated into a `tempfile.mkdtemp()` workspace, one subdirectory
per case, because the leftover-temp-file checks read the TARGET's directory and
two cases sharing one would be measuring each other.

Groups:
  A  block model: Start is a header line, End is the last non-blank line
  B  the range is usable: slice == what the reader command prints
  C  toc --write: region placement, the offset fix point, idempotence
  D  byte preservation: blocks, trailing newline, file mode, non-ASCII
  E  lifecycle: fresh -> write -> prepend -> write
  F  migration, and a marker quoted inside a block
  G  the TOC is invisible to the reader commands (parity)
  H  refusals: exit 2, file unchanged, no temp garbage
  I  prepend: block + TOC in one write, segment validation, duplicate ids
  J  negative control
  K  hygiene

Usage:
  python3 tests/test_checkpoint.py
  python3 tests/test_checkpoint.py --brief
Exit code 0 iff every non-informational case passes.
"""

import contextlib
import io
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "checkpoint"
TARGET = H.repo_path("ClaudeCode", "skills", "checkpoint", "scripts",
                     "checkpoint.py")

# The live document this suite must never reach.  Named here ONLY so group J
# can digest it; no other line in this file passes it to anything.
LIVE_CHECKPOINT = H.repo_path(".claude", "tmp", "checkpoint.md")

GA = "A. block model + ranges"
GB = "B. the range is usable"
GC = "C. toc --write fix point"
GD = "D. byte preservation"
GE = "E. lifecycle"
GF = "F. migration"
GG = "G. TOC invisible to readers"
GH = "H. refusals"
GI = "I. prepend: one write"
GJ = "J. negative control"
GK = "K. hygiene"

# The on-disk format contract, spelled out independently of the module: a test
# that imported these would pass a rename that orphaned every region already
# written to disk.
BEGIN_PREFIX = "<!-- TOC:BEGIN"
END_PREFIX = "<!-- TOC:END"
COLUMNS = ("Session", "When", "Branch", "Start", "End")
TMP_PREFIX = ".checkpoint-"
# `prepend`'s segment path when nobody names one.  Spelled out here rather than
# imported, for the same reason as the markers: this suite is the thing that
# notices when a default the skill hard-codes moves underneath it.
DEFAULT_BLOCK_PATH = ".claude/tmp/session-block.md"

# ---------------------------------------------------------------------------
# fixtures.  Line numbers are pinned as constants below and the fixture's own
# shape is asserted against them (case a-fixture-line-map), so a later edit to
# a fixture fails as "the fixture moved" instead of as twenty range failures.
# ---------------------------------------------------------------------------

# 1  # Session Checkpoint          10  ### FILES                19  ## SESSION S001
# 2  (blank)                       11  | Path | ...             20  ### LOG
# 3  ## SESSION S002 ...           12  |------|                 21  1. Bootstrapped...
# 4  _note: ...                    13  | `scripts/...` |        22  (blank)
# 5  (blank)                       14  (blank)                  23  ### NEXT
# 6  ### LOG                       15  ### NEXT                 24  1. Add a line-...
# 7  1. Added the TOC ...          16  1. Write the ...         25  (blank)
# 8  2. Wrote the two-render ...   17  (blank)                  26  ## MISSION
# 9  (blank)                       18  (blank)                  ...
#
# 35  - **Out of scope:** ...      36  (blank)  <- a block ending at EOF still
#                                                  has its trailing blank
#                                                  trimmed off its End
FIXTURE_MAIN = """\
# Session Checkpoint

## SESSION S002 | 2026-09-06 18:20 | feature/toc
_note: keep the two-render trick_

### LOG
1. Added the TOC region writer.
2. Wrote the two-render offset trick.

### FILES
| Path | What | Status |
|------|------|--------|
| `scripts/checkpoint.py` | toc --write | modified |

### NEXT
1. Write the functional suite.


## SESSION S001 | 2026-09-05 09:15 | master
### LOG
1. Bootstrapped the checkpoint model.

### NEXT
1. Add a line-range table of contents.

## MISSION

Keep the session handoff cheap to reread.

### WHY
A cold session must resume without reading every block.

### SCOPE
- **In scope:** the header contract
- **Out of scope:** rewriting an existing block

"""

MAIN_LINES = 36

# label -> (when, branch, start, end) in FIXTURE_MAIN, newest block first.
MAIN_ROWS = [
    ("S002", "2026-09-06 18:20", "feature/toc", 3, 16),
    ("S001", "2026-09-05 09:15", "master", 19, 24),
    ("MISSION", "", "", 26, 35),
]
# What the same table must say once the region is in the file: three rows means
# a 7-line region plus its blank separator, so everything below shifts by 8.
MAIN_SHIFT = 8
MAIN_ROWS_WRITTEN = [(label, when, branch, start + MAIN_SHIFT,
                      end + MAIN_SHIFT)
                     for label, when, branch, start, end in MAIN_ROWS]

# The lifecycle fixture: what a first-ever `/p:checkpoint` writes.
FIXTURE_FRESH = """\
# Session Checkpoint

## SESSION S001 | 2026-09-05 09:15 | master
### LOG
1. Bootstrapped the checkpoint model.

### NEXT
1. Add a line-range table of contents.

## MISSION

Keep the session handoff cheap to reread.

### WHY
A cold session must resume without reading every block.
"""

# The block a prepend inserts, trailing blank included: it is what separates it
# from the block below, and `End` must therefore not point at it.
NEW_BLOCK = [
    "## SESSION S002 | 2026-09-06 18:20 | feature/toc",
    "### LOG",
    "1. Added the TOC region writer.",
    "",
    "### NEXT",
    "1. Write the functional suite.",
    "",
]

# A checkpoint may carry verbatim non-English quotes (SKILL.md rule 8a), so the
# file is not ASCII by contract -- and every write re-encodes the whole thing.
FIXTURE_UTF8 = """\
# Session Checkpoint

## SESSION S001 | 2026-09-05 09:15 | master
_note: "arvizturo tukorfurogep" -- arvizturo tukorfurogep_

### LOG
1. A felhasznalo kerte: "csak a checkpoint.py-t modosithatod".
2. Naive quotes: árvíztűrő tükörfúrógép, — em dash, → arrow.

## MISSION

Kerdes: mi legyen a kovetkezo lepes? Árvíztűrő.
"""

# ---------------------------------------------------------------------------
# segments for `prepend`.  The skill writes one of these to a temp file and
# hands the PATH over; the text never goes through the command line.
# ---------------------------------------------------------------------------

SEGMENT_ONE = """\
## SESSION S003 | 2026-09-07 08:05 | feature/prepend
### LOG
1. Moved the insertion into the script.

### NEXT
1. Gate the atomicity.

"""

# What a first-ever checkpoint hands over: the S001 block AND the frozen tail.
SEGMENT_FRESH = """\
## SESSION S001 | 2026-09-07 08:05 | feature/prepend
### LOG
1. First session on this work stream.

### NEXT
1. Keep going.

## MISSION

Make the handoff cheap to reread.

### WHY
A cold session must resume without reading every block.
"""

SEGMENT_TWO_BLOCKS = """\
## SESSION S004 | 2026-09-07 09:00 | feature/prepend
### LOG
1. Newer of the two.

## SESSION S003 | 2026-09-07 08:05 | feature/prepend
### LOG
1. Older of the two.
"""

# The block that documents this very feature -- the shape that used to eat
# itself.  Prepending it must not let the quoted markers pass for the region.
SEGMENT_QUOTED_MARKER = """\
## SESSION S003 | 2026-09-07 08:05 | feature/prepend
### MODEL
- The generated region is delimited by two comments:
%s -- generated by checkpoint.py toc --write; do not edit by hand -->
%s -->
- Only the script writes them.
""" % (BEGIN_PREFIX, END_PREFIX)

# An id that collides with a block in the MIDDLE of FIXTURE_MAIN (S001 is its
# bottom session block), so accepting it would prove the gate only looks at the
# top of the file.
SEGMENT_CLASH_MID = """\
## SESSION S001 | 2026-09-07 08:05 | feature/prepend
### LOG
1. Re-using an id that is already down there.
"""

# A second frozen tail, which the write-once rule forbids.
SEGMENT_CLASH_MISSION = """\
## MISSION

A second mission tail, which must never be written.
"""

SEGMENT_NO_HEADER = """\
### LOG
1. A subsection with no block header above it.
"""

SEGMENT_WITH_H1 = """\
## SESSION S003 | 2026-09-07 08:05 | feature/prepend
# Session Checkpoint
### LOG
1. Carries a second title.
"""

SEGMENT_BLANK = "\n   \n\t\n\n"


# An old-format file: no region, and a block that QUOTES the marker comments.
# Any session block documenting this very feature looks like this.
FIXTURE_QUOTED_MARKER = """\
# Session Checkpoint

## SESSION S001 | 2026-09-05 09:15 | master
### MODEL
- The generated region is delimited by two comments:
%s -- generated by checkpoint.py toc --write; do not edit by hand -->
| Session | When | Branch | Start | End |
%s -->
- Everything above the first `## ` line is preamble.

## MISSION

Keep the session handoff cheap to reread.
""" % (BEGIN_PREFIX, END_PREFIX)


# ---------------------------------------------------------------------------
# the sandbox guard -- see the module docstring
# ---------------------------------------------------------------------------

_SANDBOX = None


def set_sandbox(path):
    global _SANDBOX
    _SANDBOX = os.path.realpath(path)


def sandbox_path(path):
    """Return `path`, or raise if it is not inside this run's sandbox.

    Called on EVERY path handed to the script under test.  The script's default
    target is the user's live checkpoint; this is what makes reaching it
    impossible rather than unlikely.
    """
    real = os.path.realpath(path)
    if _SANDBOX is None:
        raise AssertionError("no sandbox is active; refusing to touch %r"
                             % (path,))
    if real != _SANDBOX and not real.startswith(_SANDBOX + os.sep):
        raise AssertionError("refusing to touch %r: outside the sandbox %r"
                             % (path, _SANDBOX))
    return path


def stage(workspace, case, body):
    """A fixture in its OWN directory: the leftover-temp-file checks read the
    target's directory, so two cases sharing one would test each other."""
    return sandbox_path(workspace.write_text(os.path.join(case,
                                                          "checkpoint.md"),
                                             body))


# ---------------------------------------------------------------------------
# plumbing: reading, writing and running, all independent of the module
# ---------------------------------------------------------------------------

def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def write_text(path, text):
    sandbox_path(path)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def lines_of(text):
    """`text` as lines, split on "\\n" ONLY.

    Deliberately not str.splitlines(): that is what the module uses, and an
    oracle that shares the implementation's splitter cannot see the difference
    between a file and its re-split copy.
    """
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def file_lines(path):
    return lines_of(read_text(path))


def run_cli(*args, **kwargs):
    """Run the script as a child process. Refuses an argv without `--file`."""
    env_extra = kwargs.pop("env_extra", None)
    timeout = kwargs.pop("timeout", 30)
    if kwargs:
        raise TypeError("unexpected kwargs: %r" % (kwargs,))
    argv = [str(a) for a in args]
    if "--file" not in argv:
        raise AssertionError("every invocation must name its own --file, or it "
                             "would target the user's live checkpoint: %r"
                             % (argv,))
    target = sandbox_path(argv[argv.index("--file") + 1])
    proc = subprocess.run([sys.executable, TARGET] + argv, input="",
                          capture_output=True, text=True, timeout=timeout,
                          cwd=os.path.dirname(os.path.abspath(target)),
                          env=H.child_env(env_extra))
    return proc.returncode, proc.stdout, proc.stderr


@contextlib.contextmanager
def captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def call_module(mod, name, path, *args, **kwargs):
    """Call a cmd_* helper in-process on a sandbox path.

    `die()` raises SystemExit, so the refusals come back as an exit code here
    exactly as they do from the CLI.
    """
    sandbox_path(path)
    fn = getattr(mod, name)
    with captured() as (out, err):
        try:
            code = fn(path, *args, **kwargs)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def problem_if(condition, message):
    return [message] if condition else []


def missing_tokens(text, tokens):
    return [t for t in tokens if t not in text]


# ---------------------------------------------------------------------------
# region helpers
# ---------------------------------------------------------------------------

def region_bounds(lines):
    """(begin, end) 0-based inclusive indices of the TOC region, or (-1, -1)."""
    begin = end = -1
    for index, line in enumerate(lines):
        if begin < 0 and line.startswith(BEGIN_PREFIX):
            begin = index
        elif begin >= 0 and line.startswith(END_PREFIX):
            end = index
            break
    return (begin, end) if end >= 0 else (-1, -1)


def region_lines(lines):
    begin, end = region_bounds(lines)
    return [] if begin < 0 else lines[begin:end + 1]


def strip_region(text):
    """`text` minus the TOC region AND the blank separator above it.

    The exact inverse of what `toc --write` inserts, so what comes back must be
    the original file byte for byte -- which is how group D asserts that a
    session block was preserved instead of merely looking right.
    """
    lines = lines_of(text)
    begin, end = region_bounds(lines)
    if begin < 0:
        return text
    cut = begin - 1 if begin > 0 and not lines[begin - 1].strip() else begin
    kept = lines[:cut] + lines[end + 1:]
    return "\n".join(kept) + ("\n" if text.endswith("\n") else "")


def table_cells(line):
    """The cells of a `| a | b |` row, stripped."""
    parts = line.split("|")
    return tuple(p.strip() for p in parts[1:-1])


def parsed_rows(region):
    """The region's data rows as tuples of strings, markers/header/rule out."""
    return [table_cells(line) for line in region[3:-1]]


def temp_leftovers(path):
    """Half-written `.checkpoint-*.tmp` files next to the target."""
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        return []
    return sorted(n for n in os.listdir(directory)
                  if n.startswith(TMP_PREFIX))


def stale_temp_files(root):
    """Every leftover temp file anywhere under the sandbox."""
    found = []
    for dirpath, _dirs, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        found += [os.path.join(rel, name) for name in sorted(files)
                  if name.startswith(TMP_PREFIX)]
    return sorted(found)


def label_of_header(header):
    """The TOC label a `## ` header line must carry.

    A second, independent reading of the header contract: the oracle below
    would accept any label at all if it asked the module.
    """
    text = header[3:].strip() if header.startswith("## ") else header.strip()
    if text == "MISSION" or text.startswith("MISSION "):
        return "MISSION"
    match = re.match(r"^SESSION\s+S0*(\d+)\b", text)
    return "S%03d" % int(match.group(1)) if match else "?"


def rows_of(mod, path):
    """The module's own table for a file, as (label, when, branch, start, end)
    with the numbers left as integers."""
    return [tuple(row) for row in mod.toc_rows(mod.load_blocks(path))]


# ---------------------------------------------------------------------------
# the oracles.  Group I feeds every one of them a broken input.
# ---------------------------------------------------------------------------

def check_ranges(rows, lines):
    """Start must BE the block's header line; End its last non-blank line.

    The second half is what makes `limit=End-Start+1` land on the block and
    NOTHING else, so it is asserted in both directions: End is non-blank, and
    every line between End and the next block is blank.
    """
    problems = []
    starts = sorted(row[3] for row in rows)
    for label, _when, _branch, start, end in rows:
        if not 1 <= start <= len(lines):
            problems.append("%s: start %d outside 1..%d"
                            % (label, start, len(lines)))
            continue
        if not start <= end <= len(lines):
            problems.append("%s: end %d outside %d..%d"
                            % (label, end, start, len(lines)))
            continue
        header = lines[start - 1]
        if not header.startswith("## "):
            problems.append("%s: line %d is not a block header: %r"
                            % (label, start, header))
        elif label_of_header(header) != label:
            problems.append("%s: line %d is the header of %s"
                            % (label, start, label_of_header(header)))
        if not lines[end - 1].strip():
            problems.append("%s: end line %d is blank: %r"
                            % (label, end, lines[end - 1]))
        following = next((s for s in starts if s > start), len(lines) + 1)
        spill = [line for line in lines[end:following - 1] if line.strip()]
        if spill:
            problems.append("%s: %d non-blank line(s) between end %d and the "
                            "next block: %r" % (label, len(spill), end,
                                                spill[:2]))
    return problems


def check_slices(rows, lines, printed):
    """`lines[Start-1:End]` must equal what the reader command printed.

    This is the feature's entire promise: one `Read offset=Start
    limit=End-Start+1` returns the block, and the same bytes the script would
    have printed.
    """
    problems = []
    for label, _when, _branch, start, end in rows:
        want = printed.get(label)
        if want is None:
            problems.append("%s: the reader command produced nothing to "
                            "compare against" % label)
            continue
        got = lines[start - 1:end]
        if got == want:
            continue
        first = next((i for i in range(max(len(got), len(want)))
                      if got[i:i + 1] != want[i:i + 1]), 0)
        problems.append("%s: lines[%d:%d] is %d line(s), the command printed "
                        "%d; first difference at slice line %d: %r vs %r"
                        % (label, start, end, len(got), len(want), first + 1,
                           got[first:first + 1], want[first:first + 1]))
    return problems


def check_fixpoint(region, fresh_toc):
    """The region IN the file must equal what `toc` prints FROM that file.

    Equivalent to "the numbers describe the file that contains them" -- the one
    property a region written before the insertion cannot have.
    """
    problems = []
    if not region:
        return ["there is no TOC region in the file"]
    if region != fresh_toc:
        pairs = list(zip(region, fresh_toc))
        first = next((i for i, (a, b) in enumerate(pairs) if a != b),
                     min(len(region), len(fresh_toc)))
        problems.append("the written region and a fresh `toc` disagree at line "
                        "%d of the region: %r vs %r"
                        % (first + 1, region[first:first + 1],
                           fresh_toc[first:first + 1]))
    inside = [line for line in region if line.startswith("## ")]
    if inside:
        problems.append("a `## ` line inside the region would become a block: "
                        "%r" % inside[:2])
    return problems


def check_idempotent(first, second):
    """A second `toc --write` must be a byte-for-byte no-op."""
    if first == second:
        return []
    return ["the second write changed the file: %d bytes -> %d bytes"
            % (len(first), len(second))]


def check_preserved(original, written):
    """The written file minus its region must be the original, byte for byte."""
    problems = []
    stripped = strip_region(written)
    if stripped != original:
        want, got = lines_of(original), lines_of(stripped)
        first = next((i for i in range(max(len(want), len(got)))
                      if want[i:i + 1] != got[i:i + 1]), 0)
        problems.append("file minus the region != the original (%d vs %d "
                        "lines); first difference at line %d: %r vs %r"
                        % (len(want), len(got), first + 1,
                           want[first:first + 1], got[first:first + 1]))
    if original.endswith("\n") != written.endswith("\n"):
        problems.append("the trailing newline changed: %r -> %r"
                        % (original[-1:], written[-1:]))
    return problems


def printed_blocks(mod, path, labels):
    """label -> the lines the reader command prints for that block."""
    out = {}
    for label in labels:
        if label == "MISSION":
            code, text, _err = call_module(mod, "cmd_mission", path)
        else:
            code, text, _err = call_module(mod, "cmd_session", path, label)
        out[label] = lines_of(text) if code == 0 else None
    return out


# ---------------------------------------------------------------------------
# A. block model: Start is a header line, End is the last non-blank line
# ---------------------------------------------------------------------------

def group_a(suite, mod, workspace):
    path = stage(workspace, "a", FIXTURE_MAIN)
    lines = file_lines(path)

    # The fixture itself, first: every expectation below is a line number, and
    # a fixture edit must fail as "the fixture moved", once, not as ten range
    # failures pointing at the script.
    problems = []
    for label, _when, _branch, start, _end in MAIN_ROWS:
        got = label_of_header(lines[start - 1]) if start <= len(lines) else "?"
        if got != label:
            problems.append("line %d is %s, the map says %s"
                            % (start, got, label))
    problems += problem_if(len(lines) != MAIN_LINES,
                           "the fixture is %d lines, the map says %d"
                           % (len(lines), MAIN_LINES))
    suite.record(GA, "a-fixture-line-map", problems,
                 detail=["%d lines; %s" % (len(lines), MAIN_ROWS)])

    blocks = mod.load_blocks(path)
    suite.record(GA, "a-parse-finds-every-block",
                 problem_if(len(blocks) != 3,
                            "%d block(s), want 3" % len(blocks)),
                 detail=["headers: %r" % [mod.header_of(b) for b in blocks]])

    preamble = [b for b in blocks if b.start < 3]
    suite.record(GA, "a-preamble-is-not-a-block", problems=problem_if(
        preamble, "the H1 / preamble was parsed as a block"),
        detail=["everything above the first `## ` is ignored, which is what "
                "lets the generated region live there"])

    rows = rows_of(mod, path)
    suite.record(GA, "a-rows-match-the-header-fields",
                 problem_if(rows != [tuple(r) for r in MAIN_ROWS],
                            "rows are %r, want %r" % (rows, MAIN_ROWS)),
                 detail=["newest block first; when/branch come from the "
                         "pipe-separated header"])

    suite.record(GA, "a-ranges-are-well-formed", check_ranges(rows, lines),
                 detail=["Start is the `## ` line, End the last non-blank one"])

    for label, _when, _branch, start, end in MAIN_ROWS:
        got = label_of_header(lines[start - 1])
        suite.record(GA, "a-start-is-the-header-of-%s" % label,
                     problem_if(got != label,
                                "line %d is the header of %s" % (start, got)),
                     detail=["line %d: %r" % (start, lines[start - 1])])

    for label, _when, _branch, _start, end in MAIN_ROWS:
        problems = problem_if(not lines[end - 1].strip(),
                              "end line %d is blank" % end)
        after = lines[end:end + 1]
        problems += problem_if(after and after[0].strip(),
                               "line %d after the end is not blank: %r"
                               % (end + 1, after))
        suite.record(GA, "a-end-is-the-last-non-blank-of-%s" % label, problems,
                     detail=["line %d: %r" % (end, lines[end - 1]),
                             "line %d: %r" % (end + 1, after or ["<eof>"])])

    header = mod.header_of(blocks[0])
    suite.record(GA, "a-header-of-strips-the-hashes",
                 problem_if(header != "SESSION S002 | 2026-09-06 18:20 | "
                                      "feature/toc", "header_of: %r" % header))


# ---------------------------------------------------------------------------
# B. the range is usable
# ---------------------------------------------------------------------------

def group_b(suite, mod, workspace):
    plain = stage(workspace, "b-plain", FIXTURE_MAIN)
    written = stage(workspace, "b-written", FIXTURE_MAIN)
    code, _out, err = call_module(mod, "cmd_toc", written, write=True)
    if code != 0:
        suite.record(GB, "b-write-precondition",
                     ["toc --write exited %r: %s" % (code, err.strip())])
        return

    for variant, path in (("no-toc", plain), ("with-toc", written)):
        lines = file_lines(path)
        rows = rows_of(mod, path)
        printed = printed_blocks(mod, path, [r[0] for r in rows])
        problems = check_slices(rows, lines, printed)
        suite.record(GB, "b-every-range-is-readable-%s" % variant, problems,
                     detail=["%d row(s) compared against `session` / `mission`"
                             % len(rows),
                             "one Read offset=Start limit=End-Start+1 per row "
                             "must return the block and nothing else"])
        for label, _when, _branch, start, end in rows:
            want = printed.get(label)
            got = lines[start - 1:end]
            suite.record(GB, "b-slice-equals-command-%s-%s" % (label, variant),
                         check_slices([(label, "", "", start, end)], lines,
                                      {label: want}),
                         detail=["lines[%d:%d] -> %d line(s)"
                                 % (start, end, len(got)),
                                 "first: %r" % (got[:1] or [""])[0],
                                 "last : %r" % (got[-1:] or [""])[0]])

        # The mirror of "and nothing else": one line PAST End must not still
        # equal the block, which is what an End that swallowed the trailing
        # blank line looks like from the outside.
        problems = []
        for label, _when, _branch, start, end in rows:
            if lines[start - 1:end + 1] == printed.get(label):
                problems.append("%s: lines[%d:%d] also equals the block, so "
                                "End is one line short" % (label, start,
                                                           end + 1))
        suite.record(GB, "b-end-is-not-one-line-short-%s" % variant, problems,
                     detail=["End+1 must add a blank line or the next header"])


# ---------------------------------------------------------------------------
# C. toc --write: placement, the offset fix point, idempotence
# ---------------------------------------------------------------------------

def group_c(suite, mod, workspace):
    path = stage(workspace, "c", FIXTURE_MAIN)
    original = read_text(path)
    code, out, err = run_cli("toc", "--write", "--file", path)
    if code != 0:
        suite.record(GC, "c-write-precondition",
                     ["exit %d: %s" % (code, err.strip())])
        return
    written = read_text(path)
    lines = lines_of(written)
    region = region_lines(lines)
    begin, end = region_bounds(lines)

    suite.record(GC, "c-markers-are-the-documented-comments",
                 problem_if(not region
                            or not region[0].startswith(BEGIN_PREFIX)
                            or not region[-1].startswith(END_PREFIX),
                            "region delimiters are %r"
                            % ([region[:1], region[-1:]] if region else [])),
                 detail=["%r" % (region[0] if region else None),
                         "%r" % (region[-1] if region else None)])

    problems = problem_if(lines[0] != "# Session Checkpoint",
                          "line 1 is no longer the H1: %r" % lines[0])
    problems += problem_if(begin != 2, "the region starts at line %d, want 3"
                           % (begin + 1))
    problems += problem_if(lines[1].strip(),
                           "line 2 is not the blank separator: %r" % lines[1])
    first_block = next((i for i, line in enumerate(lines)
                        if line.startswith("## ")), -1)
    problems += problem_if(not 0 <= end < first_block,
                           "the region does not close above the first block "
                           "(end %d, first block %d)" % (end, first_block))
    suite.record(GC, "c-region-sits-between-the-h1-and-the-first-block",
                 problems,
                 detail=["H1 on line 1, blank on 2, region on %d..%d, first "
                         "block on %d" % (begin + 1, end + 1, first_block + 1)])

    suite.record(GC, "c-region-height-is-rows-plus-four",
                 problem_if(len(region) != len(MAIN_ROWS) + 4,
                            "%d line(s) for %d row(s)"
                            % (len(region), len(MAIN_ROWS))),
                 detail=["two markers + header + rule + one line per row; the "
                         "height must not depend on the numbers, or the "
                         "two-render offset would be wrong"])

    got_columns = table_cells(region[1]) if len(region) > 1 else None
    suite.record(GC, "c-column-header-is-the-contract",
                 problem_if(len(region) < 3 or got_columns != COLUMNS,
                            "header row is %r, want %r"
                            % (got_columns, COLUMNS)),
                 detail=["%r" % (got_columns,)])

    got_rows = parsed_rows(region)
    want_rows = [(label, when, branch, str(start), str(end))
                 for label, when, branch, start, end in MAIN_ROWS_WRITTEN]
    suite.record(GC, "c-numbers-are-the-post-write-ones",
                 problem_if(got_rows != want_rows,
                            "rows are %r, want %r" % (got_rows, want_rows)),
                 detail=["the region shifts every block down by %d lines, so "
                         "the numbers it writes must already include that"
                         % MAIN_SHIFT])

    widths = sorted({len(line) for line in region[1:-1]})
    suite.record(GC, "c-table-rows-are-aligned",
                 problem_if(len(widths) != 1,
                            "rows have %d different widths: %r"
                            % (len(widths), widths)),
                 detail=["padded so it reads straight out of an editor"])

    _code, fresh, _err = run_cli("toc", "--file", path)
    suite.record(GC, "c-region-equals-a-fresh-toc-of-the-written-file",
                 check_fixpoint(region, lines_of(fresh)),
                 detail=["THE fix point: the table in the file and the table "
                         "derived from the file must be the same bytes"])

    suite.record(GC, "c-ranges-of-the-written-file-are-well-formed",
                 check_ranges(rows_of(mod, path), lines),
                 detail=["re-derived from the file that now contains the "
                         "region"])

    suite.record(GC, "c-stdout-reports-what-it-wrote",
                 missing_tokens(out, ["3 entries", "7 lines"]),
                 detail=[out.strip()])

    code2, _out2, err2 = run_cli("toc", "--write", "--file", path)
    twice = read_text(path)
    problems = problem_if(code2 != 0, "the second write exited %d: %s"
                          % (code2, err2.strip()))
    problems += check_idempotent(written, twice)
    suite.record(GC, "c-second-write-is-byte-identical", problems,
                 detail=["strip the region, re-parse, re-render: the second "
                         "run must be a no-op"])

    run_cli("toc", "--write", "--file", path)
    suite.record(GC, "c-third-write-is-byte-identical",
                 check_idempotent(written, read_text(path)))

    markers = [line for line in file_lines(path)
               if line.startswith(BEGIN_PREFIX)]
    suite.record(GC, "c-regions-never-nest",
                 problem_if(len(markers) != 1,
                            "%d BEGIN marker(s) after three writes"
                            % len(markers)))

    suite.record(GC, "c-the-original-is-still-in-there",
                 check_preserved(original, read_text(path)),
                 detail=["three writes later, the file minus the region is "
                         "still the fixture"])


# ---------------------------------------------------------------------------
# D. byte preservation
# ---------------------------------------------------------------------------

C_LOCALE = {
    # Deterministically NOT UTF-8 on macOS and on Linux: the C locale, with
    # PEP 538 coercion and PEP 540 UTF-8 mode both switched off.  A script that
    # opens its file with the locale's encoding re-encodes -- or fails on --
    # every non-ASCII byte in it.  PYTHONIOENCODING is pinned separately so
    # this case is about the FILE, not about the terminal.
    "LC_ALL": "C",
    "LANG": "C",
    "LC_CTYPE": "C",
    "PYTHONUTF8": "0",
    "PYTHONCOERCECLOCALE": "0",
    "PYTHONIOENCODING": "utf-8",
}


def group_d(suite, mod, workspace):
    path = stage(workspace, "d", FIXTURE_MAIN)
    original = read_text(path)
    os.chmod(path, 0o640)  # not 0o600: mkstemp's own mode must not pass
    code, _out, err = run_cli("toc", "--write", "--file", path)
    if code != 0:
        suite.record(GD, "d-write-precondition",
                     ["exit %d: %s" % (code, err.strip())])
        return
    written = read_text(path)

    suite.record(GD, "d-everything-outside-the-region-is-preserved",
                 check_preserved(original, written),
                 detail=["session blocks are immutable by contract; the region "
                         "is the only derived thing in the file"])

    suite.record(GD, "d-trailing-newline-is-preserved",
                 problem_if(written[-4:] != original[-4:],
                            "the file used to end %r, now ends %r"
                            % (original[-4:], written[-4:])),
                 detail=["the fixture ends with a blank line, so the tail is "
                         "%r both before and after" % original[-4:]])

    lines = lines_of(written)
    suite.record(GD, "d-line-count-grows-by-the-region-only",
                 problem_if(len(lines) != MAIN_LINES + MAIN_SHIFT,
                            "%d lines, want %d"
                            % (len(lines), MAIN_LINES + MAIN_SHIFT)))

    before = lines_of(original)
    for label, _when, _branch, start, end in MAIN_ROWS:
        want = before[start - 1:end]
        got = lines[start - 1 + MAIN_SHIFT:end + MAIN_SHIFT]
        suite.record(GD, "d-block-is-byte-identical-%s" % label,
                     problem_if(got != want,
                                "%s changed: %r != %r"
                                % (label, got[:2], want[:2])),
                     detail=["%d line(s), moved down by %d"
                             % (len(want), MAIN_SHIFT)])

    mode = os.stat(path).st_mode & 0o777
    suite.record(GD, "d-file-mode-survives-the-replace",
                 problem_if(mode != 0o640, "mode is 0o%o, want 0o640" % mode),
                 detail=["mkstemp creates 0600; the file being replaced is "
                         "not, so the mode is restored before the rename"])

    suite.record(GD, "d-no-temp-file-left-after-a-successful-write",
                 problem_if(temp_leftovers(path),
                            "left behind: %r" % temp_leftovers(path)))

    utf8 = stage(workspace, "d-utf8", FIXTURE_UTF8)
    utf8_original = read_text(utf8)
    code, _out, err = run_cli("toc", "--write", "--file", utf8)
    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    if code == 0:
        problems += check_preserved(utf8_original, read_text(utf8))
    suite.record(GD, "d-non-ascii-body-survives-a-write", problems,
                 detail=["a checkpoint may carry verbatim non-English quotes "
                         "(SKILL.md rule 8a), and every write re-encodes the "
                         "whole file"])

    c_locale = stage(workspace, "d-c-locale", FIXTURE_UTF8)
    c_original = read_text(c_locale)
    code, _out, err = run_cli("toc", "--write", "--file", c_locale,
                              env_extra=C_LOCALE)
    problems = problem_if(code != 0, "exit %d: %s"
                          % (code, err.strip().splitlines()[-1:] or ""))
    if code == 0:
        problems += check_preserved(c_original, read_text(c_locale))
    suite.record(GD, "d-non-ascii-body-survives-a-c-locale-write", problems,
                 detail=["LC_ALL=C with PEP 538 coercion and UTF-8 mode off: "
                         "the locale encoding is ASCII, so a file opened "
                         "without an explicit encoding cannot even be read",
                         "the file's encoding must not depend on the "
                         "environment of whoever runs the script"])

    # Under-specified on purpose: a file with no trailing newline is not what
    # the skill writes, and normalising one in is harmless.  Recorded so the
    # behaviour is written down somewhere other than in the reader's head.
    bare = stage(workspace, "d-bare", FIXTURE_FRESH.rstrip("\n"))
    code, _out, _err = run_cli("toc", "--write", "--file", bare)
    text = read_text(bare)
    suite.record(GD, "d-file-with-no-trailing-newline", [], status=H.INFO,
                 detail=["exit %d; the file now ends %r" % (code, text[-2:]),
                         "the write joins lines and appends one newline, so a "
                         "missing one is normalised IN -- INFO, not a gate: "
                         "the skill's own writer always ends with a newline"])


# ---------------------------------------------------------------------------
# E. lifecycle: fresh -> write -> prepend -> write
# ---------------------------------------------------------------------------

def insert_at_line(path, one_indexed, block_lines):
    """What the skill's `insert_at_line` prepend does: put these lines AT that
    line and push the rest down.  Nothing else in the file moves."""
    text = read_text(path)
    lines = lines_of(text)
    kept = lines[:one_indexed - 1] + list(block_lines) + lines[one_indexed - 1:]
    write_text(path, "\n".join(kept) + ("\n" if text.endswith("\n") else ""))


def group_e(suite, mod, workspace):
    path = stage(workspace, "e", FIXTURE_FRESH)
    fresh_lines = file_lines(path)

    code, _out, err = run_cli("toc", "--write", "--file", path)
    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    rows = rows_of(mod, path)
    problems += problem_if([r[0] for r in rows] != ["S001", "MISSION"],
                           "rows are %r" % (rows,))
    problems += check_ranges(rows, file_lines(path))
    suite.record(GE, "e-first-write-installs-the-region", problems,
                 detail=["a first-ever checkpoint has no region; this is what "
                         "creates it", "%r" % (rows,)])
    if code != 0:
        return

    # The documented prepend: the insertion line is the top row's Start, NOT
    # "just below the H1" -- which would land inside the generated table.
    top_start = rows[0][3]
    insert_at_line(path, top_start, NEW_BLOCK)
    after_insert = file_lines(path)
    suite.record(GE, "e-prepend-lands-on-the-top-rows-start-line",
                 problem_if(after_insert[top_start - 1] != NEW_BLOCK[0],
                            "line %d is %r, want the new header"
                            % (top_start, after_insert[top_start - 1])),
                 detail=["inserted at line %d" % top_start,
                         "the region above it is now stale by %d lines, which "
                         "is why the write is not finished yet"
                         % len(NEW_BLOCK)])

    code, out, err = run_cli("toc", "--write", "--file", path)
    final = read_text(path)
    lines = lines_of(final)
    rows = rows_of(mod, path)

    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    problems += problem_if([r[0] for r in rows] != ["S002", "S001", "MISSION"],
                           "rows are %r" % ([r[0] for r in rows],))
    suite.record(GE, "e-second-write-regenerates-three-rows", problems,
                 detail=[out.strip(), "%r" % (rows,)])

    suite.record(GE, "e-ranges-are-well-formed-after-the-prepend",
                 check_ranges(rows, lines))

    _c, fresh_toc, _e = run_cli("toc", "--file", path)
    suite.record(GE, "e-region-is-at-its-fix-point-after-the-prepend",
                 check_fixpoint(region_lines(lines), lines_of(fresh_toc)))

    printed = printed_blocks(mod, path, [r[0] for r in rows])
    suite.record(GE, "e-every-range-is-readable-after-the-prepend",
                 check_slices(rows, lines, printed),
                 detail=["the reason the TOC is regenerated instead of "
                         "patched: every number below the insertion moved"])

    # S001 and MISSION were written by an earlier session and are immutable.
    by_label = {row[0]: row for row in rows}
    fresh_rows = mod.toc_rows(mod.parse_blocks(fresh_lines))
    for label, _when, _branch, start, end in fresh_rows:
        want = fresh_lines[start - 1:end]
        row = by_label.get(label)
        got = lines[row[3] - 1:row[4]] if row else []
        suite.record(GE, "e-%s-is-byte-identical-across-two-writes" % label,
                     problem_if(got != want,
                                "%s changed: %r != %r" % (label, got[:2],
                                                          want[:2])),
                     detail=["was lines %d..%d, now %d..%d"
                             % (start, end, row[3] if row else -1,
                                row[4] if row else -1)])

    code, latest, _err = run_cli("latest", "--file", path)
    suite.record(GE, "e-latest-is-the-new-top-block",
                 problem_if(lines_of(latest) != NEW_BLOCK[:-1],
                            "latest printed %r" % (lines_of(latest)[:2],)),
                 detail=["%d line(s); the trailing blank line of the inserted "
                         "block is not part of it"
                         % len(lines_of(latest))])

    code, nxt, _err = run_cli("next-number", "--file", path)
    suite.record(GE, "e-next-number-is-s003",
                 problem_if(nxt.strip() != "S003", "next-number: %r" % nxt))

    code, s001, _err = run_cli("session", "S001", "--file", path)
    suite.record(GE, "e-older-session-is-still-addressable",
                 problem_if(code != 0 or "Bootstrapped" not in s001,
                            "exit %d: %r" % (code, s001[:80])))

    suite.record(GE, "e-no-temp-file-left-after-the-lifecycle",
                 problem_if(temp_leftovers(path),
                            "left behind: %r" % temp_leftovers(path)))


# ---------------------------------------------------------------------------
# F. migration, and a marker quoted inside a block
# ---------------------------------------------------------------------------

def group_f(suite, mod, workspace):
    old = stage(workspace, "f-old", FIXTURE_MAIN)
    original = read_text(old)
    code, _out, err = run_cli("toc", "--write", "--file", old)
    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    problems += problem_if(not region_lines(file_lines(old)),
                           "no region was installed")
    suite.record(GF, "f-an-old-file-gains-a-region", problems,
                 detail=["the migration path: every checkpoint written before "
                         "toc --write existed has no region"])
    suite.record(GF, "f-migration-touches-nothing-else",
                 check_preserved(original, read_text(old)))
    once = read_text(old)
    run_cli("toc", "--write", "--file", old)
    suite.record(GF, "f-migration-is-idempotent",
                 check_idempotent(once, read_text(old)))

    only = stage(workspace, "f-mission-only", "# Session Checkpoint\n"
                 "\n## MISSION\n\nKeep the handoff cheap.\n")
    code, _out, err = run_cli("toc", "--write", "--file", only)
    rows = rows_of(mod, only) if code == 0 else []
    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    problems += problem_if([r[0] for r in rows] != ["MISSION"],
                           "rows are %r" % (rows,))
    problems += check_ranges(rows, file_lines(only))
    suite.record(GF, "f-a-mission-only-file-is-tabulated", problems,
                 detail=["a checkpoint with no session block is degenerate but "
                         "legal: MISSION alone is a block"])

    # The case that made this group exist: a block DOCUMENTING the feature
    # quotes both markers.  Matching them anywhere in the file would either
    # delete those lines from an immutable block or refuse to write at all.
    quoted = stage(workspace, "f-quoted", FIXTURE_QUOTED_MARKER)
    original = read_text(quoted)
    code, _out, err = run_cli("toc", "--write", "--file", quoted)
    written = read_text(quoted)
    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    suite.record(GF, "f-a-marker-quoted-in-a-block-is-not-the-region", problems,
                 detail=["the region lives strictly between the H1 and the "
                         "first `## ` line, so marker matching must stop at "
                         "the first block",
                         err.strip() or "no diagnostic"])
    suite.record(GF, "f-the-quoted-block-survives-byte-for-byte",
                 check_preserved(original, written) if code == 0
                 else ["not written: exit %d" % code],
                 detail=["a session block is immutable; losing three lines out "
                         "of the middle of one is silent data loss"])
    if code == 0:
        rows = rows_of(mod, quoted)
        suite.record(GF, "f-the-quoted-file-still-tabulates-correctly",
                     check_ranges(rows, file_lines(quoted)),
                     detail=["%r" % (rows,)])
        run_cli("toc", "--write", "--file", quoted)
        suite.record(GF, "f-the-quoted-file-is-idempotent",
                     check_idempotent(written, read_text(quoted)),
                     detail=["a body that looks like a marker must not make "
                             "the second write disagree with the first"])
    else:
        suite.record(GF, "f-the-quoted-file-still-tabulates-correctly",
                     ["not written: exit %d" % code])
        suite.record(GF, "f-the-quoted-file-is-idempotent",
                     ["not written: exit %d" % code])


# ---------------------------------------------------------------------------
# G. the TOC is invisible to the reader commands
# ---------------------------------------------------------------------------

READER_CALLS = [
    ("latest", "cmd_latest", ()),
    ("mission", "cmd_mission", ()),
    ("session-S002", "cmd_session", ("S002",)),
    ("session-S001", "cmd_session", ("S001",)),
    ("next-number", "cmd_next_number", ()),
]


def group_g(suite, mod, workspace):
    plain = stage(workspace, "g-plain", FIXTURE_MAIN)
    with_toc = stage(workspace, "g-with-toc", FIXTURE_MAIN)
    code, _out, err = run_cli("toc", "--write", "--file", with_toc)
    if code != 0:
        suite.record(GG, "g-write-precondition",
                     ["exit %d: %s" % (code, err.strip())])
        return

    for cid, fname, args in READER_CALLS:
        code_a, out_a, _e = call_module(mod, fname, plain, *args)
        code_b, out_b, _e = call_module(mod, fname, with_toc, *args)
        problems = problem_if(code_a != code_b,
                              "exit %r without the region, %r with it"
                              % (code_a, code_b))
        problems += problem_if(out_a != out_b,
                               "the output changed once the region was there")
        suite.record(GG, "g-%s-is-unaffected-by-the-region" % cid, problems,
                     detail=["exit %r; %d line(s)" % (code_a,
                                                      len(lines_of(out_a)))])

    blocks_a = [mod.text_of(b) for b in mod.load_blocks(plain)]
    blocks_b = [mod.text_of(b) for b in mod.load_blocks(with_toc)]
    suite.record(GG, "g-the-same-blocks-parse-out-of-both-files",
                 problem_if(blocks_a != blocks_b,
                            "%d block(s) vs %d, or their text differs"
                            % (len(blocks_a), len(blocks_b))),
                 detail=["the region is preamble: everything above the first "
                         "`## ` line is ignored by the parser"])


# ---------------------------------------------------------------------------
# H. refusals: exit 2, the file unchanged, no temp garbage
# ---------------------------------------------------------------------------

def refusal(suite, workspace, cid, case, body, argv, tokens, why, group=GH):
    """One refusal, gated on all three properties at once.

    A non-zero exit that still rewrote the file, or that left a half-written
    temp file next to it, is not a refusal.

    `group` because `prepend`'s refusals belong with `prepend` -- the checks are
    identical, but a case filed under the wrong group is a case nobody finds
    when that group is the one under suspicion.
    """
    path = stage(workspace, case, body) if body is not None \
        else sandbox_path(os.path.join(workspace.subdir(case), "missing.md"))
    existed = os.path.exists(path)
    before = H.sha256_file(path) if existed else None
    code, out, err = run_cli(*(list(argv) + ["--file", path]))
    problems = problem_if(code != 2, "exit %r, want 2" % code)
    if existed:
        problems += problem_if(H.sha256_file(path) != before,
                               "the file was modified anyway")
    else:
        problems += problem_if(os.path.exists(path),
                               "a missing file was CREATED")
    problems += problem_if(temp_leftovers(path),
                           "temp file left behind: %r" % temp_leftovers(path))
    problems += ["the diagnostic omits %r" % t
                 for t in missing_tokens(err, tokens)]
    suite.record(group, cid, problems,
                 detail=[why, "exit %r; stderr: %s" % (code, err.strip()
                                                       or "<empty>"),
                         "stdout: %s" % (out.strip() or "<empty>")])


NO_BLOCKS = "# Session Checkpoint\n\nNothing here yet.\n"
NO_H1 = "## SESSION S001 | 2026-09-05 09:15 | master\n### LOG\n1. Went.\n"
UNTERMINATED = ("# Session Checkpoint\n\n%s -- generated -->\n"
                "| Session | When | Branch | Start | End |\n"
                "\n## SESSION S001 | 2026-09-05 09:15 | master\n"
                "### LOG\n1. Went.\n" % BEGIN_PREFIX)


def group_h(suite, mod, workspace):
    for cid, argv in (("h-missing-file-toc", ["toc"]),
                      ("h-missing-file-toc-write", ["toc", "--write"]),
                      ("h-missing-file-latest", ["latest"]),
                      ("h-missing-file-mission", ["mission"]),
                      ("h-missing-file-session", ["session", "S001"])):
        refusal(suite, workspace, cid, cid, None, argv,
                ["file not found"],
                "a missing checkpoint is the common case on a fresh machine; "
                "it must say so and create nothing")

    refusal(suite, workspace, "h-no-h1-title", "h-no-h1", NO_H1,
            ["toc", "--write"], ["no H1 title on line 1"],
            "without an H1 there is nowhere to put the region, and guessing "
            "would insert it above a block")
    refusal(suite, workspace, "h-unterminated-region", "h-unterminated",
            UNTERMINATED, ["toc", "--write"], ["unterminated TOC region"],
            "a BEGIN with no END: stripping to a guessed end would delete "
            "whatever follows it")
    refusal(suite, workspace, "h-no-blocks-toc", "h-no-blocks-toc", NO_BLOCKS,
            ["toc"], ["no SESSION or MISSION block"],
            "an empty table is not a table in GFM, and a file with no block "
            "has nothing to tabulate")
    refusal(suite, workspace, "h-no-blocks-toc-write", "h-no-blocks-write",
            NO_BLOCKS, ["toc", "--write"], ["no SESSION or MISSION block"],
            "the same refusal on the writing path, before anything is written")
    refusal(suite, workspace, "h-empty-file", "h-empty", "",
            ["toc", "--write"], ["no H1 title on line 1"],
            "a zero-byte file: `not body` must be caught before body[0]")
    refusal(suite, workspace, "h-session-not-found", "h-not-found",
            FIXTURE_MAIN, ["session", "S999"], ["not found", "S999"],
            "a citation for a block that is not in the file")
    refusal(suite, workspace, "h-session-id-is-not-a-number", "h-bad-id",
            FIXTURE_MAIN, ["session", "abc"], ["not found", "abc"],
            "a malformed id is a local error, not a silent empty answer")

    # The two readers whose empty case used to be a bare `return 2`.  A silent
    # exit reads as "the block was empty" to whoever is watching stdout, so the
    # diagnostic is part of the contract now, not a nicety.
    refusal(suite, workspace, "h-latest-with-no-session-block",
            "h-mission-only", "# Session Checkpoint\n\n## MISSION\n\nWhy.\n",
            ["latest"], ["no SESSION block"],
            "a checkpoint whose session blocks are all gone: exit 2 is right, "
            "but so is saying which block was missing")
    refusal(suite, workspace, "h-mission-with-no-mission-block",
            "h-no-mission", FIXTURE_FRESH.split("## MISSION")[0],
            ["mission"], ["no MISSION block"],
            "the mirror: sessions but no frozen tail, which is what a file "
            "truncated mid-write looks like")

    # A directory in the checkpoint's place: os.path.exists() is satisfied by
    # one, so this used to reach open() and exit 1 with a traceback.  Both
    # commands, because they reach the file by different routes: `toc` through
    # require_file, `next-number` through its own missing-file shortcut.
    for cid, argv in (("h-a-directory-as-the-target", ["toc"]),
                      ("h-a-directory-on-the-next-number-path",
                       ["next-number"])):
        directory = sandbox_path(workspace.subdir("h-directory"))
        entries = sorted(os.listdir(directory))
        code, out, err = run_cli(*(argv + ["--file", directory]))
        problems = problem_if(code != 2, "exit %r, want 2" % code)
        problems += problem_if("Traceback" in err,
                               "it raised instead of refusing")
        problems += ["the diagnostic omits %r" % t
                     for t in missing_tokens(err, ["checkpoint:", directory])]
        problems += problem_if(sorted(os.listdir(directory)) != entries,
                               "the directory's contents changed")
        problems += problem_if(out.strip(),
                               "it answered on stdout anyway: %r" % out)
        suite.record(GH, cid, problems,
                     detail=["exit %r; stderr: %s" % (code, err.strip()
                                                      or "<empty>"),
                             "stdout: %s" % (out.strip() or "<empty>"),
                             "every refusal in a script that WRITES has to "
                             "arrive by the same route: one line on stderr, "
                             "exit 2 -- and `next-number` must not report "
                             "S001 for a path that is not a file"])

    stale = stale_temp_files(workspace.path)
    suite.record(GH, "h-no-temp-file-after-any-refusal",
                 problem_if(stale, "left behind: %r" % stale[:6]),
                 detail=["%d case director(y|ies) swept"
                         % len(os.listdir(workspace.path)),
                         "mkstemp runs only after every refusal has already "
                         "returned, so a leftover here means a write started "
                         "and then failed"])


# ---------------------------------------------------------------------------
# I. prepend: the block and the table it describes land in ONE write
# ---------------------------------------------------------------------------

def stage_segment(workspace, case, body, name="block.md"):
    """The segment file the skill writes before calling `prepend`."""
    return sandbox_path(workspace.write_text(os.path.join(case, name), body))


def target_of(workspace, case):
    """The path `stage()` will give a case -- needed BEFORE staging, for the
    self-target refusal, which has to pass the same path twice."""
    return os.path.join(workspace.path, case, "checkpoint.md")


def group_i(suite, mod, workspace):
    # -- the fresh path: no file, one segment, one call --------------------
    case = "i-fresh"
    workspace.subdir(case)
    target = sandbox_path(target_of(workspace, case))
    segment = stage_segment(workspace, case, SEGMENT_FRESH)
    code, out, err = run_cli("prepend", "--block-file", segment,
                             "--file", target)
    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    problems += problem_if(not os.path.isfile(target), "no file was created")
    if os.path.isfile(target):
        lines = file_lines(target)
        problems += problem_if(lines[:1] != [mod.H1_TITLE],
                               "line 1 is %r, want the default title"
                               % (lines[:1],))
        problems += problem_if(not region_lines(lines),
                               "the new file has no TOC region")
    suite.record(GI, "i-fresh-file-is-created-with-a-region", problems,
                 detail=["a first-ever checkpoint needs no --overwrite: there "
                         "is nothing to lose",
                         "one call, and the file is already addressable"])
    if code != 0 or not os.path.isfile(target):
        return

    lines = file_lines(target)
    rows = rows_of(mod, target)
    problems = problem_if([r[0] for r in rows] != ["S001", "MISSION"],
                          "rows are %r" % ([r[0] for r in rows],))
    problems += check_ranges(rows, lines)
    suite.record(GI, "i-fresh-tabulates-the-whole-segment", problems,
                 detail=["%r" % (rows,),
                         "the segment carried the S001 block AND the frozen "
                         "MISSION tail, which is what a fresh checkpoint is"])

    suite.record(GI, "i-fresh-ranges-are-readable",
                 check_slices(rows, lines,
                              printed_blocks(mod, target,
                                             [r[0] for r in rows])))

    _c, fresh_toc, _e = run_cli("toc", "--file", target)
    suite.record(GI, "i-fresh-stdout-is-the-table-it-wrote",
                 problem_if(out != fresh_toc,
                            "prepend printed %d line(s), a following `toc` "
                            "printed %d" % (len(lines_of(out)),
                                            len(lines_of(fresh_toc)))),
                 detail=["byte-identical, because prepend echoes the region it "
                         "just wrote instead of rendering a second one",
                         "so the caller needs no second command to learn where "
                         "its block landed"])

    code, latest, _e = run_cli("latest", "--file", target)
    code2, mission, _e = run_cli("mission", "--file", target)
    problems = problem_if(code != 0 or "First session" not in latest,
                          "latest: exit %r %r" % (code, latest[:60]))
    problems += problem_if(code2 != 0 or "### WHY" not in mission,
                           "mission: exit %r %r" % (code2, mission[:60]))
    suite.record(GI, "i-fresh-readers-see-both-blocks", problems,
                 detail=["latest -> %d line(s), mission -> %d line(s)"
                         % (len(lines_of(latest)), len(lines_of(mission)))])

    fresh_bytes = read_text(target)
    run_cli("toc", "--write", "--file", target)
    suite.record(GI, "i-fresh-toc-write-is-a-byte-identical-no-op",
                 check_idempotent(fresh_bytes, read_text(target)),
                 detail=["prepend and `toc --write` share ONE generator, so "
                         "the second path must have nothing left to do"])

    # -- the normal prepend: an existing file, already carrying a region ---
    case = "i-prepend"
    target = stage(workspace, case, FIXTURE_MAIN)
    run_cli("toc", "--write", "--file", target)  # the file's steady state
    before = read_text(target)
    before_lines = lines_of(before)
    before_rows = rows_of(mod, target)
    before_region = region_lines(before_lines)
    before_inode = os.stat(target).st_ino
    os.chmod(target, 0o640)
    segment = stage_segment(workspace, case, SEGMENT_ONE)
    seg_lines = [line for line in lines_of(SEGMENT_ONE)]
    while seg_lines and not seg_lines[-1].strip():
        seg_lines.pop()

    code, out, err = run_cli("prepend", "--block-file", segment,
                             "--file", target)
    after = read_text(target)
    lines = lines_of(after)
    rows = rows_of(mod, target)
    by_label = {row[0]: row for row in rows}

    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    problems += problem_if([r[0] for r in rows]
                           != ["S003", "S002", "S001", "MISSION"],
                           "rows are %r" % ([r[0] for r in rows],))
    top = by_label.get("S003")
    problems += problem_if(top and lines[top[3] - 1] != seg_lines[0],
                           "the top row's Start is %r"
                           % (lines[top[3] - 1] if top else None))
    suite.record(GI, "i-prepend-inserts-above-the-current-top-block", problems,
                 detail=["%r" % ([r[0] for r in rows],),
                         "the insertion line was the top block's own Start, "
                         "which the script read itself"])

    # THE invariant: one call, and there is no reachable state in which the
    # block is in the file while the table still describes the old one.
    _c, fresh_toc, _e = run_cli("toc", "--file", target)
    problems = check_fixpoint(region_lines(lines), lines_of(fresh_toc))
    problems += check_ranges(rows, lines)
    problems += problem_if(region_lines(lines) == before_region,
                           "the region did not change at all, so nothing was "
                           "regenerated")
    problems += problem_if(seg_lines[0] not in lines,
                           "the block is not in the file")
    suite.record(GI, "i-one-call-leaves-no-stale-toc", problems,
                 detail=["the four-step version had a window here: the block "
                         "was in, the table still described the file without "
                         "it, and nothing said which was true",
                         "before: %r" % (before_region[3:4],),
                         "after : %r" % (region_lines(lines)[3:4],)])

    suite.record(GI, "i-prepend-top-range-is-the-new-block",
                 check_slices([top], lines,
                              printed_blocks(mod, target, ["S003"]))
                 if top else ["the new block is not in the table"],
                 detail=["lines[%d:%d]" % (top[3], top[4]) if top else "-",
                         "the same check as group B, on the range a caller "
                         "reads straight out of prepend's own stdout"])

    for label, _when, _branch, start, end in before_rows:
        want = before_lines[start - 1:end]
        row = by_label.get(label)
        got = lines[row[3] - 1:row[4]] if row else []
        suite.record(GI, "i-prepend-keeps-%s-byte-identical" % label,
                     problem_if(got != want, "%s changed: %r != %r"
                                % (label, got[:2], want[:2])),
                     detail=["was lines %d..%d, now %d..%d"
                             % (start, end, row[3] if row else -1,
                                row[4] if row else -1)])

    suite.record(GI, "i-prepend-stdout-is-the-table-it-wrote",
                 problem_if(out != fresh_toc, "prepend's stdout and a "
                            "following `toc` differ"),
                 detail=["%d line(s)" % len(lines_of(out))])

    mode = os.stat(target).st_mode & 0o777
    problems = problem_if(os.stat(target).st_ino == before_inode,
                          "the inode did not change, so the file was edited "
                          "in place rather than replaced")
    problems += problem_if(mode != 0o640, "mode is 0o%o, want 0o640" % mode)
    problems += problem_if(temp_leftovers(target),
                           "temp file left behind: %r" % temp_leftovers(target))
    suite.record(GI, "i-the-write-is-a-replacement-not-an-edit", problems,
                 detail=["os.replace on a fresh inode is what makes the two "
                         "halves atomic: an interruption leaves the OLD file, "
                         "never half of the new one"])

    written = read_text(target)
    run_cli("toc", "--write", "--file", target)
    suite.record(GI, "i-prepend-toc-write-is-a-byte-identical-no-op",
                 check_idempotent(written, read_text(target)),
                 detail=["proves prepend produced exactly the region the "
                         "`--write` path would have -- one generator, two "
                         "callers"])

    # -- a segment carrying two blocks (the fresh path, generalised) -------
    case = "i-multi"
    target = stage(workspace, case, FIXTURE_MAIN)
    run_cli("toc", "--write", "--file", target)
    segment = stage_segment(workspace, case, SEGMENT_TWO_BLOCKS)
    code, out, err = run_cli("prepend", "--block-file", segment,
                             "--file", target)
    lines = file_lines(target)
    rows = rows_of(mod, target)
    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    problems += problem_if([r[0] for r in rows]
                           != ["S004", "S003", "S002", "S001", "MISSION"],
                           "rows are %r" % ([r[0] for r in rows],))
    problems += check_ranges(rows, lines)
    problems += check_slices(rows, lines,
                             printed_blocks(mod, target,
                                            [r[0] for r in rows]))
    suite.record(GI, "i-a-two-block-segment-tabulates-both", problems,
                 detail=["%r" % ([r[0] for r in rows],),
                         "the segment is one or more blocks, not exactly one: "
                         "that is what lets the fresh path ship S001 and the "
                         "MISSION tail together"])

    # -- --overwrite, on a file with something to lose --------------------
    case = "i-overwrite"
    target = stage(workspace, case, FIXTURE_MAIN.replace(
        "# Session Checkpoint", "# Handoff -- prompt-heaven", 1))
    run_cli("toc", "--write", "--file", target)
    segment = stage_segment(workspace, case, SEGMENT_FRESH)
    code, out, err = run_cli("prepend", "--block-file", segment,
                             "--file", target, "--overwrite")
    lines = file_lines(target)
    rows = rows_of(mod, target)
    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    problems += problem_if([r[0] for r in rows] != ["S001", "MISSION"],
                           "rows are %r, want only the segment's blocks"
                           % ([r[0] for r in rows],))
    problems += problem_if("1. Write the functional suite." in lines,
                           "a line of the discarded content is still there")
    problems += check_ranges(rows, lines)
    suite.record(GI, "i-overwrite-replaces-the-whole-file", problems,
                 detail=["%r" % ([r[0] for r in rows],),
                         "the flag exists for exactly this, and ONLY for a "
                         "file that already exists"])
    suite.record(GI, "i-overwrite-keeps-a-hand-picked-h1",
                 problem_if(lines[:1] != ["# Handoff -- prompt-heaven"],
                            "line 1 is %r" % (lines[:1],)),
                 detail=["the title is the one thing an overwrite inherits: it "
                         "names the document, not its contents"])

    # -- a file with a preamble but no blocks yet -------------------------
    case = "i-no-blocks"
    target = stage(workspace, case, NO_BLOCKS)
    segment = stage_segment(workspace, case, SEGMENT_ONE)
    code, out, err = run_cli("prepend", "--block-file", segment,
                             "--file", target)
    lines = file_lines(target)
    rows = rows_of(mod, target)
    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    problems += problem_if([r[0] for r in rows] != ["S003"],
                           "rows are %r" % ([r[0] for r in rows],))
    problems += problem_if("Nothing here yet." not in lines,
                           "the existing preamble was dropped")
    problems += check_ranges(rows, lines)
    suite.record(GI, "i-prepend-into-a-file-with-no-blocks", problems,
                 detail=["`toc --write` refuses this file (nothing to "
                         "tabulate); prepend does not, because the segment "
                         "brings the first block with it"])

    # -- the shape that used to eat itself --------------------------------
    case = "i-quoted"
    target = stage(workspace, case, FIXTURE_MAIN)
    run_cli("toc", "--write", "--file", target)
    segment = stage_segment(workspace, case, SEGMENT_QUOTED_MARKER)
    code, out, err = run_cli("prepend", "--block-file", segment,
                             "--file", target)
    lines = file_lines(target)
    rows = rows_of(mod, target)
    quoted = [line for line in lines if line.startswith(BEGIN_PREFIX)]
    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    problems += problem_if(len(quoted) != 2,
                           "%d BEGIN-looking line(s), want 2 (the real one and "
                           "the quoted one)" % len(quoted))
    problems += check_ranges(rows, lines)
    problems += check_slices(rows, lines,
                             printed_blocks(mod, target,
                                            [r[0] for r in rows]))
    suite.record(GI, "i-a-segment-quoting-the-markers-survives", problems,
                 detail=["the first block ever written about this feature "
                         "quotes both markers; matching them below the first "
                         "`## ` line would strip them back out of it"])
    written = read_text(target)
    run_cli("toc", "--write", "--file", target)
    suite.record(GI, "i-the-quoted-segment-stays-idempotent",
                 check_idempotent(written, read_text(target)))

    # -- the refusals, every one before a single byte is written ----------
    case = "i-no-block-file"
    missing = sandbox_path(os.path.join(workspace.subdir(case), "absent.md"))
    refusal(suite, workspace, "i-refuses-a-missing-block-file", case,
            FIXTURE_MAIN, ["prepend", "--block-file", missing],
            ["block file not found"],
            "the skill writes the segment first; a missing one means that "
            "write failed, and inserting nothing would still shift the TOC",
            group=GI)

    case = "i-dir-block-file"
    blockdir = sandbox_path(workspace.subdir(os.path.join(case, "blockdir")))
    refusal(suite, workspace, "i-refuses-a-directory-as-the-block-file", case,
            FIXTURE_MAIN, ["prepend", "--block-file", blockdir],
            ["not a regular file"],
            "same refusal route as everywhere else in this script",
            group=GI)

    case = "i-empty-block"
    empty = stage_segment(workspace, case, "")
    refusal(suite, workspace, "i-refuses-an-empty-block-file", case,
            FIXTURE_MAIN, ["prepend", "--block-file", empty],
            ["block file is empty"],
            "an empty segment would regenerate the TOC and nothing else, "
            "which reads as a successful checkpoint that saved nothing",
            group=GI)

    case = "i-blank-block"
    blank = stage_segment(workspace, case, SEGMENT_BLANK)
    refusal(suite, workspace, "i-refuses-a-whitespace-only-block-file", case,
            FIXTURE_MAIN, ["prepend", "--block-file", blank],
            ["block file is empty"],
            "whitespace is empty for this purpose: there is no block in it",
            group=GI)

    case = "i-no-header"
    headerless = stage_segment(workspace, case, SEGMENT_NO_HEADER)
    refusal(suite, workspace, "i-refuses-a-segment-with-no-block-header", case,
            FIXTURE_MAIN, ["prepend", "--block-file", headerless],
            ["must start with a `## ` header"],
            "a segment that does not open with `## ` is invisible to every "
            "reader: it would be swallowed by the block above it",
            group=GI)

    case = "i-segment-h1"
    titled = stage_segment(workspace, case, SEGMENT_WITH_H1)
    refusal(suite, workspace, "i-refuses-a-segment-carrying-an-h1", case,
            FIXTURE_MAIN, ["prepend", "--block-file", titled],
            ["carries an H1 title"],
            "a second `# ` line gives the file two titles, and the generated "
            "region attaches to the first one",
            group=GI)

    # The most harmful of the seven, pinned on its own: the segment path and
    # the checkpoint path resolving to the same file.
    case = "i-self-target"
    itself = target_of(workspace, case)
    refusal(suite, workspace, "i-refuses-the-checkpoint-as-its-own-segment",
            case, FIXTURE_MAIN, ["prepend", "--block-file", itself],
            ["refusing to insert a file into itself"],
            "--block-file pointing at the checkpoint would read the whole "
            "document, fail the H1 rule at best, and duplicate every block "
            "into itself at worst",
            group=GI)

    # -- the duplicate-id gate: content-based, so nobody has to remember it --
    case = "i-duplicate"
    target = stage(workspace, case, FIXTURE_MAIN)
    segment = stage_segment(workspace, case, SEGMENT_ONE)
    code1, _o1, err1 = run_cli("prepend", "--block-file", segment,
                               "--file", target)
    once = read_text(target)
    code2, out2, err2 = run_cli("prepend", "--block-file", segment,
                                "--file", target)
    problems = problem_if(code1 != 0,
                          "the FIRST prepend failed: %s" % err1.strip())
    problems += problem_if(code2 != 2,
                           "the second exited %r, want 2" % code2)
    problems += check_idempotent(once, read_text(target))
    problems += problem_if(temp_leftovers(target),
                           "temp file left behind: %r" % temp_leftovers(target))
    problems += problem_if(out2.strip(),
                           "it printed a table anyway: %r" % out2[:60])
    problems += ["the diagnostic omits %r" % t
                 for t in missing_tokens(err2, ["S003", "duplicate"])]
    suite.record(GI, "i-refuses-the-same-segment-twice", problems,
                 detail=["exit %r then %r; stderr: %s"
                         % (code1, code2, err2.strip() or "<empty>"),
                         "the block file has a DEFAULT path, so it survives "
                         "between checkpoints: a segment nobody rewrote would "
                         "be inserted again under an id that already exists",
                         "a retried prepend now fails instead of duplicating"])

    case = "i-clash-mid"
    clashing = stage_segment(workspace, case, SEGMENT_CLASH_MID)
    refusal(suite, workspace, "i-refuses-an-id-that-clashes-mid-file", case,
            FIXTURE_MAIN, ["prepend", "--block-file", clashing],
            ["S001", "duplicate"],
            "S001 is the BOTTOM block of this fixture: the gate reads the "
            "whole file, not just the block it would land above",
            group=GI)

    case = "i-clash-mission"
    second_tail = stage_segment(workspace, case, SEGMENT_CLASH_MISSION)
    refusal(suite, workspace, "i-refuses-a-second-mission-tail", case,
            FIXTURE_MAIN, ["prepend", "--block-file", second_tail],
            ["MISSION", "duplicate"],
            "the mission tail is written once and never rewritten, so a "
            "segment carrying a second one is the same collision",
            group=GI)

    # The mirror: --overwrite must suspend the gate, and be SEEN to, or the
    # refusals above could be passing for an unrelated reason.
    case = "i-clash-overwrite"
    target = stage(workspace, case, FIXTURE_MAIN)
    clashing = stage_segment(workspace, case, SEGMENT_CLASH_MID)
    code, out, err = run_cli("prepend", "--block-file", clashing,
                             "--file", target, "--overwrite")
    rows = rows_of(mod, target) if code == 0 else []
    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    problems += problem_if([r[0] for r in rows] != ["S001"],
                           "rows are %r" % ([r[0] for r in rows],))
    suite.record(GI, "i-overwrite-suspends-the-duplicate-gate", problems,
                 detail=["the SAME segment the case above refuses",
                         "there is nothing to collide with once the old "
                         "content is discarded"])

    # -- the default --block-file: the skill's second call takes no args ----
    case = "i-default-block"
    target = stage(workspace, case, FIXTURE_MAIN)
    default_block = sandbox_path(workspace.write_text(
        os.path.join(case, *DEFAULT_BLOCK_PATH.split("/")), SEGMENT_ONE))
    code, out, err = run_cli("prepend", "--file", target)
    lines = file_lines(target)
    rows = rows_of(mod, target)
    problems = problem_if(code != 0, "exit %d: %s" % (code, err.strip()))
    problems += problem_if([r[0] for r in rows]
                           != ["S003", "S002", "S001", "MISSION"],
                           "rows are %r" % ([r[0] for r in rows],))
    problems += check_ranges(rows, lines)
    suite.record(GI, "i-reads-the-default-block-file-path", problems,
                 detail=["`prepend --file <path>` with no --block-file read "
                         "%s" % os.path.relpath(default_block,
                                                os.path.dirname(target)),
                         "one path the skill cannot mistype; the child's cwd "
                         "is the sandbox, so the relative default resolves "
                         "inside it and never at the live one"])

    case = "i-dir-target"
    dirtarget = sandbox_path(workspace.subdir(case))
    segment = stage_segment(workspace, case, SEGMENT_ONE)
    entries = sorted(os.listdir(dirtarget))
    code, out, err = run_cli("prepend", "--block-file", segment,
                             "--file", dirtarget)
    problems = problem_if(code != 2, "exit %r, want 2" % code)
    problems += problem_if("Traceback" in err, "it raised instead of refusing")
    problems += ["the diagnostic omits %r" % t
                 for t in missing_tokens(err, ["checkpoint:", dirtarget])]
    problems += problem_if(sorted(os.listdir(dirtarget)) != entries,
                           "the directory's contents changed")
    suite.record(GI, "i-refuses-a-directory-as-the-target", problems,
                 detail=["exit %r; stderr: %s" % (code, err.strip()
                                                  or "<empty>"),
                         "the writing path has to refuse a non-file the same "
                         "way every reader does"])


# ---------------------------------------------------------------------------
# J. NEGATIVE CONTROL -- every oracle above must be able to reject
# ---------------------------------------------------------------------------

def mutate_rows(rows, field, delta):
    return [tuple(v + delta if i == field else v for i, v in enumerate(row))
            for row in rows]


def group_j(suite, mod, workspace):
    path = stage(workspace, "j", FIXTURE_MAIN)
    lines = file_lines(path)
    rows = rows_of(mod, path)
    printed = printed_blocks(mod, path, [r[0] for r in rows])

    mutants = []

    mutants.append((
        "end-includes-the-trailing-blanks",
        lambda: check_ranges(mutate_rows(rows, 4, +1), lines),
        "End one line long: `limit` drags a blank line in, and the next "
        "prepend makes it drag the next header in"))
    mutants.append((
        "end-is-one-line-short",
        lambda: check_ranges(mutate_rows(rows, 4, -1), lines),
        "End one line short: the block's last line is silently missing from "
        "every Read that trusts the table"))
    mutants.append((
        "start-is-shifted-by-one",
        lambda: check_ranges(mutate_rows(rows, 3, +1), lines),
        "Start off by one: the header is gone, so a cold reader cannot even "
        "tell which block it got"))
    mutants.append((
        "slice-oracle-fed-a-shifted-range",
        lambda: check_slices(mutate_rows(rows, 3, +1), lines, printed),
        "the range and the command disagree, which is the one thing the "
        "feature must never do"))
    mutants.append((
        "region-rendered-with-offset-zero",
        lambda: control_offset_zero(mod, workspace),
        "the pre-insertion numbers, written into the file that the insertion "
        "shifted -- every row off by the region's own height"))
    mutants.append((
        "strip-leaks-a-blank-line-per-write",
        lambda: control_non_idempotent(mod, workspace),
        "a strip that forgets the blank separator: the file grows a line on "
        "every write, forever"))
    mutants.append((
        "preserve-oracle-fed-a-deleted-line",
        lambda: control_deleted_line(workspace),
        "a write that drops one line out of an immutable session block"))

    caught = 0
    for cid, oracle, why in mutants:
        problems = oracle()
        if problems:
            caught += 1
        suite.record(GJ, "control-%s" % cid,
                     problem_if(not problems,
                                "the oracle ACCEPTED a broken table, so it is "
                                "protecting nothing"),
                     detail=["mutant: %s" % why,
                             "rejected with: %s" % ("; ".join(problems[:2])
                                                    or "NOTHING")])

    suite.record(GJ, "control-fires-at-all",
                 problem_if(caught != len(mutants),
                            "only %d of %d mutants were caught"
                            % (caught, len(mutants))),
                 detail=["%d/%d rejected" % (caught, len(mutants)),
                         "a control that stopped running is indistinguishable "
                         "from code that is correct"])

    # The mirror: the SAME oracles must ACCEPT the real thing, or "rejects
    # everything" would satisfy every row above and prove nothing.
    real = stage(workspace, "j-real", FIXTURE_MAIN)
    original = read_text(real)
    run_cli("toc", "--write", "--file", real)
    once = read_text(real)
    run_cli("toc", "--write", "--file", real)
    twice = read_text(real)
    real_lines = lines_of(twice)
    real_rows = rows_of(mod, real)
    _c, fresh, _e = run_cli("toc", "--file", real)
    problems = []
    problems += ["ranges: %s" % p for p in check_ranges(real_rows, real_lines)]
    problems += ["slices: %s" % p for p in check_slices(
        real_rows, real_lines,
        printed_blocks(mod, real, [r[0] for r in real_rows]))]
    problems += ["fixpoint: %s" % p for p in check_fixpoint(
        region_lines(real_lines), lines_of(fresh))]
    problems += ["idempotent: %s" % p for p in check_idempotent(once, twice)]
    problems += ["preserved: %s" % p for p in check_preserved(original, twice)]
    suite.record(GJ, "control-the-real-implementation-passes-them-all",
                 problems,
                 detail=["five oracles, one file, no mutation"])


def control_offset_zero(mod, workspace):
    """Write a region whose numbers describe the file BEFORE the insertion --
    the defect the two-render trick exists to avoid -- and let the fix-point
    oracle judge it."""
    path = stage(workspace, "j-offset-zero", FIXTURE_MAIN)
    body = lines_of(read_text(path))
    rows = mod.toc_rows(mod.parse_blocks(body))
    region = mod.render_toc(rows, offset=0)
    write_text(path, "\n".join(body[:1] + [""] + region + body[1:]) + "\n")
    _c, fresh, _e = run_cli("toc", "--file", path)
    return check_fixpoint(region_lines(file_lines(path)), lines_of(fresh))


def control_non_idempotent(mod, workspace):
    """A writer whose strip leaves the blank separator behind, run twice."""
    def bad_write(path):
        lines = lines_of(read_text(path))
        begin, end = region_bounds(lines)
        body = lines if begin < 0 else lines[:begin] + lines[end + 1:]
        rows = mod.toc_rows(mod.parse_blocks(body))
        height = 1 + len(mod.render_toc(rows))
        region = mod.render_toc(rows, offset=height)
        write_text(path, "\n".join(body[:1] + [""] + region + body[1:]) + "\n")

    path = stage(workspace, "j-non-idempotent", FIXTURE_MAIN)
    bad_write(path)
    first = read_text(path)
    bad_write(path)
    return check_idempotent(first, read_text(path))


def control_deleted_line(workspace):
    """A write that loses one line of an immutable block, judged by the
    byte-preservation oracle."""
    path = stage(workspace, "j-deleted-line", FIXTURE_MAIN)
    original = read_text(path)
    lines = lines_of(original)
    mangled = lines[:1] + [""] + ["%s -- x -->" % BEGIN_PREFIX,
                                  "| Session |", "|---------|", "| S002    |",
                                  "%s -->" % END_PREFIX] + lines[1:20] \
        + lines[21:]
    write_text(path, "\n".join(mangled) + "\n")
    return check_preserved(original, read_text(path))


# ---------------------------------------------------------------------------
# K. hygiene -- runs LAST, after every group that writes
# ---------------------------------------------------------------------------

def repo_tree():
    out = set()
    for dirpath, dirnames, filenames in os.walk(H.REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        rel = os.path.relpath(dirpath, H.REPO_ROOT)
        prefix = "" if rel == "." else rel + "/"
        for name in dirnames:
            out.add(prefix + name + "/")
        for name in filenames:
            out.add(prefix + name)
    return out


def group_k(suite, before, pyc_before, live_before, workspace):
    # The one that matters: the script's DEFAULT target is this file, and it
    # holds the user's session handoff.  Nothing in this suite may reach it.
    if live_before is None:
        suite.record(GK, "k-live-checkpoint-untouched", [], status=H.INFO,
                     detail=["%s does not exist on this machine, so there was "
                             "nothing to protect -- the guards still ran"
                             % LIVE_CHECKPOINT])
    else:
        exists = os.path.isfile(LIVE_CHECKPOINT)
        problems = problem_if(not exists, "the live checkpoint is GONE")
        if exists:
            problems += problem_if(
                H.sha256_file(LIVE_CHECKPOINT) != live_before,
                "the live checkpoint was MODIFIED by this run")
        suite.record(GK, "k-live-checkpoint-untouched", problems,
                     detail=["sha256 %s..." % live_before[:16],
                             "the default target of every command under test"])

    after = repo_tree()
    new = sorted(after - before)
    gone = sorted(before - after)
    suite.record(GK, "k-no-new-repo-paths",
                 problem_if(new, "this suite wrote into the repo tree: %s"
                            % new[:12]),
                 detail=["%d path(s) before, %d after" % (len(before),
                                                          len(after))])
    suite.record(GK, "k-no-removed-repo-paths",
                 problem_if(gone, "paths disappeared: %s" % gone[:12]))

    pyc_after = H.pycache_snapshot()
    problems = []
    if pyc_after:
        created = sorted(set(pyc_after) - set(pyc_before))
        pre = sorted(set(pyc_after) & set(pyc_before))
        if created:
            problems.append("this run wrote bytecode: %s" % created[:6])
        if pre:
            problems.append("pre-existing .pyc a delta check would miss: %s"
                            % pre[:6])
    suite.record(GK, "k-pycache-zero", problems,
                 detail=["%d .pyc before, %d after (contract: zero)"
                         % (len(pyc_before), len(pyc_after))])

    inside = os.path.realpath(workspace).startswith(
        os.path.realpath(H.REPO_ROOT) + os.sep)
    suite.record(GK, "k-workspace-outside-the-repo-tree",
                 problem_if(inside, "the workspace is inside the repo: %s"
                            % workspace),
                 detail=["workspace: %s" % workspace])

    stale = stale_temp_files(workspace)
    suite.record(GK, "k-no-temp-file-left-anywhere-in-the-sandbox",
                 problem_if(stale, "left behind: %r" % stale[:6]),
                 detail=["every write in this suite went through mkstemp + "
                         "os.replace; a survivor means one of them was "
                         "abandoned half-written"])

    # The guards from the module docstring, exercised: a suite whose safety
    # rests on them should prove they bite.
    problems = []
    try:
        sandbox_path(LIVE_CHECKPOINT)
        problems.append("sandbox_path ACCEPTED the live checkpoint")
    except AssertionError:
        pass
    try:
        run_cli("toc")
        problems.append("run_cli ACCEPTED an argv with no --file")
    except AssertionError:
        pass
    suite.record(GK, "k-the-sandbox-guards-refuse-the-default-target",
                 problems,
                 detail=["both guards raise; that is what makes reaching "
                         "%s impossible rather than unlikely"
                         % LIVE_CHECKPOINT])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="checkpoint.py: block ranges, the toc --write fix "
                          "point, idempotence, byte preservation, and every "
                          "refusal on one route",
                    opts=opts, mode="grouped")

    before = repo_tree()
    pyc_before = H.pycache_snapshot()
    live_before = (H.sha256_file(LIVE_CHECKPOINT)
                   if os.path.isfile(LIVE_CHECKPOINT) else None)

    if not os.path.isfile(TARGET):
        suite.record(GA, "target-exists",
                     ["the script under test is missing: %s" % TARGET])
        suite.print_summary()
        return suite

    mod = H.load_module_from_path("checkpoint_under_test", TARGET)
    problems = problem_if(mod.DEFAULT_FILE != ".claude/tmp/checkpoint.md",
                          "DEFAULT_FILE is %r" % mod.DEFAULT_FILE)
    problems += problem_if(not mod.TOC_BEGIN.startswith(BEGIN_PREFIX)
                           or not mod.TOC_END.startswith(END_PREFIX),
                           "the marker comments moved: %r / %r"
                           % (mod.TOC_BEGIN, mod.TOC_END))
    problems += problem_if(tuple(mod.TOC_COLUMNS) != COLUMNS,
                           "the columns moved: %r" % (mod.TOC_COLUMNS,))
    problems += problem_if(
        mod.DEFAULT_BLOCK_FILE != DEFAULT_BLOCK_PATH,
        "DEFAULT_BLOCK_FILE is %r, want %r"
        % (mod.DEFAULT_BLOCK_FILE, DEFAULT_BLOCK_PATH))
    problems += problem_if(
        os.path.basename(mod.DEFAULT_BLOCK_FILE).startswith("checkpoint"),
        "the segment file shares a name stem with the checkpoint itself (%r) "
        "and with write_atomic's `%s*.tmp` pattern"
        % (mod.DEFAULT_BLOCK_FILE, TMP_PREFIX))
    problems += problem_if(mod.H1_TITLE != "# Session Checkpoint",
                           "H1_TITLE is %r" % mod.H1_TITLE)
    suite.record(GA, "a-the-format-contract-is-where-this-suite-expects-it",
                 problems,
                 detail=["default target: %r" % mod.DEFAULT_FILE,
                         "default segment: %r" % mod.DEFAULT_BLOCK_FILE,
                         "every path below is a sandbox path INSTEAD of those "
                         "two, and the guards enforce it"])

    with H.TempWorkspace("ph-checkpoint-", keep=opts.keep) as workspace:
        set_sandbox(workspace.path)
        try:
            group_a(suite, mod, workspace)
            group_b(suite, mod, workspace)
            group_c(suite, mod, workspace)
            group_d(suite, mod, workspace)
            group_e(suite, mod, workspace)
            group_f(suite, mod, workspace)
            group_g(suite, mod, workspace)
            group_h(suite, mod, workspace)
            group_i(suite, mod, workspace)
            group_j(suite, mod, workspace)
        finally:
            # group_k LAST, always: it asserts the repo tree and the user's
            # live checkpoint are exactly as this run found them, so every
            # group that could write has to have finished first.
            group_k(suite, before, pyc_before, live_before, workspace.path)

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
