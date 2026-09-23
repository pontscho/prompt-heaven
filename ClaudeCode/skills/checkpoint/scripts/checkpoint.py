#!/usr/bin/env python3
"""
Read sections out of a checkpoint markdown file by markdown header structure,
write a new session block to the top of it, and maintain its line-range table
of contents.
Usage: checkpoint.py {next-number|latest|mission|nexts|activate [ID]|toc|list|session <ID>} [--file PATH]
       checkpoint.py prepend [--block-file PATH] [--file PATH] [--overwrite]

The parser keys entirely off markdown header level (`## `) plus a prefix token
(SESSION / ACTIVATION / MISSION); it never regexes arbitrary body content. A
"block" runs from its `## ` header line up to (but not including) the next `## `
line, or EOF.

Each session carries its activation prompt as its OWN block, `## ACTIVATION
S<NNN>`, directly below the `## SESSION S<NNN>` it belongs to. It used to be a
`### ACTIVATION` subsection inside the session, which made the one thing a user
pastes the one thing no command could address: `latest` printed it buried in
the whole session, and the TOC had no row for it. As a block it gets a TOC row
(`A<NNN>`), `activate` prints it paste-ready, and `nexts` prints the three
blocks a resuming session needs -- MISSION, the newest SESSION, its ACTIVATION --
in one call. Files written before the change keep their `### ACTIVATION`
subsections, and both readers fall back to them; only a NEW segment handed to
`prepend` is held to the new shape.

`toc` reports each block's 1-indexed start and end line, so a cold reader can
Read one block by offset instead of the whole file. `toc --write` rewrites the
TOC region between the marker comments just below the H1 title. Every prepend
shifts every line number below it, so the TOC is REGENERATED, never hand-edited
-- and the write is atomic (temp file + os.replace), because a half-written
checkpoint is worse than a stale one.

`prepend` exists because the alternative was DISCIPLINE. Adding a session used
to be four steps across two writers -- read `toc`, insert the block at the line
it reported, then remember to run `toc --write` -- and every one of the three
seams in that sequence is a real defect: an insert line that was typed wrong or
had gone stale, a `toc --write` that never ran, and the window between the two
where the file says one thing and its own table says another. `prepend` takes
the segment as a FILE and does both halves inside one os.replace, so the block
and the table it describes become visible in the same instant, and neither the
line number nor the second command is anybody's job to remember.

`with_toc()` is the single TOC generator: `toc --write` and `prepend` both go
through it. Two writers that each rendered their own region would agree right
up until one of them was changed.

`prepend` also refuses a segment carrying an id the file already has. That gate
is CONTENT-based on purpose: the block file has a default path and therefore
survives between checkpoints, so a segment nobody rewrote would otherwise be
inserted a second time -- silently, under a duplicate id. The same gate
enforces HALF of "the new block's id comes from next-number, do not hand-guess
it": a guessed id that COLLIDES with one already in the file is refused. A
guessed id that skips one (S001 then S003) collides with nothing and goes
through -- the gate catches collisions, not gaps, and following `next-number`
is still the caller's job.
"""

import argparse
import collections
import os
import re
import sys
import tempfile

DEFAULT_FILE = ".claude/tmp/checkpoint.md"
# Where `prepend` looks for the block text when nobody says otherwise, so the
# skill's second call carries no arguments at all -- one path it cannot mistype.
# It also means the file SURVIVES between checkpoints, which is precisely why
# the duplicate-id gate below is not optional: a stale segment left at this
# path would otherwise be re-inserted verbatim.
#
# Deliberately NOT named checkpoint-something: it shares a directory with
# DEFAULT_FILE, so a `checkpoint*` glob would return two files, and the temp
# files write_atomic leaves mid-write are `.checkpoint-*.tmp` -- three things
# one word apart from each other is one confusion waiting to happen. `session`
# is the skill's own word for what is in this file: one `## SESSION` block and
# the `## ACTIVATION` block that belongs to it.
DEFAULT_BLOCK_FILE = ".claude/tmp/session-block.md"

# The title a file gets when it is created from nothing. Only ever used when
# there is no existing H1 to keep: a file that already has one keeps it, so a
# hand-picked title survives even an --overwrite.
H1_TITLE = "# Session Checkpoint"

SESSION_RE = re.compile(r"^SESSION\s+S(\d+)\b")
# The id is the SESSION's own id, not a counter of its own: an activation block
# is addressed through the session it belongs to, and `next-number` stays a
# function of the SESSION ids alone.
ACTIVATION_RE = re.compile(r"^ACTIVATION\s+S(\d+)\b")
# The pre-block form, a subsection inside a SESSION. Read-only from here on:
# `activate` and `nexts` still find it in an old file, `prepend` refuses it in a
# new segment. Case-insensitive, because the refusal must not be dodged by a
# `### Activation` that a reader would still take for the prompt.
LEGACY_ACTIVATION_RE = re.compile(r"^###\s+ACTIVATION\b", re.IGNORECASE)
# A fence opener or closer: three or more backticks or tildes after optional
# indentation. A block that DOCUMENTS this format quotes `### ACTIVATION` inside
# a fence, and taking that quote for the real subsection would refuse a correct
# segment -- or, in an old file, hand the user the quote as their prompt.
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")

# Markers are matched by PREFIX, never by full text, so the generated-by note
# can be reworded without orphaning regions written by an older version.
TOC_BEGIN_PREFIX = "<!-- TOC:BEGIN"
TOC_END_PREFIX = "<!-- TOC:END"
TOC_BEGIN = TOC_BEGIN_PREFIX + " -- generated by checkpoint.py toc --write; do not edit by hand -->"
TOC_END = TOC_END_PREFIX + " -->"
TOC_COLUMNS = ("Session", "When", "Branch", "Start", "End")
# Lines a region carries besides its rows: two markers, the header, the rule.
# This is what makes the region's height a function of the ROW COUNT alone.
TOC_FRAME_LINES = 4

# `lines` is mutated during the parse; `start` is the block's 1-indexed line.
Block = collections.namedtuple("Block", "lines start")


def read_lines(path):
    """File as a list of lines, newlines stripped, 1-indexed by position+1.

    UTF-8 explicitly, never the locale's encoding: a checkpoint may carry
    verbatim non-English quotes (SKILL.md rule 8a), and every `toc --write`
    re-encodes the WHOLE file. Under LC_ALL=C the locale encoding is ASCII, so
    the default would make reading the user's own checkpoint fail -- and, worse
    on a lenient platform, silently rewrite bytes outside the TOC region.

    Strict decoding, and a byte that is not UTF-8 is a refusal on the `die`
    route rather than a traceback: every other failure in this script is one
    line on stderr and exit 2, and a reader that crashed with exit 1 would be
    the one exception. Decoding with errors="replace" instead would be worse
    than either -- `toc --write` would then write the replacement characters
    back, silently changing bytes inside an immutable block.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().splitlines()
    except UnicodeDecodeError as exc:
        die("%s is not valid UTF-8 (byte 0x%02x at offset %d) -- refusing to "
            "read it" % (path, exc.object[exc.start], exc.start))


def parse_blocks(lines):
    """Split lines into `## ` blocks. Each block carries its raw lines (starting
    with its `## ` header line) and its 1-indexed start line. Lines before the
    first `## ` -- the H1 title, and the TOC region, which is why it must never
    contain a `## ` line -- are ignored."""
    blocks = []
    current = None
    for number, line in enumerate(lines, start=1):
        if line.startswith("## "):
            if current is not None:
                blocks.append(current)
            current = Block([line], number)
        elif current is not None:
            current.lines.append(line)
    if current is not None:
        blocks.append(current)
    return blocks


def load_blocks(path):
    return parse_blocks(read_lines(path))


def header_of(block):
    """Header line with the leading `## ` stripped and trailing space trimmed."""
    return block.lines[0][3:].rstrip()


def text_of(block):
    """Full block text, trailing blank lines / whitespace trimmed, internal
    formatting preserved exactly."""
    return "\n".join(block.lines).rstrip()


def end_of(block):
    """1-indexed last line of the block's CONTENT -- trailing blank lines
    excluded, so `Read offset=start limit=end-start+1` lands exactly on the
    block and nothing else."""
    return block.start + len(text_of(block).splitlines()) - 1


def session_num(block):
    """Integer session id if this block is a SESSION header, else None."""
    m = SESSION_RE.match(header_of(block))
    return int(m.group(1)) if m else None


def activation_num(block):
    """Integer session id if this block is an `ACTIVATION S<NNN>` header, else
    None -- the id of the session the prompt belongs to."""
    m = ACTIVATION_RE.match(header_of(block))
    return int(m.group(1)) if m else None


def is_mission(block):
    return header_of(block) == "MISSION"


def unfenced(lines):
    """(index, line) for every line of `lines` OUTSIDE a fenced code block.

    Only the `### ACTIVATION` scans use this; `parse_blocks` deliberately does
    not, because changing what counts as a `## ` header would move the block
    boundaries of files already on disk. A fence closes only on the character
    that opened it, so a ``` quoted inside a ~~~ fence does not end it.

    Known limits, kept on purpose because this only has to tell a quoted
    `### ACTIVATION` from a real one:
      * looser than CommonMark -- any indentation opens or closes a fence (not
        just up to three spaces), and neither the closer's length nor an info
        string on it is checked, so ```` can be closed by ```;
      * `parse_blocks` does not track fences at all, so a `## ` line inside a
        fence is still a block header. A session therefore cannot quote a
        `## ACTIVATION S0xx` or `## SESSION ...` header even inside a fence:
        the quote becomes a real block, and the shape gate refuses the segment.
    """
    opener = None
    for index, line in enumerate(lines):
        m = FENCE_RE.match(line)
        if m:
            char = m.group(1)[0]
            if opener is None:
                opener = char
                continue
            if char == opener:
                opener = None
                continue
        if opener is None:
            yield index, line


def legacy_activation(block):
    """The body lines of a SESSION block's old `### ACTIVATION` subsection, or
    None if it has none: from the line after that header up to the next `### `
    header or the end of the block. Fence-aware, so a quoted header inside a
    code sample is neither the start nor the end of it."""
    body = block.lines[1:]
    start = None
    for index, line in unfenced(body):
        if start is None:
            if LEGACY_ACTIVATION_RE.match(line):
                start = index + 1
        elif line.startswith("### "):
            return body[start:index]
    return None if start is None else body[start:]


def dequote(lines):
    """The prompt PASTE-READY: a leading `> ` or bare `>` removed from each line,
    blank lines trimmed at both ends. A line with no `>` passes through as it
    is, because some old checkpoints wrote the prompt as plain text."""
    out = []
    for line in lines:
        if line.startswith("> "):
            line = line[2:]
        elif line.startswith(">"):
            line = line[1:]
        out.append(line)
    start = 0
    while start < len(out) and not out[start].strip():
        start += 1
    return trim_trailing_blanks(out[start:])


def parse_session_id(raw_id, allow_activation=False):
    """(kind, number) for an id as a user types it, or None if it is not one.

    ONE parser for both readers, so `session` and `activate` cannot come to
    disagree on what `S042` means. Session spellings are S042, s042, 42 and
    042; with `allow_activation`, A042 / a042 name the ACTIVATION block of
    session 42, which is what makes every TOC label a valid `session` argument.
    """
    token = raw_id.strip()
    kind = "S"
    if token[:1] in ("S", "s"):
        token = token[1:]
    elif allow_activation and token[:1] in ("A", "a"):
        kind = "A"
        token = token[1:]
    try:
        return kind, int(token)
    except ValueError:
        return None


def die(message):
    sys.stderr.write("checkpoint: %s\n" % message)
    sys.exit(2)


def require_file(path):
    """Refuse anything that is not a regular file, on the `die` path.

    isfile, not exists: a directory satisfies os.path.exists and then makes the
    open() below it raise, which is a traceback and exit 1 where every other
    refusal in this script is one line on stderr and exit 2. The two branches
    are kept apart because they are different mistakes -- a missing checkpoint
    is the normal state of a fresh machine, a directory in its place is not.
    """
    if not os.path.exists(path):
        die("file not found: %s" % path)
    if not os.path.isfile(path):
        die("not a regular file: %s" % path)


def toc_rows(blocks):
    """One row per SESSION / ACTIVATION / MISSION block: (label, when, branch,
    start, end).

    An ACTIVATION block is labelled `A%03d`, never `S%03d`: the labels feed the
    duplicate-id gate, and a session and its own prompt sharing a label would
    read as a collision on every prepend. When and Branch stay empty -- the
    session row directly above already says both.

    The when/branch fields come from the pipe-separated header, and the split is
    BOUNDED at two so the third field keeps whatever it contains. This used to
    be an unbounded split, on the argument that a pipe inside a branch name
    breaks the header rather than this parser. True, and it hid the real cost:
    git permits `|` in a ref name, and an unbounded split does not fail on one,
    it SILENTLY TRUNCATES -- a session on `feat|x` was recorded as `feat`, in
    the one artefact whose whole job is telling a cold reader where it is.

    Bounding the split is what makes a delimiter reachable in a rendered cell,
    so it is why `render_toc` escapes. The two changes are one change: before,
    the table was safe only because this parse was lossy.
    """
    rows = []
    for block in blocks:
        number = session_num(block)
        if number is not None:
            fields = [field.strip() for field in header_of(block).split("|", 2)]
            fields += [""] * (3 - len(fields))
            rows.append(("S%03d" % number, fields[1], fields[2], block.start, end_of(block)))
        elif activation_num(block) is not None:
            rows.append(("A%03d" % activation_num(block), "", "", block.start, end_of(block)))
        elif is_mission(block):
            rows.append(("MISSION", "", "", block.start, end_of(block)))
    return rows


def block_labels(lines):
    """The TOC labels a body carries: S%03d per session, A%03d per activation
    prompt, MISSION for the tail. A stale session+activation segment therefore
    clashes on BOTH of its labels.

    Deliberately routed through toc_rows -- the same single parse `toc` and
    `next-number` use. A second id parser here could disagree with the one that
    HANDS OUT the next id, which is the exact way a duplicate gets in.
    """
    return [row[0] for row in toc_rows(parse_blocks(lines))]


def _toc_cell(value):
    r"""Escape one cell so a value cannot open a column. Reversible: \\ \| \n \r \t.

    The delimiter of this table is the pipe, so a pipe inside a value does not
    produce a broken cell -- it produces a COLUMN, and the row stops matching
    its header while staying valid markdown. That matters more here than almost
    anywhere: this table's first reader is a cold model locating a block by the
    Start and End columns, so a shifted row sends it to the wrong lines.

    Same vocabulary as `Scripts/mcp-postgres.py:_escape_cell` and
    `Scripts/mcp-tshark.py:_md_cell` -- one spelling across the whole tree. The
    escape character is escaped FIRST; doing the delimiter first would let the
    backslash pass re-escape what it just wrote.
    """
    text = str(value).replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def render_toc(rows, offset=0):
    """The TOC region as lines, markers included, padded so it reads straight
    out of an editor. Its HEIGHT depends only on the number of rows, never on
    the offset -- that is what lets `toc --write` account for the shift the TOC
    itself causes in a second render instead of iterating to a fixed point.

    Cells are escaped BEFORE the widths are measured, because a width taken
    from the raw value mis-pads every row that gained an escape. Escaping does
    not change the row COUNT, so the height contract above is untouched.
    """
    table = [list(TOC_COLUMNS)]
    for label, when, branch, start, end in rows:
        table.append([label, when, branch, str(start + offset), str(end + offset)])
    table = [[_toc_cell(cell) for cell in row] for row in table]
    widths = [max(len(row[i]) for row in table) for i in range(len(TOC_COLUMNS))]

    def row_line(row):
        cells = [
            cell.rjust(width) if index >= 3 else cell.ljust(width)
            for index, (cell, width) in enumerate(zip(row, widths))
        ]
        return "| " + " | ".join(cells) + " |"

    rule = "|" + "|".join("-" * (width + 2) for width in widths) + "|"
    return [TOC_BEGIN, row_line(table[0]), rule] + [row_line(r) for r in table[1:]] + [TOC_END]


def find_marker(lines, prefix):
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            return index
    return -1


def preamble_end(lines):
    """Index of the first `## ` line -- one past the end of the preamble.

    The TOC region lives strictly between the H1 and the first block, so
    marker matching stops here. It has to: any session block that DOCUMENTS
    this feature quotes both marker comments, and a match inside a block would
    strip those lines out of it -- silent data loss in a file whose whole
    contract is that a written block is immutable.
    """
    for index, line in enumerate(lines):
        if line.startswith("## "):
            return index
    return len(lines)


def strip_toc(lines):
    """The file without its TOC region -- the exact inverse of what `toc --write`
    inserts, blank separator included. Regenerating from the stripped body is
    what keeps the write idempotent as the TOC grows a row."""
    limit = preamble_end(lines)
    begin = find_marker(lines[:limit], TOC_BEGIN_PREFIX)
    if begin < 0:
        return list(lines)
    end = find_marker(lines[begin:limit], TOC_END_PREFIX)
    if end < 0:
        die("unterminated TOC region -- refusing to guess where it ends")
    head = lines[:begin]
    if head and not head[-1].strip():
        head = head[:-1]
    return head + lines[begin + end + 1 :]


def toc_region_of(lines):
    """The generated region inside `lines`, markers included, or [].

    What `prepend` prints: the region it just wrote IS the table, so echoing
    those lines cannot disagree with the file the way a second render could.
    """
    limit = preamble_end(lines)
    begin = find_marker(lines[:limit], TOC_BEGIN_PREFIX)
    if begin < 0:
        return []
    end = find_marker(lines[begin:limit], TOC_END_PREFIX)
    return [] if end < 0 else lines[begin:begin + end + 1]


def trim_trailing_blanks(lines):
    """`lines` without its trailing blank lines."""
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    return lines[:end]


def with_toc(body, path):
    """A TOC-LESS body, plus a freshly generated region under its H1.

    THE single TOC generator -- `toc --write` and `prepend` both end here, so
    the two writing paths cannot drift apart, and a prepended file is
    byte-identical to what a `toc --write` over the same body would produce.

    Two renders, no fixed-point loop: the first one only measures how tall the
    region is, which is exactly what every line below it shifts by. That works
    because the region's HEIGHT depends only on the number of rows, never on
    the digits in them.
    """
    if not body or not body[0].startswith("# "):
        die("no H1 title on line 1 of %s -- refusing to write a TOC" % path)
    rows = toc_rows(parse_blocks(body))
    if not rows:
        die("no SESSION or MISSION block in %s" % path)
    height = 1 + len(render_toc(rows))
    region = render_toc(rows, offset=height)
    return body[:1] + [""] + region + body[1:]


def write_atomic(path, lines):
    """Replace the file in one os.replace, so an interrupted write can never
    truncate a checkpoint. Joining without rstrip reproduces every byte outside
    the TOC region exactly, trailing blank lines included."""
    data = "\n".join(lines) + "\n"
    directory = os.path.dirname(os.path.abspath(path))
    mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".checkpoint-", suffix=".tmp")
    try:
        # UTF-8, to match read_lines: the encoding of a checkpoint must not
        # depend on the environment of whoever happened to run the script.
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(data)
        os.chmod(tmp, mode)  # mkstemp is 0600; the file we replace is not
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def cmd_next_number(path):
    if not os.path.exists(path):
        print("S001")  # a missing checkpoint is the first-ever write, not an error
        return 0
    # It exists, so only the not-a-regular-file branch can fire here: without
    # it a directory at the checkpoint's path would reach load_blocks and come
    # back as a traceback, which is the one failure route this script does not
    # have.  The missing-file contract above is untouched.
    require_file(path)
    nums = [n for n in (session_num(b) for b in load_blocks(path)) if n is not None]
    if not nums:
        print("S001")
        return 0
    print("S%03d" % (max(nums) + 1))
    return 0


def cmd_latest(path):
    require_file(path)
    for block in load_blocks(path):
        if session_num(block) is not None:
            print(text_of(block))
            return 0
    # `die`, not a bare `return 2`: the exit code is unchanged, but a writer's
    # refusals all have to arrive by the same route and say what went wrong.
    # A silent 2 reads as "the block was empty" to whoever is watching stdout.
    die("no SESSION block in %s" % path)


def cmd_mission(path):
    require_file(path)
    for block in load_blocks(path):
        if is_mission(block):
            print(text_of(block))
            return 0
    die("no MISSION block in %s" % path)


def find_block(blocks, id_of, wanted):
    """The first block whose `id_of` is `wanted`, or None."""
    return next((b for b in blocks if id_of(b) == wanted), None)


def cmd_activate(path, raw_id=None):
    """Print one session's activation prompt, paste-ready.

    Default is the newest session -- the first SESSION block in file order, the
    same one `latest` prints. The `## ACTIVATION S<NNN>` block wins; only a
    session that has none falls back to its old `### ACTIVATION` subsection,
    which is the only form any checkpoint written before the block existed has.
    """
    require_file(path)
    blocks = load_blocks(path)
    if raw_id is None:
        session = next((b for b in blocks if session_num(b) is not None), None)
        if session is None:
            die("no SESSION block in %s" % path)
        wanted = session_num(session)
    else:
        parsed = parse_session_id(raw_id)
        if parsed is None:
            die("%r is not a session id -- expected S042, s042, 42 or 042"
                % raw_id)
        wanted = parsed[1]
        session = find_block(blocks, session_num, wanted)

    _block, prompt = resolve_prompt(blocks, wanted, session, path)
    print("\n".join(prompt))
    return 0


def resolve_prompt(blocks, wanted, session, path):
    """(activation block or None, de-quoted prompt lines) for session `wanted`.

    The ONE place that decides whether a session has a usable prompt, so
    `activate` and `nexts` cannot disagree about it: the `## ACTIVATION` block
    wins, the old `### ACTIVATION` subsection inside `session` is the fallback,
    and a prompt that is missing in both forms -- or present but empty once
    de-quoted -- is a refusal. The block comes back as None in legacy mode,
    which is how `nexts` knows the session already carries the prompt.
    """
    block = find_block(blocks, activation_num, wanted)
    if block is not None:
        raw = block.lines[1:]
    elif session is not None:
        raw = legacy_activation(session)
    else:
        die("session S%03d not found in %s" % (wanted, path))
    if raw is None:
        die("no activation prompt for S%03d in %s -- neither an `## ACTIVATION "
            "S%03d` block nor a `### ACTIVATION` subsection in the session"
            % (wanted, path, wanted))
    prompt = dequote(raw)
    if not prompt:
        # An empty answer on exit 0 reads as "there is nothing to paste" -- the
        # one thing the user asked for. `prepend` refuses to write one; this is
        # for a file that got one some other way.
        die("the activation prompt for S%03d in %s is empty" % (wanted, path))
    return block, prompt


def cmd_nexts(path):
    """Print what a resuming session needs, in the order it needs it: MISSION,
    the newest SESSION, that session's ACTIVATION block -- each verbatim, so
    the output stays a sequence of self-labelled blocks.

    Everything is looked up BEFORE anything is printed: a partial answer (the
    mission, then a refusal) is exactly what a caller skimming stdout would take
    for the whole thing. On an old file the prompt lives INSIDE the session
    block, so printing it again would show it twice; there the session alone
    carries it. A missing or empty prompt is refused exactly as `activate`
    refuses it -- both go through `resolve_prompt`.
    """
    require_file(path)
    blocks = load_blocks(path)
    mission = next((b for b in blocks if is_mission(b)), None)
    if mission is None:
        die("no MISSION block in %s" % path)
    session = next((b for b in blocks if session_num(b) is not None), None)
    if session is None:
        die("no SESSION block in %s" % path)
    wanted = session_num(session)
    parts = [text_of(mission), text_of(session)]
    block, _prompt = resolve_prompt(blocks, wanted, session, path)
    if block is not None:
        parts.append(text_of(block))
    print("\n\n".join(parts))
    return 0


def cmd_toc(path, write=False):
    require_file(path)
    raw = read_lines(path)

    if not write:
        rows = toc_rows(parse_blocks(raw))
        if not rows:
            die("no SESSION or MISSION block in %s" % path)
        for line in render_toc(rows):
            print(line)
        return 0

    lines = with_toc(strip_toc(raw), path)
    write_atomic(path, lines)
    region = toc_region_of(lines)
    print("checkpoint: TOC updated -- %d entries, %d lines"
          % (len(region) - TOC_FRAME_LINES, len(region)))
    return 0


def read_segment(block_path, path):
    """The validated lines to insert: one or more `## ` blocks, nothing else.

    Every refusal here happens BEFORE anything is written, so a rejected
    segment leaves the checkpoint exactly as it was. The rules are the ones
    that decide whether the inserted text is addressable at all afterwards --
    a segment that does not start with a `## ` header is invisible to the
    parser, and a second `# ` H1 would give the file two titles and put the
    generated region above the wrong one. The last rule, `check_segment_shape`,
    is the one that decides whether `activate` / `nexts` can find the prompt.
    """
    if os.path.realpath(block_path) == os.path.realpath(path):
        # The footgun: --block-file pointing at the checkpoint itself would
        # read the whole document and insert a copy of it into itself.
        die("--block-file is the checkpoint itself (%s) -- refusing to insert "
            "a file into itself" % path)
    if not os.path.exists(block_path):
        die("block file not found: %s" % block_path)
    if not os.path.isfile(block_path):
        die("block file is not a regular file: %s" % block_path)

    segment = read_lines(block_path)
    content = [line for line in segment if line.strip()]
    if not content:
        die("block file is empty: %s" % block_path)
    if not content[0].startswith("## "):
        die("a block must start with a `## ` header line, but %s starts with "
            "%r -- as written it would be invisible to every reader"
            % (block_path, content[0][:60]))
    title = next((line for line in segment if line.startswith("# ")), None)
    if title is not None:
        die("the block carries an H1 title (%r) -- a checkpoint has exactly "
            "one, on line 1" % title[:60])
    check_segment_shape(segment, block_path)
    return segment


def check_segment_shape(segment, block_path):
    """Refuse a segment whose blocks do not pair every session with its prompt.

    A shape rule, not a collision rule, so it runs on every write path -- fresh,
    --overwrite and normal alike -- and before the duplicate gate: a segment
    that is malformed AND stale is reported for the thing that is wrong with
    the segment itself. It reads ONLY the segment. The file's existing blocks
    were written under whatever rule held at the time, and an old checkpoint
    whose sessions carry `### ACTIVATION` subsections is still a valid one.

    The order of the checks is the order of their usefulness: a misspelled
    header first (otherwise it surfaces as "session without its activation",
    which names the symptom), then the old in-block form (the likeliest mistake,
    a template from before the change), then repetition, then adjacency, then
    an activation block whose prompt is empty.
    """
    blocks = parse_blocks(segment)
    for block in blocks:
        header = header_of(block)
        if header[:10].upper() == "ACTIVATION" and activation_num(block) is None:
            die("%s: malformed header %r -- an activation block is spelled "
                "`## ACTIVATION S<NNN>`, with the id of the session it belongs to"
                % (block_path, "## " + header[:60]))
    for block in blocks:
        number = session_num(block)
        if number is None:
            continue
        if any(LEGACY_ACTIVATION_RE.match(line)
               for _index, line in unfenced(block.lines[1:])):
            die("%s: SESSION S%03d carries a `### ACTIVATION` subsection -- the "
                "activation prompt is its own block now: move it below the "
                "session as `## ACTIVATION S%03d`" % (block_path, number, number))
    labels = block_labels(segment)
    repeated = sorted({label for label in labels if labels.count(label) > 1})
    if repeated:
        die("%s: the segment carries %s more than once -- every block id is "
            "unique" % (block_path, " and ".join(repeated)))
    for index, block in enumerate(blocks):
        number = session_num(block)
        if number is not None:
            following = blocks[index + 1] if index + 1 < len(blocks) else None
            if following is None or activation_num(following) != number:
                die("%s: SESSION S%03d is not immediately followed by its "
                    "`## ACTIVATION S%03d` block (next is %s) -- every session "
                    "carries exactly one activation prompt, as its own block "
                    "right below it"
                    % (block_path, number, number,
                       "the end of the segment" if following is None
                       else repr("## " + header_of(following)[:60])))
        number = activation_num(block)
        if number is not None:
            preceding = blocks[index - 1] if index > 0 else None
            if preceding is None or session_num(preceding) != number:
                die("%s: ACTIVATION S%03d is not immediately preceded by "
                    "SESSION S%03d -- an activation block belongs to the "
                    "session right above it" % (block_path, number, number))
    # Last, because it judges a block the rules above have already placed. The
    # readers refuse an empty prompt, so writing one would be accepting on the
    # write side what the read side rejects -- found only at resume time, by
    # whoever needed the prompt. Judged through `dequote`, the same function
    # the readers use, so a `>`-only body counts as empty on both sides.
    for block in blocks:
        number = activation_num(block)
        if number is not None and not dequote(block.lines[1:]):
            die("%s: ACTIVATION S%03d has an empty prompt -- write the "
                "activation text under the header, as a `>` blockquote"
                % (block_path, number))


def cmd_prepend(path, block_path, overwrite=False):
    """Insert a segment at the top and regenerate the TOC in ONE os.replace.

    The whole point: there is no reachable state in which the new block is in
    the file but the table still describes the old one. Either both landed or
    neither did.
    """
    if os.path.exists(path):
        require_file(path)  # a directory here refuses like everywhere else
    segment = read_segment(block_path, path)

    if overwrite or not os.path.exists(path):
        # A missing file needs no --overwrite: there is nothing to lose. The
        # flag is only for DISCARDING a file that exists.
        h1 = H1_TITLE
        if os.path.isfile(path):
            first = read_lines(path)[:1]
            if first and first[0].startswith("# "):
                h1 = first[0]  # a hand-picked title outlives its content
        body = [h1, ""] + segment
    else:
        body = strip_toc(read_lines(path))
        # Content-based, so it cannot be forgotten. Three real failures share
        # this one gate: a stale segment left at the default path and inserted
        # a second time, an id guessed by hand instead of taken from
        # `next-number`, and a `prepend` retried after an interrupted session.
        # Checked against the WHOLE file, not the top block: a clash further
        # down is the same corruption and harder to notice.
        already = set(block_labels(body))
        clash = [label for label in block_labels(segment) if label in already]
        if clash:
            die("%s already in %s -- refusing to prepend a duplicate block "
                "(a session id comes from `next-number`, and the MISSION tail "
                "is written once)" % (" and ".join(clash), path))
        blocks = parse_blocks(body)
        # The insertion point is the current top block's own start line, never
        # "just below the H1": with a region sitting there, that would land
        # inside the generated table.
        index = blocks[0].start - 1 if blocks else len(body)
        segment = trim_trailing_blanks(segment)
        if index < len(body):
            body = body[:index] + segment + [""] + body[index:]
        else:
            # No blocks at all -- the segment brings the first one, and goes
            # after whatever preamble the file already has.
            body = trim_trailing_blanks(body) + [""] + segment

    lines = with_toc(body, path)
    write_atomic(path, lines)
    # The table that was just written, echoed verbatim: identical to what a
    # `toc` run would print, so the caller needs no second command to learn
    # where its block landed.
    for line in toc_region_of(lines):
        print(line)
    return 0


def cmd_session(path, raw_id):
    """Print one block verbatim by any label the TOC shows: S042 (and its
    other spellings) for a session, A042 for that session's ACTIVATION block --
    so every row of the table can be read back by the label it carries."""
    require_file(path)
    parsed = parse_session_id(raw_id, allow_activation=True)
    if parsed is None:
        sys.stderr.write("checkpoint: session %s not found\n" % raw_id)
        return 2
    kind, wanted = parsed
    id_of = session_num if kind == "S" else activation_num
    for block in load_blocks(path):
        if id_of(block) == wanted:
            print(text_of(block))
            return 0
    sys.stderr.write("checkpoint: session %s not found\n" % raw_id)
    return 2


def build_parser():
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--file", default=DEFAULT_FILE, help="checkpoint markdown file (default: %(default)s)"
    )

    parser = argparse.ArgumentParser(
        description="Read sections out of a checkpoint markdown file by header "
        "structure, write a new block to the top of it, and maintain its "
        "line-range table of contents."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("next-number", parents=[parent], help="print the next session id (S%%03d)")
    sub.add_parser("latest", parents=[parent], help="print the newest SESSION block")
    sub.add_parser("mission", parents=[parent], help="print the MISSION block")
    sub.add_parser(
        "nexts",
        parents=[parent],
        help="print MISSION, the newest SESSION and its ACTIVATION block, in that "
        "order -- everything a resuming session needs, in one call",
    )
    act = sub.add_parser(
        "activate",
        parents=[parent],
        help="print a session's activation prompt, paste-ready (quote markers "
        "stripped); default: the newest session",
    )
    act.add_argument(
        "id", nargs="?", default=None, help="session id, e.g. S042, s042, 42, 042"
    )
    toc = sub.add_parser(
        "toc", parents=[parent], help="print the table of contents (id | when | branch | lines)"
    )
    toc.add_argument(
        "--write",
        action="store_true",
        help="rewrite the file's TOC region in place instead of printing it",
    )
    sub.add_parser("list", parents=[parent], help="alias for `toc`")
    pre = sub.add_parser(
        "prepend",
        parents=[parent],
        help="insert a block segment at the top and regenerate the TOC in ONE "
        "atomic write, then print the fresh table",
    )
    pre.add_argument(
        "--block-file",
        default=DEFAULT_BLOCK_FILE,
        metavar="PATH",
        help="file holding the `## ` block(s) to insert; write it first, then "
        "call this -- the text never goes through the command line "
        "(default: %(default)s)",
    )
    pre.add_argument(
        "--overwrite",
        action="store_true",
        help="replace the whole file with the segment instead of prepending "
        "(only needed for a file that EXISTS; a missing one is just created)",
    )
    sp = sub.add_parser(
        "session",
        parents=[parent],
        help="print one block by its TOC label: a SESSION (S042) or its ACTIVATION (A042)",
    )
    sp.add_argument("id", help="block id, e.g. S042, s042, 42, 042, or A042 / a042")
    return parser


def main():
    args = build_parser().parse_args()
    path = args.file
    if args.command == "next-number":
        code = cmd_next_number(path)
    elif args.command == "latest":
        code = cmd_latest(path)
    elif args.command == "mission":
        code = cmd_mission(path)
    elif args.command == "nexts":
        code = cmd_nexts(path)
    elif args.command == "activate":
        code = cmd_activate(path, args.id)
    elif args.command in ("toc", "list"):
        code = cmd_toc(path, getattr(args, "write", False))
    elif args.command == "prepend":
        code = cmd_prepend(path, args.block_file, args.overwrite)
    elif args.command == "session":
        code = cmd_session(path, args.id)
    else:  # pragma: no cover - argparse enforces a valid command
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
