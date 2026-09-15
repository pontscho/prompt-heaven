#!/usr/bin/env python3
"""A rendered table cell must not be able to forge a column boundary (A-G).

THE DEFECT
----------
Every one of these renderers turns rows of data into text a MODEL reads back.
If a cell can contain the character the renderer uses to separate columns, the
row silently grows a column: still valid output, still parses, just not the
table that was meant.  The model then reads one field's value under another
field's name and has no way to know.

MEASURED over `Scripts/` AND `ClaudeCode/skills/*/scripts/`, six renderers exist
and they split two ways:

  * `Scripts/mcp-postgres.py`  escapes, reversibly, and SAYS SO on the wire.
  * `ClaudeCode/skills/jira/scripts/jira.py`  escapes the pipe, deliberately
    not reversibly, and says so to nobody.
  * `Scripts/mcp-tshark.py`  did not escape at all -- and it is the one whose
    cells carry packet bytes, which are by definition untrusted input.
  * `ClaudeCode/skills/checkpoint/scripts/checkpoint.py`  renders the TOC that
    tells a COLD MODEL which line range to read, and was safe only because its
    own parser ate the delimiter first.
  * `Scripts/mcp-jenkins.py` and `Scripts/mcp-inspect.py` do not escape either
    and do not need to: their delimiter is whitespace, not `|`.

That last pair is why this suite has two halves rather than one rule.

The first draft of this docstring said FIVE, and the sweep behind it read only
`Scripts/`.  That was a `Scripts/`-scoped measurement stated as a tree-wide one,
and it hid the sixth renderer -- in the file whose output a cold model parses by
offset.  The roster already reached outside `Scripts/` by hand for the Jira CLI,
so the sweep's narrower root was an inconsistency with the table it backstops,
not a considered scope.  Both roots are swept now.

THE INVARIANT, AS ONE SENTENCE
------------------------------
A renderer satisfies it by exactly ONE of two routes, and its declared row says
which:

  * **ESCAPING**  -- the renderer neutralises its own delimiter inside a cell,
    and the server DOCUMENTS the scheme where the model actually reads it.  An
    escaping scheme the reader does not know about is a different bug, not a
    fix: the model sees `\\|` and cannot tell an escaped literal from a value
    that really contained a backslash.
  * **STRUCTURE** -- the delimiter cannot be a column boundary in that
    renderer's output at all, so there is nothing to escape.  This is a real
    answer and not an excuse, but the row has to say WHY it holds, and group D
    MEASURES the reason rather than accepting the assertion.

THREE CLAUSES, AND THEY CATCH THREE DIFFERENT THINGS
-----------------------------------------------------
The column-count clause and the reversibility clause are not two spellings of
one check, and jira is the worked example of the gap between them.

  1. **The escaper oracle** (group A) is lifted from `tests/test_jira_cli.py`
     `check_escaper`, which is where this fleet first wrote the problem down:
     the pipe must come back as `\\|`, no SEPARATOR pipe may survive in a cell,
     a newline must not survive because it ends the row outright, and a value
     with nothing to escape must come back untouched.
  2. **The column count** (group B) renders the same table TWICE through the
     real function -- once with a harmless cell, once with a cell carrying a
     pipe, a backslash and a newline -- and requires the separator count of
     every line to be identical.  Comparing two renders rather than inspecting
     one is what makes it robust against a renderer's own furniture, a GFM rule
     line or a paging note, with no per-row declaration of which lines are the
     table; and it catches the newline for free, because a cell that ends the
     row early changes the LINE COUNT and two lists of different length are not
     equal.  `unescaped_pipes` is taken from `tests/test_jira_cli.py:320` --
     `count("|") - count("\\|")`, which is the whole difference between "this
     cell contains a pipe" and "this row has an extra column".
  3. **Reversibility** (group A, second clause) requires `decode(encode(x))`
     to return `x` for a corpus that includes a literal backslash.  THIS is the
     clause the other two cannot reach: `md_escape("a\\|b")` emits `a\\\\|b`,
     whose `count("|") - count("\\|")` is zero, so clause 2 passes it -- while a
     decoder correctly reads `\\\\` as one literal backslash and the surviving
     `|` as a COLUMN BOUNDARY.  An encoder that does not escape its own escape
     character is not reversible, and only a round trip says so.

WHY `Scripts/mcp-tshark.py` IS DECLARED REVERSIBLE AND JIRA IS NOT
-------------------------------------------------------------------
The obvious fix for a GFM table is jira's one-liner, and copying it would
inherit jira's hole.  The rows differ on purpose, on the data rather than on
taste: a Jira summary is human prose, while tshark's cells carry `_ws.col.Info`,
`dns.qry.name` and the TLS SNI -- bytes off the wire, from a capture that is
untrusted by construction, and emitted by tshark under `-E quote=n` with nothing
upstream neutralising anything.  A backslash before a pipe is a stretch in a
ticket title and routine in a packet.

jira's deviation is DECLARED rather than failed, with both of its facts named --
it collapses a newline to a space instead of encoding it, and it does not escape
the backslash -- and the case asserts the deviation is EXACTLY what the row
says.  A jira that quietly became reversible would trip the row just as a jira
that got worse would.  A declared deviation nobody re-measures is a comment.

DECLARED DATA, NOT INFERRED
---------------------------
`RENDERERS` below is hand-written, one row per renderer, each with a prose
reason.  Inferring the classes from the tree would produce a suite that agrees
with whatever the tree currently does -- it would have passed, green, on the
unescaped renderer, because "every renderer escapes what it escapes" is
trivially true.  This is `tests/test_read_loop.py`'s, `tests/test_wire_log.py`'s
and `tests/test_handler_crash.py`'s pattern and it is here for their reason.

DECLARED BLIND SPOTS -- on the page, because an unstated scope is the same
defect as a false invariant:

  1. **This suite cannot prove a delimiter is unreachable in live upstream
     DATA.**  It never asks whether a pipe turns up in a real capture or a real
     Jira summary.  It proves the renderer is safe WHATEVER the data is, which
     is both the stronger claim and the cheaper one -- a reachability argument
     has to be re-made every time an upstream changes, and this one does not.
  2. **The fence is applied at the CALL SITE, and call sites are not
     surveyed.**  Group D measures the load-bearing half of a STRUCTURE claim --
     that the renderer emitted no pipe it was not handed, which is the one thing
     "the pipe is not my delimiter" actually says -- and checks only that the
     module owns a fence helper.  A renderer whose output escaped a fence at one
     call site would not be seen.  Note that `unescaped_pipes` is deliberately
     NOT the instrument there: it counts every pipe a backslash does not escape,
     which is correct in a `|`-delimited world and wrong the moment the
     delimiter is whitespace, where a literal pipe resting in a cell would read
     as a separator and fail a row for being exactly what it declares.
  3. **Only the renderers in the table are read.**  Group E's sweep is the
     backstop: it re-runs the `.ljust(`/`.rjust(` census that found them, across
     BOTH roots the roster spans, and fails if an undeclared file appears -- so a
     new renderer cannot arrive unnoticed.  A renderer that aligns by some other
     means would still escape that sweep, which is how the sixth was missed:
     nothing was wrong with the tokens, the ROOT was too narrow.
  4. **`_cell`'s NULL discipline is not judged here.**  `Scripts/mcp-postgres.py`
     spells the literal string "NULL" as `\\NULL`, which the decoder in this file
     deliberately reports as MALFORMED -- because this file decodes
     `_escape_cell`, and the NULL marker belongs to the layer above it.

The case count IS typed in run.py's SUITES table: a renderer appearing without a
declared row IS the defect, so a count that moves when the roster moves is the
alarm working, not noise.

Imports the real functions and calls them.  Starts no server, spawns nothing,
writes nothing at all -- there is no sandbox because there is no fixture on
disk; the control group's escapers are functions in this file.  ~1s.

Usage:
  python3 tests/test_table_cells.py
  python3 tests/test_table_cells.py --brief
Exit code 0 iff every non-informational case passes.

Groups:
  A  GATE     -- the escaper oracle, plus reversibility, per ESCAPED renderer
  B  GATE     -- a real table rendered through the real function keeps its
                 column count
  C  GATE     -- the scheme is documented where the model reads it
  D  DECLARED -- the STRUCTURE renderers' reason is measured, not accepted
  E  ROSTER   -- the table covers the tree, totals hold, no row may declare
                 STRUCTURE for a pipe-delimited renderer
  F  control  -- escapers with one defect each that the oracle MUST reject, and
                 correct ones it must not; plus the PAIRING control, where a
                 parser and a renderer are combined four ways and only one
                 combination is safe
  G  hygiene  -- writes nothing, no bytecode, no new repo paths, and every
                 module came from the live tree
"""

import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "table_cells"

GA = "A. GATE: the escaper neutralises its own delimiter"
GB = "B. GATE: a rendered row keeps its column count"
GC = "C. GATE: the scheme is documented on the wire"
GD = "D. DECLARED: the structure renderers' reason holds"
GE = "E. ROSTER: the table covers the tree"
GF = "F. negative control"
GG = "G. hygiene"

WRITES = []

# ---------------------------------------------------------------------------
# THE DECLARED TABLE
# ---------------------------------------------------------------------------
# key -> Row.  `cls` is the route by which this renderer satisfies the
# invariant, and the two routes are judged by different groups on purpose.
ESCAPED = "ESCAPED"
STRUCTURE = "STRUCTURE"

# Declared totals, so a silent re-classification of one renderer trips a case
# rather than sliding through as "the table matches the table".
DECLARED_ESCAPED = 4
DECLARED_STRUCTURE = 2


class Row:
    def __init__(self, path, cls, renderer, escaper=None, delim="|",
                 reversible=False, deviation=(), desc_const=None,
                 desc_tokens=(), fence=None, why=""):
        self.path = path              # repo-relative source file
        self.cls = cls                # ESCAPED | STRUCTURE
        self.renderer = renderer      # attribute name of the table function
        self.escaper = escaper        # attribute name of the cell escaper
        self.delim = delim            # the character that separates columns
        self.reversible = reversible  # decode(encode(x)) == x is REQUIRED
        self.deviation = tuple(deviation)   # corpus keys that must NOT round-trip
        self.desc_const = desc_const  # module attr holding the tools/list dict
        self.desc_tokens = tuple(desc_tokens)
        self.fence = fence            # module attr wrapping output in a fence
        self.why = why


RENDERERS = {
    "mcp-postgres": Row(
        path="Scripts/mcp-postgres.py", cls=ESCAPED,
        renderer="_render_result", escaper="_escape_cell", delim="|",
        reversible=True,
        desc_const="POSTGRES_CALL_TOOL", desc_tokens=("escaped", "\\|"),
        why="the canonical form. A custom `|`-delimited row format, unaligned "
            "because the reader is a model that parses on the delimiter; the "
            "escaping is C-style and reversible BECAUSE the escape character "
            "is itself escaped, and the tools/list description teaches the "
            "decode ORDER, not just the character set"),

    "mcp-tshark": Row(
        path="Scripts/mcp-tshark.py", cls=ESCAPED,
        renderer="_markdown_table", escaper="_md_cell", delim="|",
        reversible=True,
        desc_const="TSHARK_CALL_TOOL", desc_tokens=("escaped", "\\|"),
        why="`_md_cell` rather than postgres's `_escape_cell`: the name follows "
            "this file's own `_markdown_table`, and the fleet's other markdown "
            "renderers spell the prefix `_md_`. The SCHEME is postgres's, "
            "deliberately -- one escape vocabulary across two table formats -- "
            "and it is the scheme, not the symbol, that this suite judges. "
            "This row was written as a REQUIREMENT and run red: until the "
            "commit that turned it green it rendered a real GFM "
            "pipe table with no escaping whatsoever, and its cells carry "
            "_ws.col.Info, dns.qry.name and the TLS SNI -- bytes off an "
            "untrusted capture, emitted under -E quote=n. It is declared "
            "REVERSIBLE where jira is not, on the data rather than on taste: a "
            "backslash before a pipe is a stretch in a ticket title and "
            "routine in a packet"),

    "jira-cli": Row(
        path="ClaudeCode/skills/jira/scripts/jira.py", cls=ESCAPED,
        renderer="md_table", escaper="md_escape", delim="|",
        reversible=False,
        deviation=("backslash", "backslash_pipe", "cr", "newline"),
        desc_const=None, desc_tokens=(),
        why="A DECLARED DEVIATION with two named facts: it collapses a newline "
            "(and a CR) to a space rather than encoding it, and it does not "
            "escape the backslash, so `\\|` in a summary decodes as a column "
            "boundary. Both are deliberate for a GFM RENDERER, which is a "
            "one-way pipe to a markdown parser rather than a codec, and jira "
            "never claims reversibility. Out of group C by construction: it is "
            "a CLI the model runs for stdout, not an MCP server, so there is "
            "no tools/list to carry the scheme -- which means its escaping is "
            "documented to nobody, mitigated only by `\\|` being the spelling "
            "a markdown-literate reader guesses"),

    "checkpoint": Row(
        path="ClaudeCode/skills/checkpoint/scripts/checkpoint.py", cls=ESCAPED,
        renderer="render_toc", escaper="_toc_cell", delim="|",
        reversible=True,
        desc_const=None, desc_tokens=(),
        why="Its delimiter IS the pipe: a padded GFM table, and the one whose "
            "first reader is a COLD MODEL reading a session block by the line "
            "range in its own Start/End columns. Until the commit that added "
            "`_toc_cell` it escaped nothing -- and was nonetheless safe, "
            "because `toc_rows` split the block header on `|` UNBOUNDED, so a "
            "branch named `feat|x` reached the renderer already truncated to "
            "`feat`. That is the invariant being retired: SAFE BECAUSE THE "
            "PARSER IS LOSSY. Bounding the split so the branch survives is "
            "exactly what CREATES the reachability, which is why the parse fix "
            "and the escaper are one change and not two. Out of group C by "
            "construction, like the Jira CLI: a skill script has no tools/list "
            "to carry a scheme -- but unlike jira its reader is the model this "
            "repo ships, so the scheme is documented in the skill body instead"),

    "mcp-jenkins": Row(
        path="Scripts/mcp-jenkins.py", cls=STRUCTURE,
        renderer="_md_table", delim="  ", fence="_md_fence",
        why="two spaces separate columns, never a pipe, and the output is "
            "wrapped in a fence where GFM table parsing does not apply at all. "
            "A `|` in a cell is just a character. Its real unguarded hazard is "
            "a different one -- a cell containing two consecutive spaces -- and "
            "that is not this suite's invariant"),

    "mcp-inspect": Row(
        path="Scripts/mcp-inspect.py", cls=STRUCTURE,
        renderer="_fmt_table", delim=" ", fence="_md_fence",
        why="one space, fenced, and the ljust alignment is what disambiguates "
            "a value containing a space -- its own docstring says the "
            "alignment is load-bearing precisely when the header is dropped. A "
            "pipe has no column meaning here"),
}

# Problem codes.  The control group asserts these by name.
NO_MODULE = "MODULE-DID-NOT-IMPORT"
NO_RENDERER = "RENDERER-NOT-FOUND"
NO_ESCAPER = "ESCAPER-NOT-FOUND"
PIPE_SURVIVED = "SEPARATOR-DELIMITER-SURVIVED-IN-CELL"
NEWLINE_SURVIVED = "NEWLINE-SURVIVED-IN-CELL"
CLEAN_REWRITTEN = "CLEAN-VALUE-REWRITTEN"
NOT_REVERSIBLE = "ESCAPING-NOT-REVERSIBLE"
DEVIATION_DRIFTED = "DECLARED-DEVIATION-DRIFTED"
COLUMN_FORGED = "RENDERED-ROW-FORGED-A-COLUMN"
UNDOCUMENTED = "SCHEME-NOT-IN-TOOL-DESCRIPTION"
NO_DESC = "TOOL-DESCRIPTION-NOT-FOUND"
DELIM_IS_PIPE = "STRUCTURE-ROW-RENDERS-PIPE-SEPARATORS"
NO_FENCE = "FENCE-HELPER-NOT-FOUND"
NOT_DECLARED = "RENDERER-NOT-IN-TABLE"
BRANCH_LOST = "BRANCH-DID-NOT-ROUND-TRIP"


# ---------------------------------------------------------------------------
# the primitives -- both taken from tests/test_jira_cli.py
# ---------------------------------------------------------------------------

def unescaped_pipes(line):
    """`|` characters that still act as a COLUMN SEPARATOR in a row.

    Taken verbatim from `tests/test_jira_cli.py:320`.  An escaped `\\|` is
    counted by str.count("|") too, so it is subtracted back out -- which is the
    whole difference between "this cell contains a pipe" and "this row has an
    extra column".
    """
    return line.count("|") - line.count("\\|")


def added_pipes(text, supplied):
    """Pipes the RENDERER contributed, beyond the ones the data carried.

    This is the STRUCTURE measurement, and `unescaped_pipes` is the wrong
    instrument for it: that one counts every pipe a backslash does not escape,
    which is right in a `|`-delimited world and wrong the moment the delimiter
    is whitespace -- a literal pipe sitting quietly in a jenkins cell would read
    as a separator and the row would fail for being exactly what it declares.

    "The pipe is not my delimiter" says precisely one thing, and it is this: the
    renderer emitted no pipe it was not handed.  A `|`-delimited renderer
    misdeclared as STRUCTURE adds one per column boundary and is caught.
    """
    return text.count("|") - supplied


def split_cells(line):
    """Split a rendered `| a | b |` row on UNESCAPED delimiters.

    This is the work a READER has to do, so the suite does it rather than
    reaching for `str.split("|")` -- which is the very mistake the escaping
    exists to prevent, and which would make an escaped cell look like two.
    The leading and trailing empties a `| ... |` row produces are dropped.
    """
    cells = []
    buf = []
    index = 0
    while index < len(line):
        ch = line[index]
        if ch == "\\" and index + 1 < len(line):
            buf.append(ch)
            buf.append(line[index + 1])
            index += 2
            continue
        if ch == "|":
            cells.append("".join(buf))
            buf = []
            index += 1
            continue
        buf.append(ch)
        index += 1
    cells.append("".join(buf))
    return [cell.strip() for cell in cells[1:-1]]


# The real shape of the defect, kept in the words tests/test_jira_cli.py:429
# put it in: free text carries pipes constantly.
PIPED = "parse|render crashes on boot"


def check_escaper(fn):
    """Problems for an `escape(text) -> str` implementation.

    The shape is lifted from `tests/test_jira_cli.py:433`, which is where this
    fleet first wrote the problem down.  Shared by the live rows and by the
    control below, so a row that blesses a real escaper is passing an oracle
    that has been SHOWN to reject one which lets a pipe through.
    """
    problems = []
    got = fn(PIPED)
    if "\\|" not in got:
        problems.append("%s: the pipe was not escaped as \\|: %r"
                        % (PIPE_SURVIVED, got))
    elif unescaped_pipes(got) != 0:
        problems.append("%s: %d separator pipe(s) survived in a cell: %r"
                        % (PIPE_SURVIVED, unescaped_pipes(got), got))
    got = fn("two\nlines")
    if "\n" in got:
        problems.append("%s: a newline survived, which ends the row: %r"
                        % (NEWLINE_SURVIVED, got))
    if fn("plain value") != "plain value":
        problems.append("%s: a value with nothing to escape was rewritten: %r"
                        % (CLEAN_REWRITTEN, fn("plain value")))
    return problems


# The round-trip corpus.  Named, because a row that declares a deviation names
# the entries it is allowed to lose rather than a count of them.
ROUNDTRIP = [
    ("empty", ""),
    ("plain", "plain value"),
    ("pipe", "a|b"),
    ("backslash", "a\\b"),
    ("backslash_pipe", "a\\|b"),
    ("newline", "two\nlines"),
    ("cr", "a\rb"),
    ("tab", "a\tb"),
]

_UNESCAPE = {"\\": "\\", "|": "|", "n": "\n", "r": "\r", "t": "\t"}


def decode_cell(text):
    """Reverse the C-style escaping.  Returns (value, problem) -- problem is "".

    A backslash always consumes the next character.  A `\\X` the scheme never
    emits is MALFORMED and is REPORTED rather than passed through: silently
    accepting it is exactly how a decoder blesses an encoder that does not
    escape its own escape character.  An unescaped delimiter inside a cell is a
    column boundary and can never be part of a value.

    It decodes `_escape_cell`, not `_cell`: postgres's `\\NULL` marker belongs
    to the layer above and is correctly reported malformed here (blind spot 4).
    """
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            if i + 1 >= len(text):
                return "".join(out), "trailing backslash consumes nothing"
            nxt = text[i + 1]
            if nxt not in _UNESCAPE:
                return "".join(out), "malformed escape \\%s" % nxt
            out.append(_UNESCAPE[nxt])
            i += 2
            continue
        if ch == "|":
            return "".join(out), "an unescaped | is a column boundary"
        out.append(ch)
        i += 1
    return "".join(out), ""


def check_reversible(fn):
    """(failed_keys, detail) for `decode(encode(x)) == x` over the corpus."""
    failed = []
    detail = []
    for key, value in ROUNDTRIP:
        encoded = fn(value)
        decoded, problem = decode_cell(encoded)
        if problem:
            failed.append(key)
            detail.append("%s: %r -> %r -> %s" % (key, value, encoded, problem))
        elif decoded != value:
            failed.append(key)
            detail.append("%s: %r -> %r -> %r" % (key, value, encoded, decoded))
    return failed, detail


# ---------------------------------------------------------------------------
# loading the live renderers
# ---------------------------------------------------------------------------

class Loaded:
    def __init__(self, key, row):
        self.key = key
        self.row = row
        self.module = None
        self.renderer = None
        self.escaper = None
        self.problems = []
        self.detail = []

    def fail(self, code, line):
        if code not in self.problems:
            self.problems.append(code)
        self.detail.append("%s: %s" % (code, line))


def load(key, row):
    """Import one renderer's module from the LIVE tree and bind its functions."""
    got = Loaded(key, row)
    path = H.repo_path(*row.path.split("/"))
    try:
        got.module = H.load_module_from_path("tblcell_" + key.replace("-", "_"),
                                             path)
    except Exception as exc:                                 # pragma: no cover
        got.fail(NO_MODULE, "%s: %s" % (type(exc).__name__, exc))
        return got
    got.renderer = getattr(got.module, row.renderer, None)
    if got.renderer is None:
        got.fail(NO_RENDERER, "%s has no %s" % (row.path, row.renderer))
    if row.escaper:
        got.escaper = getattr(got.module, row.escaper, None)
        if got.escaper is None:
            got.fail(NO_ESCAPER,
                     "%s defines no %s, so every cell reaches the table raw"
                     % (row.path, row.escaper))
    return got


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

def group_escapers(suite, loaded):
    """A. the oracle, and reversibility where the row requires it."""
    for key in sorted(loaded):
        got = loaded[key]
        row = got.row
        if row.cls != ESCAPED:
            continue
        problems = [p for p in got.problems if p in (NO_MODULE, NO_ESCAPER)]
        detail = ["file        : %s" % row.path,
                  "escaper     : %s" % row.escaper,
                  "reversible  : %s" % ("REQUIRED" if row.reversible
                                        else "declared deviation")]
        if got.escaper is not None:
            problems += check_escaper(got.escaper)
            failed, why = check_reversible(got.escaper)
            detail += ["  " + line for line in why]
            if row.reversible:
                if failed:
                    problems.append("%s: %s did not round-trip"
                                    % (NOT_REVERSIBLE, ", ".join(failed)))
            else:
                # A declared deviation is asserted, not tolerated: drifting in
                # EITHER direction trips this row.
                if sorted(failed) != sorted(row.deviation):
                    problems.append(
                        "%s: lost %r, the row declares %r"
                        % (DEVIATION_DRIFTED, sorted(failed),
                           sorted(row.deviation)))
                else:
                    detail.append("note        : deviation is exactly as "
                                  "declared (%s)" % ", ".join(sorted(failed)))
        detail += ["  " + line for line in got.detail]
        detail.append("why         : %s" % row.why)
        suite.record(GA, key, problems, detail=detail,
                     brief="%s | %s | %s"
                           % (H.FAIL if problems else H.PASS, key,
                              problems[0].split(":")[0] if problems else "safe"))


def _render_postgres(module, headers, rows):
    """`_render_result` reads exactly two attributes off its QueryResult."""
    class _Res:
        columns = headers
    res = _Res()
    res.rows = rows
    return module._render_result(res)


# The same table twice: one benign cell, one carrying every character that can
# forge a boundary.  Comparing two RENDERS rather than inspecting one is what
# makes this robust against a renderer's own furniture -- a GFM rule line, a
# paging note -- without a per-row declaration of which lines are the table.
# Slicing by a guessed row count got this wrong in the first draft: tshark's
# rule line sits between the header and the data, so `lines[:1 + len(rows)]`
# stopped before the only row that mattered and the check passed on a renderer
# that escapes nothing.
RND_HEADERS = ["alpha", "beta"]
RND_BENIGN = [["plain", "harmless"]]
RND_NASTY = [["plain", "pipe|here \\ back\nand a newline"]]


def _render_checkpoint(module, headers, rows):
    """`render_toc` takes 5-tuples and returns LINES, not one string.

    The two probe columns map onto When and Branch, the only two fields that
    carry free text -- the other three are generated (`S%03d` and two line
    numbers) and can carry nothing a caller chose.
    """
    mapped = [("S%03d" % (index + 1), row[0], row[1], 1, 2)
              for index, row in enumerate(rows)]
    return "\n".join(module.render_toc(mapped))


def group_rendered(suite, loaded):
    """B. a cell's CONTENT must not change the table's structure.

    Rendered twice through the real function, and the separator count of every
    line must be identical between the two.  That covers the newline as well as
    the pipe: a cell that ends the row early changes the LINE COUNT, and two
    lists of different length are not equal.
    """
    for key in sorted(loaded):
        got = loaded[key]
        row = got.row
        if row.cls != ESCAPED:
            continue
        # Only the codes that stop this group from measuring at all.  A missing
        # escaper is group A's finding, not this one's -- carrying it here would
        # report one defect twice and hide whether the renderer itself holds.
        problems = [p for p in got.problems if p in (NO_MODULE, NO_RENDERER)]
        detail = ["file        : %s" % row.path,
                  "renderer    : %s" % row.renderer,
                  "cell        : %r" % RND_NASTY[0][1]]
        shapes = []
        if got.renderer is not None:
            for data in (RND_BENIGN, RND_NASTY):
                try:
                    if key == "mcp-postgres":
                        text = _render_postgres(got.module, RND_HEADERS, data)
                    elif key == "checkpoint":
                        text = _render_checkpoint(got.module, RND_HEADERS, data)
                    else:
                        text = got.renderer(RND_HEADERS, data)
                except Exception as exc:                      # pragma: no cover
                    problems.append("%s: renderer raised %s: %s"
                                    % (COLUMN_FORGED, type(exc).__name__, exc))
                    shapes = []
                    break
                shapes.append(([unescaped_pipes(l) for l in text.splitlines()],
                               text))
        if len(shapes) == 2:
            (benign, _bt), (nasty, nt) = shapes
            if benign != nasty:
                problems.append(
                    "%s: separators per line are %r for a harmless cell and "
                    "%r once the cell carries a pipe and a newline"
                    % (COLUMN_FORGED, benign, nasty))
            detail += ["benign      : %r" % benign,
                       "nasty       : %r" % nasty]
            detail += ["  " + line for line in nt.splitlines()]
        suite.record(GB, key, problems, detail=detail,
                     brief="%s | %s | %s"
                           % (H.FAIL if problems else H.PASS, key,
                              "forged" if problems else "structure held"))
    case_checkpoint_coupling(suite, loaded)


# Git permits `|` in a ref name -- check-ref-format forbids space, `~^:?*[\` and
# the control characters, and not this one -- so `git checkout -b 'feat|x'` is
# legal and this header is not a contrived input.
CPT_BRANCH = "feat|with-pipe"
CPT_BODY = [
    "# Session Checkpoint",
    "",
    "## SESSION S001 | 2026-09-15 07:55 | " + CPT_BRANCH,
    "",
    "body",
]


def case_checkpoint_coupling(suite, loaded):
    """B, continued: the TOC's PARSER and its RENDERER judged together.

    The other group-B cases hand a renderer a cell directly.  This one feeds the
    real `toc_rows` a real block header, because on this renderer the defect was
    never in one function.  An unbounded split made an unescaped renderer look
    safe -- the pipe never reached a cell, so the table was always well-formed
    and always named the wrong branch.  Bounding the split is what CREATES the
    reachability.  So the two assertions are made on one run: the table's
    separator count is uniform, AND the Branch cell decodes back to the name the
    header carried.  Either half alone can be satisfied by a defect.
    """
    got = loaded.get("checkpoint")
    detail = ["branch      : %r" % CPT_BRANCH,
              "header      : %r" % CPT_BODY[2]]
    if got is None or got.module is None:
        suite.record(GB, "checkpoint: the parse and the render, together",
                     ["%s: checkpoint did not load" % NO_MODULE], detail=detail)
        return
    module = got.module
    problems = []
    rows = module.toc_rows(module.parse_blocks(list(CPT_BODY)))
    region = [line for line in module.render_toc(rows) if line.startswith("|")]
    counts = [unescaped_pipes(line) for line in region]
    if len(set(counts)) != 1:
        problems.append("%s: separators per table line are %r -- a cell opened "
                        "a column" % (COLUMN_FORGED, counts))
    branch_cells = [split_cells(line)[2] for line in region[2:]]
    decoded = [decode_cell(cell)[0] for cell in branch_cells]
    if decoded != [CPT_BRANCH]:
        problems.append(
            "%s: the Branch cell decodes to %r but the header carried %r, so "
            "the TOC names a branch that does not exist"
            % (BRANCH_LOST, decoded, [CPT_BRANCH]))
    detail += ["parsed rows : %r" % (rows,),
               "branch cell : %r" % branch_cells,
               "decoded     : %r" % decoded,
               "separators  : %r" % counts]
    detail += ["  " + line for line in region]
    suite.record(GB, "checkpoint: the parse and the render, together", problems,
                 detail=detail,
                 brief="%s | checkpoint-coupling | %s"
                       % (H.FAIL if problems else H.PASS,
                          "half-fixed" if problems else "both halves hold"))


def group_documented(suite, loaded):
    """C. the scheme must be where the model reads it: the tools/list text.

    An escaping scheme the reader does not know about is a different bug, not a
    fix -- the model sees `\\|` and cannot tell an escaped literal from a value
    that really held a backslash.

    Scope, by construction rather than by omission: only ESCAPED renderers that
    live in an MCP SERVER are in this group.  `ClaudeCode/skills/jira/scripts/
    jira.py` is a CLI whose output the model reads off stdout; it has no
    tools/list to carry anything, so it is absent from this group rather than
    silently skipped inside it.  That absence is itself a finding, and it is
    recorded in that row's `why`.
    """
    for key in sorted(loaded):
        got = loaded[key]
        row = got.row
        if row.cls != ESCAPED or not row.desc_const:
            continue
        # This group's subject is the DESCRIPTION, so it carries only the code
        # that stops it reading one.  A server can document a scheme it has not
        # implemented yet, and that is a different row's complaint.
        problems = [p for p in got.problems if p == NO_MODULE]
        tool = getattr(got.module, row.desc_const, None) if got.module else None
        text = (tool or {}).get("description", "") if isinstance(tool, dict) else ""
        if not text:
            problems.append("%s: %s has no %s['description']"
                            % (NO_DESC, row.path, row.desc_const))
        else:
            missing = [t for t in row.desc_tokens if t not in text]
            if missing:
                problems.append(
                    "%s: the description never mentions %s, so a model reading "
                    "a cell cannot tell an escaped delimiter from a literal one"
                    % (UNDOCUMENTED, ", ".join(repr(m) for m in missing)))
        suite.record(GC, key, problems,
                     detail=["file        : %s" % row.path,
                             "constant    : %s" % row.desc_const,
                             "tokens      : %s" % ", ".join(
                                 repr(t) for t in row.desc_tokens),
                             "desc chars  : %d" % len(text),
                             "note        : the tokens are a floor, not the "
                             "prose -- postgres additionally teaches the decode "
                             "ORDER, which no token check can require"],
                     brief="%s | %s | %s"
                           % (H.FAIL if problems else H.PASS, key,
                              "undocumented" if problems else "on the wire"))


# The STRUCTURE probe, shared by group D and by group E's anti-self-certification
# case so the two cannot disagree about what was measured.
STC_HEADERS = ["alpha", "beta"]
STC_ROWS = [["plain", "pipe|here"]]
STC_SUPPLIED = sum(c.count("|")
                   for c in STC_HEADERS + [c for r in STC_ROWS for c in r])


def group_structure(suite, loaded):
    """D. a STRUCTURE row's reason is MEASURED, not accepted.

    The load-bearing half is the delimiter: a pipe is put in a cell and the
    rendered rows must carry no separator pipes at all, which is what makes
    escaping unnecessary rather than merely absent.  The fence is the second
    half and it is checked only as far as this suite's scope goes -- it is
    applied at the CALL SITE, and call sites are not surveyed here (blind
    spot 2).
    """
    for key in sorted(loaded):
        got = loaded[key]
        row = got.row
        if row.cls != STRUCTURE:
            continue
        problems = [p for p in got.problems if p in (NO_MODULE, NO_RENDERER)]
        detail = ["file        : %s" % row.path,
                  "renderer    : %s" % row.renderer,
                  "delimiter   : %r" % row.delim]
        if got.renderer is not None:
            text = got.renderer(STC_HEADERS, STC_ROWS)
            extra = added_pipes(text, STC_SUPPLIED)
            if extra:
                problems.append(
                    "%s: the renderer emitted %d pipe(s) it was not handed, so "
                    "the pipe IS a separator here and the row is misdeclared"
                    % (DELIM_IS_PIPE, extra))
            detail += ["supplied |  : %d" % STC_SUPPLIED,
                       "emitted  |  : %d" % text.count("|")]
            detail += ["  " + line for line in text.splitlines()]
        if row.fence and got.module is not None \
                and getattr(got.module, row.fence, None) is None:
            problems.append("%s: %s defines no %s, so the declared reason's "
                            "second half cannot hold"
                            % (NO_FENCE, row.path, row.fence))
        detail.append("why         : %s" % row.why)
        suite.record(GD, key, problems, detail=detail,
                     brief="%s | %s | %s"
                           % (H.FAIL if problems else H.PASS, key,
                              "misdeclared" if problems else "no pipe columns"))


# The census that found these six: `.ljust(`/`.rjust(` across BOTH roots the
# roster spans, plus postgres's unpadded DELIM.join and jira's `---` GFM table,
# neither of which pads.  Re-run every time, so a seventh cannot arrive unnoticed.
#
# The roots are two because the ROSTER is two.  It reached outside `Scripts/` by
# hand for the Jira CLI while this sweep read `Scripts/` only, and that gap is
# exactly what hid `checkpoint.py`: a padded pipe table using the very tokens
# swept for, sitting in a directory not swept.  A backstop narrower than the
# table it backs is not a backstop.
ALIGN_TOKENS = (".ljust(", ".rjust(")
SWEEP_ROOTS = ("Scripts", "ClaudeCode/skills")
SWEEP_EXPECTED = {
    "Scripts/mcp-inspect.py",
    "Scripts/mcp-jenkins.py",
    "Scripts/mcp-tshark.py",
    "ClaudeCode/skills/checkpoint/scripts/checkpoint.py",
}


def _sweep():
    """Aligning .py files under the swept roots, as repo-relative paths.

    Repo-relative rather than a bare filename: two roots can hold the same
    basename, and a collision would let a declared renderer vouch for an
    undeclared one.
    """
    repo = os.path.abspath(H.REPO_ROOT)
    hits = set()
    for root in SWEEP_ROOTS:
        base = H.repo_path(*root.split("/"))
        for dirpath, _dirs, names in os.walk(base):
            for name in sorted(names):
                if not name.endswith(".py"):
                    continue
                full = os.path.join(dirpath, name)
                with open(full, encoding="utf-8") as fh:
                    source = fh.read()
                if any(tok in source for tok in ALIGN_TOKENS):
                    hits.add(os.path.relpath(full, repo).replace(os.sep, "/"))
    return hits


def group_roster(suite, loaded):
    """E. the table covers the tree; totals hold; no row declares itself safe."""
    hits = _sweep()
    problems = []
    if hits != SWEEP_EXPECTED:
        new = sorted(hits - SWEEP_EXPECTED)
        gone = sorted(SWEEP_EXPECTED - hits)
        if new:
            problems.append("%s: undeclared aligning file(s): %s"
                            % (NOT_DECLARED, ", ".join(new)))
        if gone:
            problems.append("%s: declared but no longer aligning: %s"
                            % (NOT_DECLARED, ", ".join(gone)))
    suite.record(GE, "the alignment sweep finds no undeclared renderer", problems,
                 detail=["tokens      : %s" % ", ".join(ALIGN_TOKENS),
                         "roots       : %s" % ", ".join(SWEEP_ROOTS),
                         "found       : %s" % ", ".join(sorted(hits)),
                         "note        : postgres and jira align nothing -- one "
                         "joins on DELIM unpadded and the other emits a `---` "
                         "GFM rule, so both are in the table by name rather "
                         "than by this sweep"])

    esc = sorted(k for k, r in RENDERERS.items() if r.cls == ESCAPED)
    stc = sorted(k for k, r in RENDERERS.items() if r.cls == STRUCTURE)
    problems = []
    if len(esc) != DECLARED_ESCAPED:
        problems.append("ESCAPED rows are %d, declared %d"
                        % (len(esc), DECLARED_ESCAPED))
    if len(stc) != DECLARED_STRUCTURE:
        problems.append("STRUCTURE rows are %d, declared %d"
                        % (len(stc), DECLARED_STRUCTURE))
    suite.record(GE, "the declared class split matches its totals", problems,
                 detail=["ESCAPED (%d)  : %s" % (len(esc), ", ".join(esc)),
                         "STRUCTURE (%d): %s" % (len(stc), ", ".join(stc)),
                         "note        : moving a renderer between the two "
                         "routes is a decision about whether a cell can forge "
                         "a column, so it must trip something"])

    bad = []
    for key in sorted(loaded):
        got = loaded[key]
        if got.row.cls != STRUCTURE or got.renderer is None:
            continue
        text = got.renderer(STC_HEADERS, STC_ROWS)
        extra = added_pipes(text, STC_SUPPLIED)
        if extra:
            bad.append("%s declares STRUCTURE but emitted %d pipe(s) it was "
                       "not handed" % (key, extra))
    suite.record(GE, "no row declares STRUCTURE for a pipe-delimited renderer",
                 bad,
                 detail=["structure   : %d" % len(stc),
                         "note        : without this case the table is a hole "
                         "in the gate -- a failing renderer could be 'fixed' by "
                         "re-declaring it as STRUCTURE"])

    resolved = [k for k, g in loaded.items() if g.renderer is not None]
    suite.record(GE, "every declared renderer resolved in the live tree",
                 [] if len(resolved) == len(RENDERERS)
                 else ["resolved %d of %d: missing %s"
                       % (len(resolved), len(RENDERERS),
                          ", ".join(sorted(set(RENDERERS) - set(resolved))))],
                 detail=["resolved    : %d/%d" % (len(resolved), len(RENDERERS)),
                         "note        : a blindness floor -- an analyser that "
                         "stopped resolving the functions would report a clean "
                         "fleet while measuring nothing"])


# ---------------------------------------------------------------------------
# F. negative control
# ---------------------------------------------------------------------------

def ctl_ignores_pipe(text):
    """Collapses newlines and leaves the pipe alone -- the live defect.

    `tests/test_jira_cli.py:2228` plants this same mutant, in these words: it
    looks finished, and the table renders correctly right up until a value
    contains a `|`.
    """
    return " ".join(str(text).splitlines())


def ctl_no_backslash_escape(text):
    """Escapes the pipe but not its own escape character -- jira's hole.

    `a\\|b` comes back as `a\\\\|b`, which a decoder reads as one literal
    backslash followed by a COLUMN BOUNDARY.  The column count cannot see it,
    which is the whole reason reversibility is a separate clause.
    """
    return " ".join(str(text).splitlines()).replace("|", "\\|")


def ctl_lossy(text):
    """Reversible-looking, but drops the tab instead of encoding it."""
    out = str(text).replace("\\", "\\\\").replace("|", "\\|")
    return out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", " ")


def ctl_rewrites_clean(text):
    """Escapes correctly and then strips -- a clean value comes back changed."""
    out = str(text).replace("\\", "\\\\").replace("|", "\\|")
    return out.replace("\n", "\\n").replace("\r", "\\r").replace(
        "\t", "\\t").strip().rstrip("e")


def ctl_good(text):
    """The canonical form: backslash FIRST, then the delimiter."""
    out = str(text).replace("\\", "\\\\").replace("|", "\\|")
    return out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def ctl_good_verbose(text):
    """The same scheme spelled with a loop -- the rule is the property."""
    out = []
    for ch in str(text):
        out.append({"\\": "\\\\", "|": "\\|", "\n": "\\n",
                    "\r": "\\r", "\t": "\\t"}.get(ch, ch))
    return "".join(out)


# ---------------------------------------------------------------------------
# The PAIRING control, and it is the one that earns its place.
#
# The checkpoint TOC's defect lived in TWO functions and in neither of them
# alone.  A lossy parse makes an unescaped renderer look safe; an escaping
# renderer makes a lossy parse look harmless.  Both of those satisfy the
# SEPARATOR assertion while failing the round trip, so a case checking only the
# column count would have blessed either -- which is what happened for as long
# as the file existed.
#
# The fourth combination is the one worth reading twice, and it was measured
# rather than predicted: bounding the split WITHOUT adding the escaper fails
# BOTH assertions.  The branch survives the parse and the raw render loses it
# again, to a reader splitting on the unescaped pipe.  Fixing the parser alone
# is not a partial fix; it is strictly worse than the state it replaces, and
# that is the whole reason the parse fix and the escaper are one change.
#
# These are copies of the OLD behaviour rather than reads of the live file, on
# purpose: they keep asserting the pairing after the live file is fixed, which a
# case built from the live file cannot do.
# ---------------------------------------------------------------------------

def ctl_parse_unbounded(header):
    """The OLD `toc_rows` split: `header.split("|")` with no maxsplit.

    A branch named `feat|with-pipe` arrives as `feat`.  The renderer downstream
    then has nothing to escape, which is exactly why the table looked correct.
    """
    fields = [f.strip() for f in header.split("|")]
    fields += [""] * (3 - len(fields))
    return fields[2]


def ctl_parse_bounded(header):
    """The fixed split: everything after the second `|` IS the branch."""
    fields = [f.strip() for f in header.split("|", 2)]
    fields += [""] * (3 - len(fields))
    return fields[2]


def ctl_render_raw(cells):
    """The OLD `render_toc` row: join on `" | "`, escape nothing."""
    return "| " + " | ".join(cells) + " |"


def ctl_render_escaped(cells):
    """The same row with the fleet's escaping applied to each cell first."""
    return "| " + " | ".join(ctl_good(c) for c in cells) + " |"


# (parse, render, name, must the branch round-trip?, must the row stay uniform?)
PAIRS = [
    (ctl_parse_unbounded, ctl_render_raw, "lossy-parse-and-raw-render",
     False, True),
    # MEASURED, not assumed, and it is the sharpest row here: bounding the split
    # alone fails BOTH assertions.  The branch survives the parse and the raw
    # render loses it again, because a reader splitting on unescaped pipes reads
    # `feat` out of a forged column -- so fixing the parser on its own is not a
    # partial fix, it is a worse state than the original.
    (ctl_parse_bounded, ctl_render_raw, "fixed-parse-and-raw-render",
     False, False),
    (ctl_parse_unbounded, ctl_render_escaped, "lossy-parse-and-escaped-render",
     False, True),
    (ctl_parse_bounded, ctl_render_escaped, "fixed-parse-and-escaped-render",
     True, True),
]


def case_pairing_control(suite):
    """F, continued: three of four combinations are wrong, each in its own way."""
    header = "SESSION S001 | 2026-09-15 07:55 | " + CPT_BRANCH
    safe = 0
    for parse, render, name, want_trip, want_uniform in PAIRS:
        cells = ["S001", "2026-09-15 07:55", parse(header), "1", "2"]
        line = render(cells)
        want_separators = len(cells) + 1
        uniform = unescaped_pipes(line) == want_separators
        trip = decode_cell(split_cells(line)[2])[0] == CPT_BRANCH
        problems = []
        if trip != want_trip:
            problems.append("%s: round-trip=%r, declared %r (cell %r)"
                            % (BRANCH_LOST, trip, want_trip,
                               split_cells(line)[2]))
        if uniform != want_uniform:
            problems.append("%s: %d separator(s), declared %s%d"
                            % (COLUMN_FORGED, unescaped_pipes(line),
                               "" if want_uniform else "NOT ", want_separators))
        if trip and uniform:
            safe += 1
        suite.record(GF, "pair-" + name, problems,
                     detail=["header      : %r" % header,
                             "branch      : %r" % parse(header),
                             "rendered    : %r" % line,
                             "round-trip  : %r (declared %r)" % (trip, want_trip),
                             "uniform     : %r (declared %r)"
                             % (uniform, want_uniform)],
                     brief="%s | pair-%s | %s"
                           % (H.FAIL if problems else H.PASS, name,
                              "as declared" if not problems else "drifted"))

    suite.record(GF, "only the PAIR is safe",
                 [] if safe == 1
                 else ["%d of %d combinations satisfied BOTH assertions; "
                       "exactly one may" % (safe, len(PAIRS))],
                 detail=["combinations: %d" % len(PAIRS),
                         "both-hold   : %d" % safe,
                         "note        : this is the case the live suite cannot "
                         "make. Once the file is fixed every live assertion "
                         "passes, and nothing would then show that the two "
                         "halves are load-bearing TOGETHER rather than "
                         "separately"])


CONTROLS = [
    # (fn, must the oracle complain?, must reversibility fail?)
    (ctl_ignores_pipe, True, True),
    (ctl_no_backslash_escape, False, True),
    (ctl_lossy, False, True),
    (ctl_rewrites_clean, True, True),
    (ctl_good, False, False),
    (ctl_good_verbose, False, False),
]


def group_control(suite):
    """F. escapers with one defect each, and correct ones it must stay silent on."""
    caught = 0
    for fn, want_oracle, want_irreversible in CONTROLS:
        oracle = check_escaper(fn)
        failed, why = check_reversible(fn)
        problems = []
        if bool(oracle) != want_oracle:
            problems.append("oracle complained=%r, expected %r: %r"
                            % (bool(oracle), want_oracle, oracle))
        if bool(failed) != want_irreversible:
            problems.append("round-trip failed=%r (%r), expected %r"
                            % (bool(failed), failed, want_irreversible))
        if want_oracle or want_irreversible:
            caught += 1 if (oracle or failed) else 0
        suite.record(GF, "control-" + fn.__name__, problems,
                     detail=["expected    : oracle=%r irreversible=%r"
                             % (want_oracle, want_irreversible),
                             "oracle      : %r" % oracle,
                             "round-trip  : %r" % failed]
                            + ["  " + line for line in why],
                     brief="%s | control-%s | %s"
                           % (H.FAIL if problems else H.PASS, fn.__name__,
                              "as declared" if not problems else "drifted"))

    must = sum(1 for _f, a, b in CONTROLS if a or b)
    suite.record(GF, "control fires at all",
                 [] if caught == must
                 else ["only %d of %d defective escapers were rejected"
                       % (caught, must)],
                 detail=["controls    : %d (%d defective, %d correct)"
                         % (len(CONTROLS), must, len(CONTROLS) - must),
                         "note        : without this group, an oracle that "
                         "silently accepted everything would be "
                         "indistinguishable from a fleet that already escapes"])

    case_pairing_control(suite)


def group_hygiene(suite, loaded, pyc_before, tree_before):
    """G. writes nothing, no bytecode, no new paths, live modules only."""
    suite.record(GG, "this suite writes nothing at all",
                 [] if not WRITES
                 else ["%d write(s): %s" % (len(WRITES), WRITES[:5])],
                 detail=["writes      : %d" % len(WRITES),
                         "note        : there is no sandbox because there is no "
                         "fixture on disk -- the control escapers are functions "
                         "in this file, which is the strongest form of the "
                         "claim rather than a weaker one"])

    pyc_after = H.pycache_snapshot()
    new = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    suite.record(GG, "no .pyc written anywhere in the repo tree",
                 [] if not (new or touched)
                 else ["new=%r touched=%r" % (new, touched)],
                 detail=["pyc before=%d after=%d"
                         % (len(pyc_before), len(pyc_after)),
                         "note        : this suite IMPORTS six modules, so "
                         "this case is load-bearing here rather than "
                         "ceremonial"])

    added = sorted(H.repo_tree() - tree_before)
    suite.record(GG, "no new repo paths",
                 [] if not added
                 else ["%d new path(s): %s" % (len(added), added[:5])],
                 detail=["note        : importing a server must not create a "
                         "capture directory, a cache, or a log"])

    root = os.path.abspath(H.REPO_ROOT)
    scratch = os.path.join(root, ".claude", "tmp")
    stale = []
    for key in sorted(loaded):
        module = loaded[key].module
        path = os.path.abspath(getattr(module, "__file__", "") or "")
        if not path.startswith(root + os.sep) or path.startswith(scratch + os.sep):
            stale.append("%s loaded from %s" % (key, path or "<nowhere>"))
    suite.record(GG, "every module came from the live tree", stale,
                 detail=["modules     : %d" % len(loaded),
                         "note        : this repo keeps stale server snapshots "
                         "under .claude/tmp, so a suite that measured one would "
                         "report on code nobody runs"])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="a rendered table cell cannot forge a column "
                          "boundary: every renderer either escapes its own "
                          "delimiter and says so, or has none to escape",
                    opts=opts, mode="grouped", cid_width=30)
    pyc_before = H.pycache_snapshot()
    tree_before = H.repo_tree()
    del WRITES[:]

    loaded = {key: load(key, row) for key, row in RENDERERS.items()}

    group_escapers(suite, loaded)
    group_rendered(suite, loaded)
    group_documented(suite, loaded)
    group_structure(suite, loaded)
    group_roster(suite, loaded)
    group_control(suite)
    group_hygiene(suite, loaded, pyc_before, tree_before)

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
